import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from inkplate_dev_console.client import (
    DeviceProtocolError,
    FrameCapture,
    InkplateDevConsoleClient,
    OutputPathError,
    SerialConnectionError,
    capture_frame,
    normalize_frame_bits,
    read_serial_line,
    write_frame_output,
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
        invalid = (
            {"width": 8},
            {"width": -8, "height": 1, "bytes": 1},
            {"width": 8, "height": 1, "rowBytes": 1, "bytes": 2},
            {
                "width": 8,
                "height": 1,
                "rowBytes": 1,
                "bytes": 1,
                "format": "unknown",
            },
            {
                "width": 8,
                "height": 1,
                "rowBytes": 1,
                "bytes": 1,
                "encoding": "base64",
            },
        )
        for metadata in invalid:
            with (
                self.subTest(metadata=metadata),
                self.assertRaises(DeviceProtocolError),
            ):
                FrameCapture.from_meta(metadata, Path("frame.png"))

    def test_padded_rows_are_preserved_raw_and_compacted_for_images(self) -> None:
        metadata = {
            "width": 8,
            "height": 2,
            "rowBytes": 2,
            "bytes": 4,
            "encoding": "hex",
            "format": "1bpp-msb-black1",
        }
        frame = b"\x80\xaa\x40\xbb"
        with tempfile.TemporaryDirectory() as directory:
            raw = Path(directory) / "frame.raw"
            pbm = Path(directory) / "frame.pbm"
            write_frame_output(raw, metadata, frame)
            write_frame_output(pbm, metadata, frame)
            self.assertEqual(raw.read_bytes(), frame)
            self.assertEqual(pbm.read_bytes(), b"P4\n8 2\n\x80\x40")

    def test_frame_surfaces_negative_ack_immediately(self) -> None:
        serial = Mock()
        serial.readline.return_value = (
            b'DEV_ACK {"command":"frame","ok":false,'
            b'"message":"frame callback failed"}\n'
        )
        with self.assertRaisesRegex(DeviceProtocolError, "frame callback failed"):
            capture_frame(
                serial,
                Path(tempfile.gettempdir()) / "rejected-frame.raw",
                timeout=0.01,
                echo_unmatched=False,
            )

    def test_invalid_frame_hex_is_rejected_instead_of_filtered(self) -> None:
        serial = Mock()
        serial.readline.side_effect = [
            b'DEV_FRAME_BEGIN {"width":8,"height":1,"rowBytes":1,"bytes":1}\n',
            b"DEV_FRAME 0g\n",
        ]
        with self.assertRaisesRegex(DeviceProtocolError, "hexadecimal"):
            capture_frame(
                serial,
                Path(tempfile.gettempdir()) / "invalid-frame.raw",
                timeout=1,
                echo_unmatched=False,
            )

    def test_frame_payload_cannot_exceed_declared_size(self) -> None:
        serial = Mock()
        serial.readline.side_effect = [
            b'DEV_FRAME_BEGIN {"width":8,"height":1,"rowBytes":1,"bytes":1}\n',
            b"DEV_FRAME 0001\n",
        ]
        with self.assertRaisesRegex(DeviceProtocolError, "exceeds"):
            capture_frame(
                serial,
                Path(tempfile.gettempdir()) / "oversized-frame.raw",
                timeout=1,
                echo_unmatched=False,
            )

    def test_reset_retry_extension_applies_to_entire_frame(self) -> None:
        serial = Mock()
        serial.readline.side_effect = [
            b"[INIT] Setup complete\n",
            b'DEV_FRAME_BEGIN {"width":8,"height":1,"rowBytes":1,"bytes":1}\n',
            b"DEV_FRAME 01\n",
            b"DEV_FRAME_END\n",
        ]
        output = Path(tempfile.gettempdir()) / "retry-frame.raw"
        with patch(
            "inkplate_dev_console.client.time.monotonic",
            side_effect=[0.0, 0.0, 0.1, 5.0, 5.1, 5.2, 5.3],
        ):
            capture = capture_frame(serial, output, timeout=1, echo_unmatched=False)
        self.assertEqual(capture.byte_count, 1)
        self.assertEqual(output.read_bytes(), b"\x01")

    def test_output_path_failure_is_an_operational_error(self) -> None:
        output = Path(tempfile.gettempdir()) / "blocked-frame.pbm"
        with (
            patch.object(Path, "write_bytes", side_effect=PermissionError("denied")),
            self.assertRaisesRegex(OutputPathError, "writable path"),
        ):
            write_pbm(output, 8, 1, b"\x00")


if __name__ == "__main__":
    unittest.main()
