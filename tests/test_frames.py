import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from inkplate_dev_console.client import (
    DeviceProtocolError,
    FrameCapture,
    InkplateDevConsoleClient,
    SerialConnectionError,
    normalize_frame_bits,
    read_serial_line,
    write_pbm,
    write_png,
)


class FrameEncodingTests(unittest.TestCase):
    def test_serial_read_failure_is_an_operational_error(self) -> None:
        serial = Mock()
        serial.readline.side_effect = OSError("device disconnected")
        with self.assertRaisesRegex(SerialConnectionError, "reconnect the device"):
            read_serial_line(serial)

    def test_client_frame_honors_configured_timeout(self) -> None:
        client = InkplateDevConsoleClient.__new__(InkplateDevConsoleClient)
        client._serial = Mock()
        client.timeout = 5.0
        client.echo_unmatched = False
        with patch("inkplate_dev_console.client.capture_frame") as capture:
            client.frame(Path(tempfile.gettempdir()) / "frame.png")
        self.assertEqual(capture.call_args.args[2], 5.0)

    def test_lsb_black1_bytes_are_reversed_for_standard_image_writers(self) -> None:
        self.assertEqual(
            normalize_frame_bits(bytes([0b00000001]), "1bpp-lsb-black1"),
            bytes([0b10000000]),
        )
        self.assertEqual(
            normalize_frame_bits(bytes([0b10000000]), "1bpp-lsb-black1"),
            bytes([0b00000001]),
        )

    def test_msb_black1_bytes_pass_through(self) -> None:
        raw = bytes([0b10100000])
        self.assertEqual(normalize_frame_bits(raw, "1bpp-msb-black1"), raw)

    def test_pbm_writer_emits_p4_header(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "frame.pbm"
            write_pbm(path, 8, 1, bytes([0b10000000]))
            self.assertEqual(path.read_bytes(), b"P4\n8 1\n\x80")

    def test_png_writer_emits_valid_signature_and_ihdr(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "frame.png"
            write_png(path, 8, 1, bytes([0b10000000]))
            payload = path.read_bytes()
            self.assertTrue(payload.startswith(b"\x89PNG\r\n\x1a\n"))
            self.assertEqual(payload[12:16], b"IHDR")
            width, height, bit_depth, color_type = struct.unpack(
                ">IIBB", payload[16:26]
            )
            self.assertEqual((width, height, bit_depth, color_type), (8, 1, 1, 3))

    def test_frame_metadata_errors_are_protocol_errors(self) -> None:
        with self.assertRaises(DeviceProtocolError):
            FrameCapture.from_meta({"width": 8}, Path("frame.png"))


if __name__ == "__main__":
    unittest.main()
