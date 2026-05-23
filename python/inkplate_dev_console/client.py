from __future__ import annotations

from dataclasses import dataclass
import glob
import json
import os
from pathlib import Path
import struct
import sys
import time
import zlib
from typing import Any, Iterable


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


@dataclass(frozen=True)
class FrameCapture:
    width: int
    height: int
    row_bytes: int
    byte_count: int
    frame_format: str
    path: Path

    @classmethod
    def from_meta(cls, meta: dict[str, Any], path: Path) -> "FrameCapture":
        return cls(
            width=int(meta["width"]),
            height=int(meta["height"]),
            row_bytes=int(meta.get("rowBytes", (int(meta["width"]) + 7) // 8)),
            byte_count=int(meta["bytes"]),
            frame_format=str(meta.get("format", "1bpp-lsb-black1")),
            path=path,
        )

    def as_json(self) -> dict[str, Any]:
        return {
            "width": self.width,
            "height": self.height,
            "rowBytes": self.row_bytes,
            "bytes": self.byte_count,
            "format": self.frame_format,
            "path": str(self.path),
        }


def detect_port() -> str:
    override = os.environ.get("UPLOAD_PORT") or os.environ.get("INKPLATE_PORT")
    if override:
        return override

    for pattern in PORT_GLOBS:
        matches = sorted(glob.glob(pattern))
        if matches:
            return matches[0]

    raise RuntimeError("No Inkplate USB serial port found. Set INKPLATE_PORT=/dev/... to override.")


def open_serial(port: str, baud: int):
    import serial

    ser = serial.Serial()
    ser.port = port
    ser.baudrate = baud
    ser.timeout = 0.25
    ser.write_timeout = 2
    ser.dtr = True
    ser.rts = False
    ser.open()
    ser.dtr = True
    ser.rts = False
    time.sleep(0.15)
    ser.reset_input_buffer()
    return ser


def write_command(ser: Any, command: str) -> None:
    command = command.strip()
    if not command.startswith("dev:"):
        command = f"dev:{command}"
    ser.write(f"{command}\n".encode("utf-8"))
    ser.flush()


def read_prefixed_line(
    ser: Any,
    prefixes: tuple[str, ...],
    timeout: float,
    retry_command: str | None = None,
    echo_unmatched: bool = True,
) -> tuple[str, str]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        raw = ser.readline()
        if not raw:
            continue

        line = raw.decode("utf-8", errors="replace").strip()
        for prefix in prefixes:
            if line.startswith(prefix):
                return prefix, line[len(prefix) :].strip()

        if line and echo_unmatched:
            print(line, file=sys.stderr)

        if retry_command and ("[INIT] Setup complete" in line or "[INIT] Resumed game" in line):
            write_command(ser, retry_command)
            retry_command = None
            deadline = max(deadline, time.monotonic() + timeout)

    raise TimeoutError(f"Timed out waiting for one of: {', '.join(prefixes)}")


def parse_json_object(payload: str, label: str) -> dict[str, Any]:
    try:
        value = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid {label} JSON: {payload}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"Invalid {label} JSON: expected object, got {type(value).__name__}")
    return value


def read_json_prefix(
    ser: Any,
    prefix: str,
    timeout: float,
    retry_command: str | None = None,
    echo_unmatched: bool = True,
) -> dict[str, Any]:
    _, payload = read_prefixed_line(ser, (prefix,), timeout, retry_command, echo_unmatched)
    return parse_json_object(payload, prefix)


def request_state(ser: Any, timeout: float = 30.0, echo_unmatched: bool = True) -> dict[str, Any]:
    write_command(ser, "state")
    return read_json_prefix(ser, "DEV_STATE", timeout, "state", echo_unmatched)


def request_ack(ser: Any, command: str, timeout: float = 30.0, echo_unmatched: bool = True) -> dict[str, Any]:
    write_command(ser, command)
    prefix, payload = read_prefixed_line(ser, ("DEV_ACK", "DEV_HELP"), timeout, command, echo_unmatched)
    if prefix == "DEV_HELP":
        return {"ok": True, "help": payload}
    return parse_json_object(payload, prefix)


def normalize_frame_bits(raw_frame: bytes, frame_format: str) -> bytes:
    if frame_format == "1bpp-lsb-black1":
        return raw_frame.translate(BIT_REVERSE)
    if frame_format == "1bpp-msb-black1":
        return raw_frame
    raise RuntimeError(f"Unsupported frame format: {frame_format}")


def write_pbm(path: Path, width: int, height: int, packed_black1_msb: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(f"P4\n{width} {height}\n".encode("ascii") + packed_black1_msb)


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(tag)
    crc = zlib.crc32(data, crc)
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc & 0xFFFFFFFF)


def write_png(path: Path, width: int, height: int, packed_black1_msb: bytes) -> None:
    row_bytes = (width + 7) // 8
    expected = row_bytes * height
    if len(packed_black1_msb) != expected:
        raise RuntimeError(f"PNG frame length mismatch: got {len(packed_black1_msb)} bytes, expected {expected}")

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


def write_frame_output(path: Path, meta: dict[str, Any], raw_frame: bytes) -> FrameCapture:
    capture = FrameCapture.from_meta(meta, path)
    if len(raw_frame) != capture.byte_count:
        raise RuntimeError(f"Frame length mismatch: got {len(raw_frame)} bytes, expected {capture.byte_count}")

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
            raise RuntimeError("Frame output extension must be .png, .pbm, or .raw")

    return capture


def capture_frame(
    ser: Any,
    output: Path = DEFAULT_FRAME_PATH,
    timeout: float = 40.0,
    echo_unmatched: bool = True,
) -> FrameCapture:
    write_command(ser, "frame")
    _, payload = read_prefixed_line(ser, ("DEV_FRAME_BEGIN",), timeout, "frame", echo_unmatched)
    meta = parse_json_object(payload, "DEV_FRAME_BEGIN")
    chunks: list[str] = []

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        raw = ser.readline()
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
        raise TimeoutError("Timed out waiting for DEV_FRAME_END")

    return write_frame_output(output, meta, bytes.fromhex("".join(chunks)))


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

    def __enter__(self) -> "InkplateDevConsoleClient":
        self.open()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def open(self) -> None:
        if self._serial is None:
            self._serial = open_serial(self.port, self.baud)

    def close(self) -> None:
        if self._serial is not None:
            self._serial.close()
            self._serial = None

    @property
    def serial(self):
        if self._serial is None:
            raise RuntimeError("Client is not open")
        return self._serial

    def state(self) -> dict[str, Any]:
        return request_state(self.serial, self.timeout, self.echo_unmatched)

    def command(self, command: str) -> dict[str, Any]:
        return request_ack(self.serial, command, self.timeout, self.echo_unmatched)

    def frame(self, output: Path = DEFAULT_FRAME_PATH, timeout: float | None = None) -> FrameCapture:
        return capture_frame(self.serial, output, max(timeout or self.timeout, 40.0), self.echo_unmatched)

    def tap(self, x: int, y: int) -> dict[str, Any]:
        return self.command(f"tap {x} {y}")

    def square(self, square: str) -> dict[str, Any]:
        return self.command(f"square {square}")

    def watch(self, output: Path, interval: float = 1.0, count: int | None = None) -> Iterable[FrameCapture]:
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
