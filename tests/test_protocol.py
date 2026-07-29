import unittest
from unittest.mock import Mock

from inkplate_dev_console.client import (
    DeviceProtocolError,
    DeviceTimeoutError,
    parse_json_object,
    request_ack,
    request_state,
)


class ProtocolTests(unittest.TestCase):
    def test_parse_json_object_rejects_non_object(self) -> None:
        with self.assertRaises(RuntimeError):
            parse_json_object("[]", "DEV_STATE")

    def test_parse_json_object_returns_dict(self) -> None:
        self.assertEqual(parse_json_object('{"ok":true}', "DEV_ACK"), {"ok": True})

    def test_parse_json_object_rejects_nonfinite_numbers(self) -> None:
        for token in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(token=token), self.assertRaises(DeviceProtocolError):
                parse_json_object(f'{{"value":{token}}}', "DEV_STATE")

    def test_request_ack_rejects_missing_or_nonboolean_ok(self) -> None:
        for payload in (
            b"DEV_ACK {}\n",
            b'DEV_ACK {"command":"tap","ok":0}\n',
        ):
            serial = Mock()
            serial.readline.return_value = payload
            with self.subTest(payload=payload), self.assertRaises(DeviceProtocolError):
                request_ack(serial, "tap 1 2", timeout=0.01, echo_unmatched=False)

    def test_request_ack_correlates_command(self) -> None:
        serial = Mock()
        serial.readline.return_value = b'DEV_ACK {"command":"back","ok":true}\n'
        with self.assertRaisesRegex(DeviceProtocolError, "command mismatch"):
            request_ack(serial, "tap 1 2", timeout=0.01, echo_unmatched=False)

    def test_legacy_unknown_ack_is_correlated_by_echoed_request(self) -> None:
        serial = Mock()
        serial.readline.return_value = (
            b'DEV_ACK {"command":"unknown","ok":false,"message":"refresh"}\n'
        )
        ack = request_ack(serial, "refresh", timeout=0.01, echo_unmatched=False)
        self.assertFalse(ack["ok"])
        self.assertEqual(ack["message"], "refresh")

    def test_empty_legacy_unknown_ack_is_a_protocol_error(self) -> None:
        serial = Mock()
        serial.readline.return_value = (
            b'DEV_ACK {"command":"unknown","ok":false,"message":"  "}\n'
        )
        with self.assertRaisesRegex(DeviceProtocolError, "command mismatch"):
            request_ack(serial, "refresh", timeout=0.01, echo_unmatched=False)

    def test_non_help_command_does_not_accept_dev_help(self) -> None:
        serial = Mock()
        serial.readline.return_value = b"DEV_HELP stale\n"
        with self.assertRaisesRegex(DeviceTimeoutError, "Timed out"):
            request_ack(serial, "back", timeout=0.001, echo_unmatched=False)

    def test_state_surfaces_negative_ack_immediately(self) -> None:
        serial = Mock()
        serial.readline.return_value = (
            b'DEV_ACK {"command":"state","ok":false,'
            b'"message":"state callback is not configured"}\n'
        )
        with self.assertRaisesRegex(
            DeviceProtocolError, "state callback is not configured"
        ):
            request_state(serial, timeout=0.01, echo_unmatched=False)


if __name__ == "__main__":
    unittest.main()
