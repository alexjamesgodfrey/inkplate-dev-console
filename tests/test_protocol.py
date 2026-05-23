import unittest

from inkplate_dev_console.client import parse_json_object


class ProtocolTests(unittest.TestCase):
    def test_parse_json_object_rejects_non_object(self) -> None:
        with self.assertRaises(RuntimeError):
            parse_json_object("[]", "DEV_STATE")

    def test_parse_json_object_returns_dict(self) -> None:
        self.assertEqual(parse_json_object('{"ok":true}', "DEV_ACK"), {"ok": True})


if __name__ == "__main__":
    unittest.main()
