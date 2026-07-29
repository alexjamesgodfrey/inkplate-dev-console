import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from inkplate_dev_console.client import (
    PortDetectionError,
    SerialOpenError,
    detect_port,
    list_port_candidates,
    open_serial,
    port_inventory,
)


class PortDiscoveryTests(unittest.TestCase):
    def test_tool_specific_override_wins_over_platformio_override(self) -> None:
        with (
            patch.dict(
                os.environ,
                {
                    "INKPLATE_PORT": "/dev/inkplate",
                    "UPLOAD_PORT": "/dev/platformio",
                },
                clear=True,
            ),
            patch("inkplate_dev_console.client.glob.glob", return_value=[]),
        ):
            candidates = list_port_candidates()

        self.assertEqual(
            [(candidate.path, candidate.source) for candidate in candidates],
            [
                ("/dev/inkplate", "INKPLATE_PORT"),
                ("/dev/platformio", "UPLOAD_PORT"),
            ],
        )

    def test_auto_detected_ports_are_sorted_and_deduplicated(self) -> None:
        def fake_glob(pattern: str) -> list[str]:
            if pattern.endswith("usbserial*"):
                return ["/dev/cu.usbserial-20", "/dev/cu.usbserial-10"]
            if pattern.endswith("usbmodem*"):
                return ["/dev/cu.usbserial-10"]
            return []

        with (
            patch.dict(os.environ, {}, clear=True),
            patch("inkplate_dev_console.client.glob.glob", side_effect=fake_glob),
        ):
            candidates = list_port_candidates()

        self.assertEqual(
            [candidate.path for candidate in candidates],
            ["/dev/cu.usbserial-10", "/dev/cu.usbserial-20"],
        )

    def test_detect_port_failure_includes_exact_recovery_commands(self) -> None:
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("inkplate_dev_console.client.glob.glob", return_value=[]),
            self.assertRaises(PortDetectionError) as raised,
        ):
            detect_port()

        self.assertIn("inkplate-dev ports --json", str(raised.exception))
        self.assertIn("INKPLATE_PORT=/dev/...", str(raised.exception))

    def test_inventory_records_explicit_selection_provenance(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(os.environ, {}, clear=True),
            patch("inkplate_dev_console.client.glob.glob", return_value=[]),
        ):
            path = str(Path(directory) / "serial")
            inventory = port_inventory(path)

        self.assertEqual(inventory["selected"]["path"], path)
        self.assertEqual(inventory["selected"]["source"], "--port")
        self.assertEqual(inventory["selectionOrder"][0], "--port")

    def test_partially_opened_serial_handle_is_closed_on_setup_failure(self) -> None:
        class FakeSerialException(Exception):
            pass

        handle = Mock()
        handle.is_open = True
        handle.reset_input_buffer.side_effect = FakeSerialException("reset failed")
        serial_module = types.SimpleNamespace(
            Serial=Mock(return_value=handle),
            SerialException=FakeSerialException,
        )
        with (
            patch.dict(sys.modules, {"serial": serial_module}),
            patch("inkplate_dev_console.client.time.sleep"),
            self.assertRaises(SerialOpenError),
        ):
            open_serial("/dev/example", 115200)
        handle.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
