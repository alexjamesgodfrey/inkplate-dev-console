from __future__ import annotations

import glob
import json
import os
import struct
import sys
import time
import zlib
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn

DEFAULT_BAUD = 115200
DEFAULT_FRAME_PATH = Path("inkplate-frame.png")
HEX_CHARS = frozenset("0123456789abcdefABCDEF")
BIT_REVERSE = bytes(int(f"{value:08b}"[::-1], 2) for value in range(256))
PORT_GLOBS = (
    "/dev/cu.usbserial*",
    "/dev/cu.wchusbserial*",
    "/dev/cu.SLAB_USBtoUART*",
    "/dev/cu.usbmodem*",
    "/dev/ttyUSB*",
    "/dev/ttyACM*",
)


class InkplateDevConsoleError(RuntimeError):
    """Base class for expected operational failures."""


class PortDetectionError(InkplateDevConsoleError):
    """Raised when no serial port can be selected."""


class SerialOpenError(InkplateDevConsoleError):
    """Raised when pyserial cannot open the selected port."""


class SerialConnectionError(InkplateDevConsoleError):
    """Raised when an open serial connection fails during I/O."""


class DeviceProtocolError(InkplateDevConsoleError):
    """Raised when firmware returns malformed or unsupported protocol data."""


class DeviceTimeoutError(TimeoutError, InkplateDevConsoleError):
    """Raised when dev firmware does not answer before the deadline."""


@dataclass(frozen=True)
class PortCandidate:
    path: str
    source: str

    def as_json(self) -> dict[str, Any]:
        return {
            "exists": Path(self.path).exists(),
            "path": self.path,
            "readable": os.access(self.path, os.R_OK),
            "source": self.source,
            "writable": os.access(self.path, os.W_OK),
        }


@dataclass(frozen=True)
class FrameCapture:
    width: int
    height: int
    row_bytes: int
    byte_count: int
    frame_format: str
    path: Path

    @classmethod
    def from_meta(cls, meta: dict[str, Any], path: Path) -> FrameCapture:
        try:
            width = int(meta["width"])
            return cls(
                width=width,
                height=int(meta["height"]),
                row_bytes=int(meta.get("rowBytes", (width + 7) // 8)),
                byte_count=int(meta["bytes"]),
                frame_format=str(meta.get("format", "1bpp-lsb-black1")),
                path=path,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise DeviceProtocolError(
                f"Invalid DEV_FRAME_BEGIN metadata: {meta}"
            ) from exc

    def as_json(self) -> dict[str, Any]:
        return {
            "width": self.width,
            "height": self.height,
            "rowBytes": self.row_bytes,
            "bytes": self.byte_count,
            "format": self.frame_format,
            "path": str(self.path),
        }


def list_port_candidates() -> list[PortCandidate]:
    candidates: list[PortCandidate] = []
    seen: set[str] = set()

    for variable in ("INKPLATE_PORT", "UPLOAD_PORT"):
        path = os.environ.get(variable)
        if path and path not in seen:
            candidates.append(PortCandidate(path=path, source=variable))
            seen.add(path)

    for pattern in PORT_GLOBS:
        for path in sorted(glob.glob(pattern)):
            if path not in seen:
                candidates.append(PortCandidate(path=path, source=f"auto:{pattern}"))
                seen.add(path)

    return candidates


def port_inventory(explicit_port: str | None = None) -> dict[str, Any]:
    candidates = list_port_candidates()
    selected: PortCandidate | None
    if explicit_port:
        selected = PortCandidate(path=explicit_port, source="--port")
    else:
        selected = candidates[0] if candidates else None

    return {
        "candidates": [candidate.as_json() for candidate in candidates],
        "selected": selected.as_json() if selected else None,
        "selectionOrder": ["--port", "INKPLATE_PORT", "UPLOAD_PORT", *PORT_GLOBS],
    }


def detect_port() -> str:
    candidates = list_port_candidates()
    if candidates:
        return candidates[0].path

    raise PortDetectionError(
        "No Inkplate USB serial port found. "
        "Next: connect the device or run `inkplate-dev ports --json`; "
        "override with `INKPLATE_PORT=/dev/... inkplate-dev state`."
    )


def open_serial(port: str, baud: int):
    try:
        import serial  # type: ignore[import-untyped]
    except ImportError as exc:
        raise SerialOpenError(
            "pyserial is not installed. Next: `python3 -m pip install pyserial`."
        ) from exc

    try:
        ser = serial.Serial()
        ser.port = port
        ser.baudrate = baud
        ser.timeout = 0.25
        ser.write_timeout = 2
        ser.dtr = True
        ser.rts = False
        ser.open()  # ubs:ignore — pyserial lifecycle is closed by InkplateDevConsoleClient.close().
        ser.dtr = True
        ser.rts = False
        time.sleep(0.15)
        ser.reset_input_buffer()
        return ser
    except (OSError, serial.SerialException) as exc:
        raise SerialOpenError(
            f"Could not open Inkplate serial port {port!r}: {exc}. "
            "Next: close other serial monitors, then run "
            f"`inkplate-dev --port {port} doctor --connect --json`."
        ) from exc


def write_command(ser: Any, command: str) -> None:
    command = command.strip()
    if not command.startswith("dev:"):
        command = f"dev:{command}"
    try:
        ser.write(f"{command}\n".encode())
        ser.flush()
    except Exception as exc:  # noqa: BLE001 - pyserial is imported lazily; non-serial errors are re-raised.
        raise_serial_connection_error(exc, f"sending {command!r}")


def is_serial_error(exc: Exception) -> bool:
    if isinstance(exc, OSError):
        return True
    try:
        import serial  # type: ignore[import-untyped]
    except ImportError:
        return False
    return isinstance(exc, serial.SerialException)


def raise_serial_connection_error(exc: Exception, operation: str) -> NoReturn:
    if not is_serial_error(exc):
        raise exc
    raise SerialConnectionError(
        f"Serial connection failed while {operation}: {exc}. "
        "Next: reconnect the device, close other serial monitors, then run "
        "`inkplate-dev doctor --connect --json`."
    ) from exc


def read_serial_line(ser: Any) -> bytes:
    try:
        return ser.readline()
    except Exception as exc:  # noqa: BLE001 - pyserial is imported lazily; non-serial errors are re-raised.
        raise_serial_connection_error(exc, "reading from the device")


def read_prefixed_line(
    ser: Any,
    prefixes: tuple[str, ...],
    timeout: float,
    retry_command: str | None = None,
    echo_unmatched: bool = True,
) -> tuple[str, str]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        raw = read_serial_line(ser)
        if not raw:
            continue

        line = raw.decode("utf-8", errors="replace").strip()
        for prefix in prefixes:
            if line.startswith(prefix):
                return prefix, line[len(prefix) :].strip()

        if line and echo_unmatched:
            print(line, file=sys.stderr)

        if retry_command and (
            "[INIT] Setup complete" in line or "[INIT] Resumed game" in line
        ):
            write_command(ser, retry_command)
            retry_command = None
            deadline = max(deadline, time.monotonic() + timeout)

    expected = ", ".join(prefixes)
    raise DeviceTimeoutError(
        f"Timed out waiting for {expected}. The connected firmware may be asleep "
        "or may be a production build without the dev console. "
        "Next: flash a build with `-DINKPLATE_DEV_CONSOLE=1`, then run "
        "`inkplate-dev doctor --connect --json`."
    )


def parse_json_object(payload: str, label: str) -> dict[str, Any]:
    try:
        value = json.loads(payload)  # ubs:ignore — guarded by JSONDecodeError below.
    except json.JSONDecodeError as exc:
        raise DeviceProtocolError(f"Invalid {label} JSON: {payload}") from exc
    if not isinstance(value, dict):
        raise DeviceProtocolError(
            f"Invalid {label} JSON: expected object, got {type(value).__name__}"
        )
    return value


def read_json_prefix(
    ser: Any,
    prefix: str,
    timeout: float,
    retry_command: str | None = None,
    echo_unmatched: bool = True,
) -> dict[str, Any]:
    _, payload = read_prefixed_line(
        ser, (prefix,), timeout, retry_command, echo_unmatched
    )
    return parse_json_object(payload, prefix)


def request_state(
    ser: Any, timeout: float = 30.0, echo_unmatched: bool = True
) -> dict[str, Any]:
    write_command(ser, "state")
    return read_json_prefix(ser, "DEV_STATE", timeout, "state", echo_unmatched)


def request_ack(
    ser: Any, command: str, timeout: float = 30.0, echo_unmatched: bool = True
) -> dict[str, Any]:
    write_command(ser, command)
    prefix, payload = read_prefixed_line(
        ser, ("DEV_ACK", "DEV_HELP"), timeout, command, echo_unmatched
    )
    if prefix == "DEV_HELP":
        return {"ok": True, "help": payload}
    return parse_json_object(payload, prefix)


def normalize_frame_bits(raw_frame: bytes, frame_format: str) -> bytes:
    if frame_format == "1bpp-lsb-black1":
        return raw_frame.translate(BIT_REVERSE)
    if frame_format == "1bpp-msb-black1":
        return raw_frame
    raise DeviceProtocolError(f"Unsupported frame format: {frame_format}")


def write_pbm(path: Path, width: int, height: int, packed_black1_msb: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(f"P4\n{width} {height}\n".encode("ascii") + packed_black1_msb)


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(tag)
    crc = zlib.crc32(data, crc)
    return (
        struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc & 0xFFFFFFFF)
    )


def write_png(path: Path, width: int, height: int, packed_black1_msb: bytes) -> None:
    row_bytes = (width + 7) // 8
    expected = row_bytes * height
    if len(packed_black1_msb) != expected:
        raise DeviceProtocolError(
            f"PNG frame length mismatch: got {len(packed_black1_msb)} bytes, "
            f"expected {expected}"
        )

    rows = bytearray()
    for row in range(height):
        start = row * row_bytes
        rows.append(0)  # PNG filter type 0: none.
        rows.extend(packed_black1_msb[start : start + row_bytes])

    ihdr = struct.pack(">IIBBBBB", width, height, 1, 3, 0, 0, 0)
    # Palette index 0 = white, index 1 = black. That preserves PBM-style black1 bits.
    plte = bytes((255, 255, 255, 0, 0, 0))
    payload = (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"PLTE", plte)
        + _png_chunk(b"IDAT", zlib.compress(bytes(rows)))
        + _png_chunk(b"IEND", b"")
    )

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def write_frame_output(
    path: Path, meta: dict[str, Any], raw_frame: bytes
) -> FrameCapture:
    capture = FrameCapture.from_meta(meta, path)
    if len(raw_frame) != capture.byte_count:
        raise DeviceProtocolError(
            f"Frame length mismatch: got {len(raw_frame)} bytes, "
            f"expected {capture.byte_count}"
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    suffix = path.suffix.lower()
    if suffix == ".raw":
        path.write_bytes(raw_frame)
    else:
        packed = normalize_frame_bits(raw_frame, capture.frame_format)
        if suffix == ".pbm":
            write_pbm(path, capture.width, capture.height, packed)
        elif suffix == ".png":
            write_png(path, capture.width, capture.height, packed)
        else:
            raise ValueError(
                "Frame output extension must be .png, .pbm, or .raw. "
                "Next: use `--out /tmp/inkplate.png`."
            )

    return capture


def capture_frame(
    ser: Any,
    output: Path = DEFAULT_FRAME_PATH,
    timeout: float = 40.0,
    echo_unmatched: bool = True,
) -> FrameCapture:
    deadline = time.monotonic() + timeout
    write_command(ser, "frame")
    _, payload = read_prefixed_line(
        ser, ("DEV_FRAME_BEGIN",), timeout, "frame", echo_unmatched
    )
    meta = parse_json_object(payload, "DEV_FRAME_BEGIN")
    chunks: list[str] = []

    while time.monotonic() < deadline:
        raw = read_serial_line(ser)
        if not raw:
            continue
        line = raw.decode("utf-8", errors="replace").strip()
        if line == "DEV_FRAME_END":
            break
        if line.startswith("DEV_FRAME "):
            payload = "".join(ch for ch in line[len("DEV_FRAME ") :] if ch in HEX_CHARS)
            if payload:
                chunks.append(payload)
            continue
        if line and echo_unmatched:
            print(line, file=sys.stderr)
    else:
        raise DeviceTimeoutError(
            "Timed out waiting for DEV_FRAME_END. "
            "Next: rerun with a larger `--timeout`, for example "
            "`inkplate-dev frame --timeout 90 --out /tmp/inkplate.png`."
        )

    try:
        raw_frame = bytes.fromhex("".join(chunks))
    except ValueError as exc:
        raise DeviceProtocolError(
            "DEV_FRAME contained invalid hexadecimal data"
        ) from exc
    return write_frame_output(output, meta, raw_frame)


class InkplateDevConsoleClient:
    def __init__(
        self,
        port: str | None = None,
        baud: int = DEFAULT_BAUD,
        timeout: float = 30.0,
        echo_unmatched: bool = True,
    ) -> None:
        self.port = port or detect_port()
        self.baud = baud
        self.timeout = timeout
        self.echo_unmatched = echo_unmatched
        self._serial = None

    def __enter__(self) -> InkplateDevConsoleClient:  # noqa: PYI034 - Python 3.10 lacks typing.Self.
        self.open()  # ubs:ignore — opens serial, not a filesystem handle; __exit__ closes it.
        return self

    def __exit__(self, exc_type: object, *_: object) -> None:
        try:
            self.close()
        except SerialConnectionError:
            if exc_type is None:
                raise

    def open(self) -> None:  # ubs:ignore — serial lifecycle, not filesystem.
        if self._serial is None:
            self._serial = open_serial(self.port, self.baud)

    def close(self) -> None:
        if self._serial is not None:
            serial_handle = self._serial
            self._serial = None
            try:
                serial_handle.close()
            except Exception as exc:  # noqa: BLE001 - pyserial is imported lazily; non-serial errors are re-raised.
                raise_serial_connection_error(exc, "closing the device")

    @property
    def serial(self):
        if self._serial is None:
            raise RuntimeError("Client is not open")
        return self._serial

    def state(self) -> dict[str, Any]:
        return request_state(self.serial, self.timeout, self.echo_unmatched)

    def command(self, command: str) -> dict[str, Any]:
        return request_ack(self.serial, command, self.timeout, self.echo_unmatched)

    def frame(
        self, output: Path = DEFAULT_FRAME_PATH, timeout: float | None = None
    ) -> FrameCapture:
        return capture_frame(
            self.serial,
            output,
            self.timeout if timeout is None else timeout,
            self.echo_unmatched,
        )

    def tap(self, x: int, y: int) -> dict[str, Any]:
        return self.command(f"tap {x} {y}")

    def square(self, square: str) -> dict[str, Any]:
        return self.command(f"square {square}")

    def watch(
        self, output: Path, interval: float = 1.0, count: int | None = None
    ) -> Iterable[FrameCapture]:
        index = 0
        while count is None or index < count:
            if "{}" in str(output):
                path = Path(str(output).format(index))
            else:
                path = output
            yield self.frame(path)
            index += 1
            if count is None or index < count:
                time.sleep(interval)
