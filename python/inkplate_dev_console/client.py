from __future__ import annotations

import glob
import json
import os
import re
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
MAX_FRAME_DIMENSION = 8192
MAX_FRAME_BYTES = 64 * 1024 * 1024
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


class OutputPathError(InkplateDevConsoleError):
    """Raised when a requested output artifact cannot be written."""


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
    encoding: str = "hex"

    @classmethod
    def from_meta(cls, meta: dict[str, Any], path: Path) -> FrameCapture:
        try:
            width = protocol_int(meta["width"], "width")
            height = protocol_int(meta["height"], "height")
            row_bytes = protocol_int(meta.get("rowBytes", (width + 7) // 8), "rowBytes")
            byte_count = protocol_int(meta["bytes"], "bytes")
            frame_format = str(meta.get("format", "1bpp-lsb-black1"))
            encoding = str(meta.get("encoding", "hex"))
        except (KeyError, TypeError, ValueError) as exc:
            raise DeviceProtocolError(
                f"Invalid DEV_FRAME_BEGIN metadata: {meta}"
            ) from exc
        expected_row_bytes = (width + 7) // 8
        expected_byte_count = row_bytes * height
        if (
            not 0 < width <= MAX_FRAME_DIMENSION
            or not 0 < height <= MAX_FRAME_DIMENSION
            or row_bytes < expected_row_bytes
            or byte_count != expected_byte_count
            or byte_count > MAX_FRAME_BYTES
            or frame_format not in {"1bpp-lsb-black1", "1bpp-msb-black1"}
            or encoding != "hex"
        ):
            raise DeviceProtocolError(
                "Invalid DEV_FRAME_BEGIN geometry, encoding, or format: "
                f"width={width}, height={height}, rowBytes={row_bytes}, "
                f"bytes={byte_count}, encoding={encoding!r}, format={frame_format!r}"
            )
        return cls(
            width=width,
            height=height,
            row_bytes=row_bytes,
            byte_count=byte_count,
            frame_format=frame_format,
            path=path,
            encoding=encoding,
        )

    def as_json(self) -> dict[str, Any]:
        return {
            "width": self.width,
            "height": self.height,
            "rowBytes": self.row_bytes,
            "bytes": self.byte_count,
            "format": self.frame_format,
            "path": str(self.path),
            "encoding": self.encoding,
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


def protocol_int(value: object, label: str) -> int:
    if isinstance(value, bool):
        raise TypeError(f"{label} must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and re.fullmatch(r"-?[0-9]+", value):
        return int(value)
    raise TypeError(f"{label} must be an integer")


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

    ser = None
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
        if ser is not None and getattr(ser, "is_open", False):
            try:
                ser.close()
            except (OSError, serial.SerialException):
                pass
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
) -> tuple[str, str, float]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        raw = read_serial_line(ser)
        if not raw:
            continue

        line = raw.decode("utf-8", errors="replace").strip()
        for prefix in prefixes:
            if line.startswith(prefix):
                return prefix, line[len(prefix) :].strip(), deadline

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
    def reject_nonfinite(value: str) -> NoReturn:
        raise ValueError(f"non-finite number {value}")

    try:
        value = json.loads(  # ubs:ignore — guarded by JSONDecodeError/ValueError below.
            payload, parse_constant=reject_nonfinite
        )
    except (json.JSONDecodeError, ValueError) as exc:
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
    _, payload, _ = read_prefixed_line(
        ser, (prefix,), timeout, retry_command, echo_unmatched
    )
    return parse_json_object(payload, prefix)


def request_state(
    ser: Any, timeout: float = 30.0, echo_unmatched: bool = True
) -> dict[str, Any]:
    write_command(ser, "state")
    prefix, payload, _ = read_prefixed_line(
        ser, ("DEV_STATE", "DEV_ACK"), timeout, "state", echo_unmatched
    )
    if prefix == "DEV_ACK":
        reject_unexpected_ack(payload, "state")
    return parse_json_object(payload, prefix)


def parse_correlated_ack(payload: str, expected_command: str) -> dict[str, Any]:
    ack = parse_json_object(payload, "DEV_ACK")
    if not isinstance(ack.get("ok"), bool):
        raise DeviceProtocolError(
            "DEV_ACK must contain boolean field `ok`. "
            "Next: verify the firmware and CLI protocol versions match."
        )
    actual_command = ack.get("command")
    message = ack.get("message")
    message_parts = message.split(maxsplit=1) if isinstance(message, str) else []
    legacy_unknown_rejection = (
        actual_command == "unknown"
        and not ack["ok"]
        and bool(message_parts)
        and message_parts[0].lower() == expected_command
    )
    if (
        not isinstance(actual_command, str)
        or actual_command != expected_command
        and not legacy_unknown_rejection
    ):
        raise DeviceProtocolError(
            f"DEV_ACK command mismatch: expected {expected_command!r}, "
            f"got {actual_command!r}. Next: flush stale serial responses and retry."
        )
    return ack


def reject_unexpected_ack(payload: str, expected_command: str) -> NoReturn:
    ack = parse_correlated_ack(payload, expected_command)
    message = ack.get("message")
    if not ack["ok"]:
        suffix = f": {message}" if isinstance(message, str) and message else ""
        raise DeviceProtocolError(
            f"Device rejected {expected_command!r}{suffix}. "
            "Next: verify the corresponding firmware callback is configured."
        )
    raise DeviceProtocolError(
        f"Unexpected positive DEV_ACK for {expected_command!r}; "
        "expected command data. Next: flush stale serial responses and retry."
    )


def request_ack(
    ser: Any, command: str, timeout: float = 30.0, echo_unmatched: bool = True
) -> dict[str, Any]:
    normalized_command = command.strip()
    if normalized_command.startswith("dev:"):
        normalized_command = normalized_command[4:].lstrip()
    if not normalized_command:
        raise ValueError("Device command cannot be empty")
    write_command(ser, command)
    expected_command = normalized_command.split(maxsplit=1)[0].lower()
    if expected_command == "?":
        expected_command = "help"
    prefixes = ("DEV_HELP",) if expected_command == "help" else ("DEV_ACK",)
    prefix, payload, _ = read_prefixed_line(
        ser, prefixes, timeout, command, echo_unmatched
    )
    if prefix == "DEV_HELP":
        return {"ok": True, "help": payload}
    return parse_correlated_ack(payload, expected_command)


def normalize_frame_bits(raw_frame: bytes, frame_format: str) -> bytes:
    if frame_format == "1bpp-lsb-black1":
        return raw_frame.translate(BIT_REVERSE)
    if frame_format == "1bpp-msb-black1":
        return raw_frame
    raise DeviceProtocolError(f"Unsupported frame format: {frame_format}")


def compact_frame_rows(frame: bytes, width: int, height: int, row_bytes: int) -> bytes:
    packed_row_bytes = (width + 7) // 8
    if row_bytes == packed_row_bytes:
        return frame
    return b"".join(
        frame[row * row_bytes : row * row_bytes + packed_row_bytes]
        for row in range(height)
    )


def write_pbm(path: Path, width: int, height: int, packed_black1_msb: bytes) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"P4\n{width} {height}\n".encode("ascii") + packed_black1_msb)
    except OSError as exc:
        raise_output_path_error(path, exc)


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

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    except OSError as exc:
        raise_output_path_error(path, exc)


def raise_output_path_error(path: Path, exc: OSError) -> NoReturn:
    raise OutputPathError(
        f"Could not write frame output {str(path)!r}: {exc}. "
        "Next: choose a writable path with `--out /tmp/inkplate.png`."
    ) from exc


def write_frame_output(
    path: Path, meta: dict[str, Any], raw_frame: bytes
) -> FrameCapture:
    capture = FrameCapture.from_meta(meta, path)
    if len(raw_frame) != capture.byte_count:
        raise DeviceProtocolError(
            f"Frame length mismatch: got {len(raw_frame)} bytes, "
            f"expected {capture.byte_count}"
        )

    suffix = path.suffix.lower()
    if suffix == ".raw":
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw_frame)
        except OSError as exc:
            raise_output_path_error(path, exc)
    else:
        packed = normalize_frame_bits(raw_frame, capture.frame_format)
        packed = compact_frame_rows(
            packed, capture.width, capture.height, capture.row_bytes
        )
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
    prefix, payload, deadline = read_prefixed_line(
        ser, ("DEV_FRAME_BEGIN", "DEV_ACK"), timeout, "frame", echo_unmatched
    )
    if prefix == "DEV_ACK":
        reject_unexpected_ack(payload, "frame")
    meta = parse_json_object(payload, "DEV_FRAME_BEGIN")
    expected_capture = FrameCapture.from_meta(meta, output)
    chunks: list[str] = []
    hex_char_count = 0

    while time.monotonic() < deadline:
        raw = read_serial_line(ser)
        if not raw:
            continue
        line = raw.decode("utf-8", errors="replace").strip()
        if line == "DEV_FRAME_END":
            break
        if line.startswith("DEV_FRAME "):
            payload = "".join(line[len("DEV_FRAME ") :].split())
            if (
                not payload
                or len(payload) % 2 != 0
                or any(ch not in HEX_CHARS for ch in payload)
            ):
                raise DeviceProtocolError(
                    "DEV_FRAME contained invalid hexadecimal data"
                )
            hex_char_count += len(payload)
            if hex_char_count > expected_capture.byte_count * 2:
                raise DeviceProtocolError(
                    "DEV_FRAME payload exceeds the byte count declared by "
                    "DEV_FRAME_BEGIN"
                )
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
