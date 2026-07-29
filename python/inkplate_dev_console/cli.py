from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections.abc import Sequence
from difflib import get_close_matches
from importlib import metadata
from pathlib import Path
from typing import NoReturn

from .client import (
    DEFAULT_BAUD,
    DEFAULT_FRAME_PATH,
    DeviceProtocolError,
    DeviceTimeoutError,
    InkplateDevConsoleClient,
    InkplateDevConsoleError,
    PortDetectionError,
    SerialConnectionError,
    SerialOpenError,
    port_inventory,
)

CONTRACT_VERSION = "1"
EXIT_CODES = {
    "success": 0,
    "usage": 2,
    "environment": 3,
    "device": 4,
    "internal": 5,
    "interrupted": 130,
}
COMMAND_ALIASES = {
    "capture": "frame",
    "device-help": "help",
    "diagnose": "doctor",
    "screen": "frame",
    "status": "state",
}
CANONICAL_COMMANDS = (
    "awake",
    "back",
    "bench",
    "capabilities",
    "doctor",
    "frame",
    "help",
    "ports",
    "refresh",
    "repl",
    "robot-docs",
    "snapshot",
    "square",
    "state",
    "tap",
    "watch",
)
GLOBAL_OPTIONS_WITH_VALUE = ("--port", "-p", "--baud", "-b", "--timeout", "-t")
GLOBAL_FLAGS = ("--quiet-boot", "--no-boot-logs", "--json-errors")


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def positive_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("must be finite and greater than zero")
    return parsed


def nonnegative_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed < 0:
        raise argparse.ArgumentTypeError("must be finite and zero or greater")
    return parsed


def package_version() -> str:
    try:
        return metadata.version("inkplate-dev-console")
    except metadata.PackageNotFoundError:
        return "0.2.0+source"


def print_json(value: object) -> None:
    print(json.dumps(value, indent=2, sort_keys=True))


def capture_to_json(
    client: InkplateDevConsoleClient, output: Path
) -> dict[str, object]:
    return client.frame(output).as_json()


def normalize_global_options(argv: Sequence[str]) -> list[str]:
    """Allow connection options before or after the subcommand."""
    global_tokens: list[str] = []
    remaining: list[str] = []
    index = 0
    while index < len(argv):
        token = argv[index]
        option_name = token.split("=", 1)[0]
        if option_name in GLOBAL_OPTIONS_WITH_VALUE:
            if "=" not in token and index + 1 >= len(argv):
                remaining.append(token)
            else:
                global_tokens.append(token)
            if "=" not in token and index + 1 < len(argv):
                index += 1
                global_tokens.append(argv[index])
        elif token in GLOBAL_FLAGS:
            global_tokens.append(token)
        else:
            remaining.append(token)
        index += 1
    return [*global_tokens, *remaining]


class AgentArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        hint = ""
        invalid_choice = re.search(r"invalid choice: '([^']+)'", message)
        unknown_argument = re.search(r"unrecognized arguments?: ([^ ]+)", message)
        misspelling = (
            invalid_choice.group(1)
            if invalid_choice
            else unknown_argument.group(1)
            if unknown_argument
            else ""
        )
        candidates = [*CANONICAL_COMMANDS, *COMMAND_ALIASES, "--output", "--port"]
        matches = get_close_matches(misspelling, candidates, n=1, cutoff=0.72)
        if matches:
            corrected = matches[0]
            hint = f"\nDid you mean: `inkplate-dev {corrected} --help`?"
        self.print_usage(sys.stderr)
        self.exit(EXIT_CODES["usage"], f"{self.prog}: error: {message}.{hint}\n")


def add_output_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "-o",
        "--out",
        "--output",
        dest="out",
        type=Path,
        default=DEFAULT_FRAME_PATH,
        help="Output path ending in .png, .pbm, or .raw (default: %(default)s).",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = AgentArgumentParser(
        prog="inkplate-dev",
        description="Observe, diagnose, and control Inkplate dev firmware over USB serial.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Fast paths:
  inkplate-dev doctor --json
  inkplate-dev snapshot --out /tmp/inkplate.png
  inkplate-dev state --port /dev/cu.usbserial-10
  inkplate-dev capabilities --json

Connection options work before or after a command. Device logs go to stderr;
JSON and command results go to stdout.
""",
    )
    parser.add_argument(
        "-p",
        "--port",
        default=None,
        help="Serial port; otherwise INKPLATE_PORT, UPLOAD_PORT, then USB auto-detection.",
    )
    parser.add_argument(
        "-b",
        "--baud",
        type=positive_int,
        default=DEFAULT_BAUD,
        help="Serial baud rate (default: %(default)s).",
    )
    parser.add_argument(
        "-t",
        "--timeout",
        type=positive_float,
        default=30.0,
        help="Device response timeout in seconds (default: %(default)s).",
    )
    parser.add_argument(
        "--quiet-boot",
        "--no-boot-logs",
        dest="quiet_boot",
        action="store_true",
        help="Suppress unmatched firmware boot logs on stderr.",
    )
    parser.add_argument(
        "--json-errors",
        action="store_true",
        help="Emit operational errors as one JSON object on stderr.",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {package_version()}"
    )

    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")
    subparsers.add_parser(
        "state",
        aliases=["status"],
        help="Read structured firmware state JSON.",
        description="Read structured firmware state JSON.",
    )
    subparsers.add_parser("back", help="Inject the firmware back target.")
    subparsers.add_parser("refresh", help="Run the app-specific refresh command.")
    subparsers.add_parser("bench", help="Run the app-specific benchmark command.")
    subparsers.add_parser(
        "help",
        aliases=["device-help"],
        help="Read firmware protocol help (CLI help is --help).",
    )
    subparsers.add_parser("repl", help="Open an explicit interactive device console.")

    tap = subparsers.add_parser("tap", help="Inject a tap at screen coordinates.")
    tap.add_argument("x", type=int, help="Horizontal pixel coordinate.")
    tap.add_argument("y", type=int, help="Vertical pixel coordinate.")

    square = subparsers.add_parser("square", help="Inject an app-defined named target.")
    square.add_argument("square", help="Target name, such as e2.")

    awake = subparsers.add_parser(
        "awake", help="Toggle the development keep-awake flag."
    )
    awake.add_argument("value", choices=("on", "off"))

    frame = subparsers.add_parser(
        "frame",
        aliases=["capture", "screen"],
        help="Capture the current framebuffer.",
    )
    add_output_argument(frame)

    watch = subparsers.add_parser(
        "watch", help="Capture repeated framebuffer samples as NDJSON."
    )
    add_output_argument(watch)
    watch.add_argument(
        "--interval",
        type=nonnegative_float,
        default=1.0,
        help="Seconds between captures (default: %(default)s).",
    )
    watch.add_argument(
        "--count", type=positive_int, default=None, help="Stop after N captures."
    )

    snapshot = subparsers.add_parser(
        "snapshot",
        help="Read state and capture a frame through one serial connection.",
        description=(
            "Recommended verification workflow: read state and capture a frame "
            "through one serial connection."
        ),
    )
    add_output_argument(snapshot)

    ports = subparsers.add_parser(
        "ports", help="List deterministic serial-port selection data."
    )
    ports.add_argument(
        "--json", action="store_true", help="Emit the full machine-readable inventory."
    )

    doctor = subparsers.add_parser(
        "doctor",
        aliases=["diagnose"],
        help="Diagnose local setup and optionally probe the device.",
    )
    doctor.add_argument(
        "--connect",
        action="store_true",
        help="Open the selected port and request state.",
    )
    doctor.add_argument(
        "--json", action="store_true", help="Emit the full machine-readable report."
    )

    capabilities = subparsers.add_parser(
        "capabilities",
        help="Describe commands, schemas, environment variables, and exit codes.",
    )
    capabilities.add_argument(
        "--json", action="store_true", help="Emit the stable JSON contract."
    )

    robot_docs = subparsers.add_parser(
        "robot-docs", help="Print embedded agent-facing guidance."
    )
    robot_docs.add_argument("topic", choices=("guide",), nargs="?", default="guide")

    return parser


def capabilities_payload() -> dict[str, object]:
    return {
        "commands": list(CANONICAL_COMMANDS),
        "contractVersion": CONTRACT_VERSION,
        "environment": {
            "INKPLATE_PORT": "Tool-specific serial-port override (highest environment priority).",
            "UPLOAD_PORT": "PlatformIO-compatible serial-port override.",
        },
        "exitCodes": EXIT_CODES,
        "featureFlags": {
            "deviceProtocolHelp": True,
            "doctorConnectProbe": True,
            "frameFormats": ["png", "pbm", "raw"],
            "globalOptionsAnywhere": True,
            "snapshot": True,
        },
        "outputContracts": {
            "capabilities": "JSON object",
            "doctor": "JSON object with health, inventory, and recommendedAction",
            "frame": "JSON object describing the written artifact",
            "ports": "JSON object with selected port provenance and candidates",
            "snapshot": "JSON object containing state and frame",
            "state": "firmware-defined JSON object",
            "watch": "one frame JSON object per line (NDJSON)",
        },
        "version": package_version(),
    }


def robot_guide() -> str:
    return """\
Inkplate Dev Console agent guide

Canonical loop (one serial connection):
  inkplate-dev snapshot --out /tmp/inkplate.png

Diagnose before retrying:
  inkplate-dev doctor --connect --json

Explicit port (options may appear before or after the command):
  inkplate-dev state --port /dev/cu.usbserial-10

Input and verification:
  inkplate-dev tap 420 260
  inkplate-dev snapshot --out /tmp/after.png

Output contract:
  stdout = command data / JSON
  stderr = boot logs and diagnostics
  exit 2 = usage, 3 = local environment, 4 = device/protocol, 5 = internal

Production firmware intentionally omits the dev console. A timeout against a
production build is expected; flash with -DINKPLATE_DEV_CONSOLE=1 for HIL work.
"""


def run_repl(client: InkplateDevConsoleClient) -> None:
    print(
        "Inkplate dev console. Commands: state, frame [path], tap x y, square e2, "
        "back, refresh, awake on/off, help, quit"
    )
    while True:
        try:
            line = input("inkplate> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return

        if not line:
            continue
        lowered = line.lower()
        if lowered in {"quit", "exit"}:
            return

        try:
            if lowered in {"state", "status"}:
                print_json(client.state())
            elif lowered.startswith(("frame", "capture", "screen")):
                parts = line.split(maxsplit=1)
                out = Path(parts[1]) if len(parts) == 2 else DEFAULT_FRAME_PATH
                print_json(capture_to_json(client, out))
            else:
                print_json(client.command(line))
        except Exception as exc:  # noqa: BLE001 - REPL isolates each user command.
            print(f"error: {exc}", file=sys.stderr)


def print_ports_human(inventory: dict[str, object]) -> None:
    selected = inventory["selected"]
    if selected is None:
        print("No Inkplate USB serial ports found.")
        print("Next: connect the device or set INKPLATE_PORT=/dev/...")
        return

    if not isinstance(selected, dict):
        raise TypeError("Invalid internal port inventory: selected must be an object")
    print(f"Selected: {selected['path']} ({selected['source']})")
    candidates = inventory["candidates"]
    if not isinstance(candidates, list):
        raise TypeError("Invalid internal port inventory: candidates must be a list")
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise TypeError(
                "Invalid internal port inventory: candidate must be an object"
            )
        print(
            f"- {candidate['path']} source={candidate['source']} "
            f"exists={str(candidate['exists']).lower()} "
            f"readable={str(candidate['readable']).lower()} "
            f"writable={str(candidate['writable']).lower()}"
        )


def doctor_payload(args: argparse.Namespace) -> tuple[dict[str, object], int]:
    inventory = port_inventory(args.port)
    selected = inventory["selected"]
    report: dict[str, object] = {
        "contractVersion": CONTRACT_VERSION,
        "health": "ready",
        "portInventory": inventory,
        "recommendedAction": "inkplate-dev snapshot --out /tmp/inkplate.png",
        "version": package_version(),
    }
    if selected is None:
        report["health"] = "environment-error"
        report["recommendedAction"] = "Connect the device or set INKPLATE_PORT=/dev/..."
        return report, EXIT_CODES["environment"]
    if not isinstance(selected, dict):
        raise TypeError("Invalid internal port inventory: selected must be an object")
    if not all(selected.get(flag) for flag in ("exists", "readable", "writable")):
        report["health"] = "environment-error"
        report["recommendedAction"] = (
            f"Verify {selected.get('path')!s} exists and is readable/writable, "
            "or run `inkplate-dev ports --json` and choose another port."
        )
        return report, EXIT_CODES["environment"]

    if args.connect:
        try:
            with InkplateDevConsoleClient(
                port=args.port,
                baud=args.baud,
                timeout=args.timeout,
                echo_unmatched=not args.quiet_boot,
            ) as client:
                report["deviceState"] = client.state()
                report["health"] = "device-ready"
        except (PortDetectionError, SerialOpenError, SerialConnectionError) as exc:
            report["error"] = str(exc)
            report["health"] = "environment-error"
            report["recommendedAction"] = next_action(
                exc, "Close other serial monitors and verify the selected port."
            )
            return report, EXIT_CODES["environment"]
        except (
            DeviceTimeoutError,
            DeviceProtocolError,
            InkplateDevConsoleError,
        ) as exc:
            report["error"] = str(exc)
            report["health"] = "device-error"
            report["recommendedAction"] = (
                "Flash dev firmware with -DINKPLATE_DEV_CONSOLE=1, then retry this command."
            )
            return report, EXIT_CODES["device"]

    return report, EXIT_CODES["success"]


def canonical_command(command: str) -> str:
    return COMMAND_ALIASES.get(command, command)


def next_action(exc: Exception, fallback: str) -> str:
    marker = "Next: "
    message = str(exc)
    if marker in message:
        return message.rsplit(marker, 1)[1].rstrip(".")
    return fallback


def validate_output_path(output: Path) -> None:
    if output.suffix.lower() not in {".png", ".pbm", ".raw"}:
        raise ValueError(
            f"Unsupported output path {str(output)!r}. "
            "Next: use `--out /tmp/inkplate.png`, `.pbm`, or `.raw`."
        )


def print_ack(ack: dict[str, object]) -> int:
    if not isinstance(ack.get("ok"), bool):
        raise DeviceProtocolError(
            "DEV_ACK must contain boolean field `ok`. "
            "Next: verify the firmware and CLI protocol versions match."
        )
    print_json(ack)
    return (
        EXIT_CODES["success"]
        if ack.get("ok", True) is not False
        else EXIT_CODES["device"]
    )


def run_command(args: argparse.Namespace) -> int:
    command = canonical_command(args.command)

    if command == "ports":
        inventory = port_inventory(args.port)
        if args.json:
            print_json(inventory)
        else:
            print_ports_human(inventory)
        return EXIT_CODES["success"]
    if command == "doctor":
        report, exit_code = doctor_payload(args)
        if args.json:
            print_json(report)
        else:
            print(f"Health: {report['health']}")
            print(f"Next: {report['recommendedAction']}")
        return exit_code
    if command == "capabilities":
        print_json(capabilities_payload())
        return EXIT_CODES["success"]
    if command == "robot-docs":
        print(robot_guide())
        return EXIT_CODES["success"]
    if command == "repl" and not sys.stdin.isatty():
        raise ValueError(
            "`repl` requires an interactive terminal. "
            "Next: use `inkplate-dev snapshot --out /tmp/inkplate.png`."
        )
    if command in {"frame", "snapshot", "watch"}:
        validate_output_path(args.out)

    with InkplateDevConsoleClient(
        port=args.port,
        baud=args.baud,
        timeout=args.timeout,
        echo_unmatched=not args.quiet_boot,
    ) as client:
        if command == "state":
            print_json(client.state())
        elif command == "tap":
            return print_ack(client.tap(args.x, args.y))
        elif command == "square":
            return print_ack(client.square(args.square))
        elif command in {"back", "refresh", "bench", "help"}:
            return print_ack(client.command(command))
        elif command == "awake":
            return print_ack(client.command(f"awake {args.value}"))
        elif command == "frame":
            print_json(capture_to_json(client, args.out))
        elif command == "snapshot":
            state = client.state()
            frame = capture_to_json(client, args.out)
            print_json(
                {
                    "contractVersion": CONTRACT_VERSION,
                    "frame": frame,
                    "state": state,
                }
            )
        elif command == "watch":
            for capture in client.watch(args.out, args.interval, args.count):
                print(json.dumps(capture.as_json(), sort_keys=True), flush=True)
        elif command == "repl":
            run_repl(client)
        else:
            raise ValueError(f"Unknown command: {command}")

    return EXIT_CODES["success"]


def emit_error(kind: str, message: str, exit_code: int, json_errors: bool) -> None:
    if json_errors:
        print(
            json.dumps(
                {
                    "contractVersion": CONTRACT_VERSION,
                    "error": {"exitCode": exit_code, "kind": kind, "message": message},
                    "ok": False,
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
    else:
        print(f"inkplate-dev: {kind}: {message}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    if not raw_argv:
        parser.print_help()
        return EXIT_CODES["success"]

    args = parser.parse_args(normalize_global_options(raw_argv))
    if args.command is None:
        parser.error("a command is required; run `inkplate-dev --help`")

    try:
        return run_command(args)
    except ValueError as exc:
        emit_error("usage", str(exc), EXIT_CODES["usage"], args.json_errors)
        return EXIT_CODES["usage"]
    except (PortDetectionError, SerialOpenError, SerialConnectionError) as exc:
        emit_error("environment", str(exc), EXIT_CODES["environment"], args.json_errors)
        return EXIT_CODES["environment"]
    except (DeviceTimeoutError, DeviceProtocolError, InkplateDevConsoleError) as exc:
        emit_error("device", str(exc), EXIT_CODES["device"], args.json_errors)
        return EXIT_CODES["device"]
    except KeyboardInterrupt:
        emit_error(
            "interrupted",
            "Interrupted by user.",
            EXIT_CODES["interrupted"],
            args.json_errors,
        )
        return EXIT_CODES["interrupted"]
    except Exception as exc:  # noqa: BLE001 - CLI boundary must never leak a traceback.
        emit_error(
            "internal",
            f"{type(exc).__name__}: {exc}. Next: rerun with `--json-errors` and report this failure.",
            EXIT_CODES["internal"],
            args.json_errors,
        )
        return EXIT_CODES["internal"]
