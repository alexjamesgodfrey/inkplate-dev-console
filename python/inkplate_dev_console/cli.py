from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .client import DEFAULT_BAUD, DEFAULT_FRAME_PATH, InkplateDevConsoleClient


def print_json(value: object) -> None:
    print(json.dumps(value, indent=2, sort_keys=True))


def capture_to_json(client: InkplateDevConsoleClient, output: Path) -> dict[str, object]:
    return client.frame(output).as_json()


def run_repl(client: InkplateDevConsoleClient) -> None:
    print("Inkplate dev console. Commands: state, frame [path], tap x y, square e2, back, refresh, awake on/off, help, quit")
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
            if lowered == "state":
                print_json(client.state())
            elif lowered.startswith("frame"):
                parts = line.split(maxsplit=1)
                out = Path(parts[1]) if len(parts) == 2 else DEFAULT_FRAME_PATH
                print_json(capture_to_json(client, out))
            else:
                print_json(client.command(line))
        except Exception as exc:
            print(f"error: {exc}", file=sys.stderr)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Control Inkplate firmware through the dev serial console.")
    parser.add_argument("--port", default=None, help="USB serial port. Defaults to INKPLATE_PORT/UPLOAD_PORT or auto-detect.")
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUD)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--quiet-boot", action="store_true", help="Do not echo unmatched firmware boot logs to stderr.")

    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("state")
    subparsers.add_parser("back")
    subparsers.add_parser("refresh")
    subparsers.add_parser("bench")
    subparsers.add_parser("help")
    subparsers.add_parser("repl")

    tap = subparsers.add_parser("tap")
    tap.add_argument("x", type=int)
    tap.add_argument("y", type=int)

    square = subparsers.add_parser("square")
    square.add_argument("square")

    awake = subparsers.add_parser("awake")
    awake.add_argument("value", choices=("on", "off"))

    frame = subparsers.add_parser("frame")
    frame.add_argument("--out", type=Path, default=DEFAULT_FRAME_PATH)

    watch = subparsers.add_parser("watch")
    watch.add_argument("--out", type=Path, default=DEFAULT_FRAME_PATH)
    watch.add_argument("--interval", type=float, default=1.0)
    watch.add_argument("--count", type=int, default=None)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    with InkplateDevConsoleClient(
        port=args.port,
        baud=args.baud,
        timeout=args.timeout,
        echo_unmatched=not args.quiet_boot,
    ) as client:
        if args.command == "state":
            print_json(client.state())
        elif args.command == "tap":
            print_json(client.tap(args.x, args.y))
        elif args.command == "square":
            print_json(client.square(args.square))
        elif args.command == "back":
            print_json(client.command("back"))
        elif args.command == "refresh":
            print_json(client.command("refresh"))
        elif args.command == "bench":
            print_json(client.command("bench"))
        elif args.command == "awake":
            print_json(client.command(f"awake {args.value}"))
        elif args.command == "help":
            print_json(client.command("help"))
        elif args.command == "frame":
            print_json(capture_to_json(client, args.out))
        elif args.command == "watch":
            for capture in client.watch(args.out, args.interval, args.count):
                print(json.dumps(capture.as_json(), sort_keys=True), flush=True)
        elif args.command == "repl":
            run_repl(client)

    return 0
