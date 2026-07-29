import io
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

from inkplate_dev_console import cli
from inkplate_dev_console.client import (
    DeviceProtocolError,
    DeviceTimeoutError,
    FrameCapture,
    SerialOpenError,
)


class FakeClient:
    instances = 0

    def __init__(self, **kwargs) -> None:
        type(self).instances += 1
        self.kwargs = kwargs
        self.calls: list[str] = []
        self.tap_result: dict[str, object] = {"command": "tap", "ok": True}

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def state(self) -> dict[str, object]:
        self.calls.append("state")
        return {"devSerialConsole": True, "screen": "LAUNCHER"}

    def frame(self, output: Path) -> FrameCapture:
        self.calls.append("frame")
        return FrameCapture(
            width=8,
            height=1,
            row_bytes=1,
            byte_count=1,
            frame_format="1bpp-lsb-black1",
            path=output,
        )

    def tap(self, _x: int, _y: int) -> dict[str, object]:
        return self.tap_result


class CliTests(unittest.TestCase):
    def parse_json(self, value: str) -> dict[str, Any]:
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            self.fail(f"CLI output was not valid JSON: {exc}: {value!r}")
        self.assertIsInstance(parsed, dict)
        return cast(dict[str, Any], parsed)

    def run_main(self, argv: list[str]) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch("sys.stdout", stdout), patch("sys.stderr", stderr):
            exit_code = cli.main(argv)
        return exit_code, stdout.getvalue(), stderr.getvalue()

    def test_bare_invocation_prints_help_and_succeeds(self) -> None:
        exit_code, stdout, stderr = self.run_main([])
        self.assertEqual(exit_code, 0)
        self.assertIn("Fast paths:", stdout)
        self.assertEqual(stderr, "")

    def test_global_options_work_after_subcommand(self) -> None:
        args = cli.build_parser().parse_args(
            cli.normalize_global_options(
                ["state", "--port", "/dev/example", "--timeout", "5", "--quiet-boot"]
            )
        )
        self.assertEqual(args.command, "state")
        self.assertEqual(args.port, "/dev/example")
        self.assertEqual(args.timeout, 5)
        self.assertTrue(args.quiet_boot)

    def test_obvious_aliases_resolve_to_canonical_commands(self) -> None:
        parser = cli.build_parser()
        output = str(Path(tempfile.gettempdir()) / "frame.png")
        status = parser.parse_args(["status"])
        capture = parser.parse_args(["capture", "--output", output])
        self.assertEqual(cli.canonical_command(status.command), "state")
        self.assertEqual(cli.canonical_command(capture.command), "frame")
        self.assertEqual(capture.out, Path(output))

    def test_typo_error_teaches_exact_command(self) -> None:
        with self.assertRaises(SystemExit) as raised:
            self.run_main(["sttae"])
        self.assertEqual(raised.exception.code, 2)

        stderr = io.StringIO()
        with patch("sys.stderr", stderr), self.assertRaises(SystemExit):
            cli.main(["sttae"])
        self.assertIn("Did you mean: `inkplate-dev state --help`?", stderr.getvalue())

    def test_capabilities_are_stable_json(self) -> None:
        exit_code, stdout, stderr = self.run_main(["capabilities", "--json"])
        payload = self.parse_json(stdout)
        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr, "")
        self.assertEqual(payload["contractVersion"], "1")
        self.assertEqual(payload["exitCodes"]["device"], 4)
        self.assertTrue(payload["featureFlags"]["globalOptionsAnywhere"])
        self.assertEqual(payload["commands"], sorted(payload["commands"]))

    def test_json_error_contract_for_device_timeout(self) -> None:
        with patch.object(
            cli,
            "run_command",
            side_effect=DeviceTimeoutError("No DEV_STATE. Next: flash dev firmware."),
        ):
            exit_code, stdout, stderr = self.run_main(["state", "--json-errors"])

        payload = self.parse_json(stderr)
        self.assertEqual(exit_code, 4)
        self.assertEqual(stdout, "")
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["kind"], "device")
        self.assertEqual(payload["error"]["exitCode"], 4)
        self.assertIn("Next:", payload["error"]["message"])

    def test_snapshot_reuses_one_client_and_orders_state_before_frame(self) -> None:
        FakeClient.instances = 0
        client = FakeClient()
        output = str(Path(tempfile.gettempdir()) / "inkplate.png")
        with patch.object(cli, "InkplateDevConsoleClient", return_value=client):
            exit_code, stdout, stderr = self.run_main(
                ["snapshot", "--out", output, "--port", "/dev/example"]
            )

        payload = self.parse_json(stdout)
        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr, "")
        self.assertEqual(FakeClient.instances, 1)
        self.assertEqual(client.calls, ["state", "frame"])
        self.assertEqual(payload["state"]["screen"], "LAUNCHER")
        frame = payload["frame"]
        self.assertIsInstance(frame, dict)
        frame = cast(dict[str, Any], frame)
        self.assertEqual(frame["path"], output)

    def test_invalid_output_extension_fails_before_opening_serial(self) -> None:
        with patch.object(cli, "InkplateDevConsoleClient") as client_class:
            exit_code, stdout, stderr = self.run_main(["frame", "--out", "frame.jpg"])

        self.assertEqual(exit_code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("--out /tmp/inkplate.png", stderr)
        client_class.assert_not_called()

    def test_nonpositive_timeout_is_a_usage_error(self) -> None:
        stderr = io.StringIO()
        with patch("sys.stderr", stderr), self.assertRaises(SystemExit) as raised:
            cli.main(["state", "--timeout", "0"])

        self.assertEqual(raised.exception.code, 2)
        self.assertIn("must be finite and greater than zero", stderr.getvalue())

    def test_nonfinite_timeout_and_interval_are_usage_errors(self) -> None:
        for argv in (
            ["state", "--timeout", "inf"],
            ["state", "--timeout", "nan"],
            ["watch", "--interval", "inf"],
        ):
            with self.subTest(argv=argv), self.assertRaises(SystemExit) as raised:
                self.run_main(argv)
            self.assertEqual(raised.exception.code, 2)

    def test_nonempty_invocation_requires_a_command(self) -> None:
        with self.assertRaises(SystemExit) as raised:
            self.run_main(["--port", "/dev/example"])
        self.assertEqual(raised.exception.code, 2)

    def test_missing_global_option_value_is_a_usage_error(self) -> None:
        with self.assertRaises(SystemExit) as raised:
            self.run_main(["state", "--port"])
        self.assertEqual(raised.exception.code, 2)

    def test_rejected_ack_returns_device_exit_with_json_body(self) -> None:
        client = FakeClient()
        client.tap_result = {"command": "tap", "ok": False, "message": "busy"}
        with patch.object(cli, "InkplateDevConsoleClient", return_value=client):
            exit_code, stdout, stderr = self.run_main(["tap", "1", "2"])

        self.assertEqual(exit_code, 4)
        payload = self.parse_json(stdout)
        self.assertEqual(payload["ok"], False)
        self.assertEqual(stderr, "")

    def test_malformed_ack_is_a_protocol_error(self) -> None:
        for ack in ({}, {"ok": 0}, {"ok": "false"}):
            with self.subTest(ack=ack), self.assertRaises(DeviceProtocolError):
                cli.print_ack(ack)

    def test_doctor_rejects_missing_selected_port_without_connecting(self) -> None:
        selected = {
            "exists": False,
            "path": "/definitely/missing",
            "readable": False,
            "source": "INKPLATE_PORT",
            "writable": False,
        }
        with (
            patch.object(
                cli,
                "port_inventory",
                return_value={
                    "candidates": [selected],
                    "selected": selected,
                    "selectionOrder": [],
                },
            ),
            patch.object(cli, "InkplateDevConsoleClient") as client_class,
        ):
            exit_code, stdout, stderr = self.run_main(["doctor", "--json"])

        payload = self.parse_json(stdout)
        self.assertEqual(exit_code, 3)
        self.assertEqual(stderr, "")
        self.assertEqual(payload["health"], "environment-error")
        self.assertIn("/definitely/missing", payload["recommendedAction"])
        client_class.assert_not_called()

    def test_doctor_preserves_dependency_specific_recovery(self) -> None:
        selected = {
            "exists": True,
            "path": "/dev/example",
            "readable": True,
            "source": "--port",
            "writable": True,
        }
        with (
            patch.object(
                cli,
                "port_inventory",
                return_value={
                    "candidates": [],
                    "selected": selected,
                    "selectionOrder": [],
                },
            ),
            patch.object(
                cli,
                "InkplateDevConsoleClient",
                side_effect=SerialOpenError(
                    "pyserial is not installed. "
                    "Next: `python3 -m pip install pyserial`."
                ),
            ),
        ):
            exit_code, stdout, stderr = self.run_main(
                ["doctor", "--connect", "--json", "--port", "/dev/example"]
            )

        payload = self.parse_json(stdout)
        self.assertEqual(exit_code, 3)
        self.assertEqual(stderr, "")
        self.assertEqual(
            payload["recommendedAction"], "`python3 -m pip install pyserial`"
        )

    def test_repl_refuses_non_tty_before_opening_serial(self) -> None:
        with (
            patch("sys.stdin.isatty", return_value=False),
            patch.object(cli, "InkplateDevConsoleClient") as client_class,
        ):
            exit_code, stdout, stderr = self.run_main(["repl"])

        self.assertEqual(exit_code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("requires an interactive terminal", stderr)
        client_class.assert_not_called()


if __name__ == "__main__":
    unittest.main()
