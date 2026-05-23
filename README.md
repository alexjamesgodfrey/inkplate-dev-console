# Inkplate Dev Console

Observe and control Inkplate firmware from a terminal or an agent loop.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

`inkplate-dev-console` is two small pieces that share one boring serial protocol:

- an Arduino library for dev firmware builds
- a Python CLI named `inkplate-dev`

It lets you dump the current e-paper framebuffer, read structured JSON state, inject taps, keep the device awake while debugging, and build repeatable hardware-in-the-loop tests.

## Quick Example

```bash
python3 -m pip install git+https://github.com/alexjamesgodfrey/inkplate-dev-console.git

inkplate-dev state
inkplate-dev frame --out /tmp/inkplate.png
inkplate-dev tap 420 260
inkplate-dev square e2
inkplate-dev watch --out /tmp/inkplate.png --interval 1
```

## Why

E-paper devices are awkward to debug because the thing you need to inspect is the physical screen. Serial logs help, but they do not answer "what is currently visible?" or "did that tap do what I expected?"

This library turns the firmware into a small, explicit test surface:

| Need | Command |
| --- | --- |
| Inspect app state | `inkplate-dev state` |
| Capture screen pixels | `inkplate-dev frame --out screen.png` |
| Inject a physical coordinate | `inkplate-dev tap 120 310` |
| Inject an app-specific target | `inkplate-dev square e2` |
| Keep development builds awake | `inkplate-dev awake on` |
| Debug interactively | `inkplate-dev repl` |

## Protocol

The protocol is line-oriented and intentionally easy to inspect with a serial monitor.

```text
dev:state
DEV_STATE {"screen":"launcher","battery":100}

dev:frame
DEV_FRAME_BEGIN {"width":800,"height":600,"rowBytes":100,"bytes":60000,"encoding":"hex","format":"1bpp-lsb-black1"}
DEV_FRAME deadbeef...
DEV_FRAME_END

dev:tap 120 310
DEV_ACK {"command":"tap","ok":true,"message":"queued 120,310"}
```

The current Inkplate framebuffer format is `1bpp-lsb-black1`: each bit is a pixel, `1` means black, and bits inside each byte are least-significant-bit first. The Python CLI reverses bits when writing standard PBM/PNG files so captures match the physical display.

## Arduino Usage

Add the library to `platformio.ini`:

```ini
lib_deps =
  https://github.com/alexjamesgodfrey/inkplate-dev-console.git
```

Enable it only in a dev environment:

```ini
[env:device_dev]
build_flags =
  -DINKPLATE_DEV_CONSOLE=1
```

Register callbacks from firmware:

```cpp
#include <ArduinoJson.h>
#include <InkplateDevConsole.h>

InkplateDevConsole devConsole;

void printDevState(Print& out, void*) {
  JsonDocument doc;
  doc["screen"] = "launcher";
  doc["battery"] = 100;
  serializeJson(doc, out);
}

bool getDevFrame(InkplateDevConsole::Frame& frame, String& error, void*) {
  if (!InkplateDevConsole::frameFromInkplate(display, frame)) {
    error = "display framebuffer is not allocated";
    return false;
  }
  return true;
}

bool resolveDevPoint(const String& name, int& x, int& y, void*) {
  if (name == "back") {
    x = 30;
    y = 30;
    return true;
  }
  return false;
}

void setup() {
  Serial.begin(115200);
  devConsole.setStateCallback(printDevState);
  devConsole.setFrameCallback(getDevFrame);
  devConsole.setNamedPointCallback(resolveDevPoint);
  devConsole.begin(Serial);
}

void loop() {
  devConsole.poll();

  int x = 0;
  int y = 0;
  if (devConsole.consumeTap(x, y)) {
    // Route the point through the same touch handling path as the panel.
  }
}
```

Use `devConsole.keepAwake()` to skip firmware deep-sleep logic in development builds.

## Python Usage

Install from GitHub:

```bash
python3 -m pip install git+https://github.com/alexjamesgodfrey/inkplate-dev-console.git
```

Run the CLI:

```bash
inkplate-dev --port /dev/cu.usbserial-10 state
inkplate-dev frame --out /tmp/current.png
inkplate-dev tap 400 300
inkplate-dev repl
```

Or use the Python API:

```python
from pathlib import Path
from inkplate_dev_console import InkplateDevConsoleClient

with InkplateDevConsoleClient() as dev:
    print(dev.state())
    dev.tap(420, 260)
    capture = dev.frame(Path("/tmp/after.png"))
    print(capture.as_json())
```

Port detection checks `INKPLATE_PORT`, then `UPLOAD_PORT`, then common macOS and Linux USB serial device names.

## Commands

| Command | Description |
| --- | --- |
| `inkplate-dev state` | Print `DEV_STATE` JSON. |
| `inkplate-dev frame --out screen.png` | Capture the framebuffer as `.png`, `.pbm`, or `.raw`. |
| `inkplate-dev tap X Y` | Queue a synthetic tap at screen coordinates. |
| `inkplate-dev square e2` | Queue a synthetic tap resolved by firmware's named-point callback. |
| `inkplate-dev back` | Queue the firmware's `back` target, or `(30, 30)` if none is registered. |
| `inkplate-dev refresh` | Send an app-specific `refresh` command if firmware implements it. |
| `inkplate-dev bench` | Send an app-specific `bench` command if firmware implements it. |
| `inkplate-dev awake on/off` | Toggle the firmware keep-awake flag. |
| `inkplate-dev watch --out screen.png --interval 1` | Capture repeatedly. |
| `inkplate-dev repl` | Open an interactive console. |

## Architecture

```text
Python CLI / tests / agent
          |
          | USB serial, line-oriented dev:* protocol
          v
InkplateDevConsole Arduino library
          |
          +-- state callback -> app JSON
          +-- frame callback -> Inkplate framebuffer
          +-- tap queue -> app touch classifier
          +-- custom command callback -> app-specific actions
```

## Isolation Rules

- Compile the console only into development firmware.
- Expose only explicit callback data; never serialize secrets by default.
- Keep app-specific commands behind a whitelist.
- Treat the Python CLI as the stable automation primitive; wrap it with MCP or other agent tooling later.

## Troubleshooting

`No Inkplate USB serial port found`

Set the port explicitly:

```bash
INKPLATE_PORT=/dev/cu.usbserial-10 inkplate-dev state
```

`Timed out waiting for DEV_STATE`

Confirm the firmware was compiled with the dev console enabled and that `devConsole.poll()` runs in `loop()`.

The PNG looks horizontally scrambled in 8-pixel groups

The firmware likely reported the wrong frame format. Inkplate's 1-bit buffer should normally be `1bpp-lsb-black1`.

Opening serial resets the board

The CLI opens with DTR enabled and RTS disabled, which works for common ESP32 USB serial adapters. If your adapter behaves differently, open an issue with the adapter model and boot log.

## Limitations

- This is a development/debug interface, not a production remote-control protocol.
- It assumes a 1-bit framebuffer today.
- It does not authenticate serial access; physical USB access is the trust boundary.
- Named targets such as chess squares are application-defined.

## Contributions

*About Contributions:* Please don't take this the wrong way, but I do not accept outside contributions for any of my projects. I simply don't have the mental bandwidth to review anything, and it's my name on the thing, so I'm responsible for any problems it causes; thus, the risk-reward is highly asymmetric from my perspective. I'd also have to worry about other "stakeholders," which seems unwise for tools I mostly make for myself for free. Feel free to submit issues, and even PRs if you want to illustrate a proposed fix, but know I won't merge them directly. Instead, I'll have Claude or Codex review submissions via `gh` and independently decide whether and how to address them. Bug reports in particular are welcome. Sorry if this offends, but I want to avoid wasted time and hurt feelings. I understand this isn't in sync with the prevailing open-source ethos that seeks community contributions, but it's the only way I can move at this velocity and keep my sanity.

## License

MIT
