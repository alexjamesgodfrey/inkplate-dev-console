---
name: inkplate-dev-console
description: "Use when Codex needs to observe, control, or debug a connected Inkplate device through the inkplate-dev-console serial protocol: capturing the current e-paper screen, reading firmware state JSON, injecting taps or named targets, running hardware-in-the-loop debug loops, wiring firmware to the Arduino InkplateDevConsole library, or using the inkplate-dev Python CLI from projects such as eink.fun."
---

# Inkplate Dev Console

Use `inkplate-dev-console` as the hardware feedback loop for Inkplate firmware. The loop is: read state, capture the framebuffer, inspect the image, inject an input, then capture/read state again.

## Fast Path

Prefer a project wrapper when one exists. In `eink.fun`, run from the repo root:

```bash
bash scripts/device-console.sh state
bash scripts/device-console.sh frame --out tmp/current.png
bash scripts/device-console.sh tap 420 260
bash scripts/device-console.sh back
bash scripts/device-console.sh repl
```

Without a wrapper, install/use the Python package:

```bash
python3 -m pip install git+https://github.com/alexjamesgodfrey/inkplate-dev-console.git
inkplate-dev state
inkplate-dev frame --out /tmp/inkplate.png
```

Use `INKPLATE_PORT=/dev/...` or `UPLOAD_PORT=/dev/...` when auto-detection picks the wrong serial device.

## Hardware Loop

1. Confirm the firmware is a dev build with the console enabled, usually `-DINKPLATE_DEV_CONSOLE=1`.
2. Build/upload the firmware before relying on new callbacks.
3. Run `state` and check `devSerialConsole: true`.
4. Run `frame --out <path>.png`; inspect the generated image directly.
5. Inject input with `tap x y`, `back`, or an app-defined target such as `square e2`.
6. Re-run `state` and `frame` after each action until the physical display state matches the intended behavior.

For Codex desktop, display local captures with an absolute image path:

```markdown
![Inkplate capture](/absolute/path/to/current.png)
```

## Firmware Integration

Use the Arduino library instead of writing a private parser:

```cpp
#include <InkplateDevConsole.h>

InkplateDevConsole devConsole;

void printDevState(Print& out, void*) {
  serializeJson(stateDoc, out);
}

bool getDevFrame(InkplateDevConsole::Frame& frame, String& error, void*) {
  if (!InkplateDevConsole::frameFromInkplate(display, frame)) {
    error = "display framebuffer is not allocated";
    return false;
  }
  return true;
}

void setup() {
  devConsole.setStateCallback(printDevState);
  devConsole.setFrameCallback(getDevFrame);
  devConsole.begin(Serial);
}

void loop() {
  devConsole.poll();
}
```

Route synthetic taps through the same coordinate classifier as real touch input:

```cpp
int x = 0;
int y = 0;
if (devConsole.consumeTap(x, y)) {
  // classify x/y with the same code path used for panel touches
}
```

Use `devConsole.keepAwake()` to skip deep sleep in development builds.

## Protocol Facts

The protocol is line-oriented and safe to inspect in a serial monitor:

```text
dev:state
DEV_STATE {"screen":"launcher"}

dev:frame
DEV_FRAME_BEGIN {"width":1024,"height":758,"rowBytes":128,"bytes":97024,"encoding":"hex","format":"1bpp-lsb-black1"}
DEV_FRAME ...
DEV_FRAME_END

dev:tap 120 310
DEV_ACK {"command":"tap","ok":true,"message":"queued 120,310"}
```

Inkplate 1-bit framebuffers normally use `1bpp-lsb-black1`: `1` means black, and bits inside a byte are least-significant-bit first. The CLI reverses bits when writing PBM/PNG; do not hand-roll this conversion unless debugging the CLI itself.

## Guardrails

- Do not print or serialize secrets in `DEV_STATE`; expose explicit redacted fields only.
- Keep the console compiled into dev firmware only.
- Prefer whitelisted custom commands through `setCustomCommandCallback`.
- Do not leave long-running serial monitors active when running CLI commands; only one process can own the port.
- If the device appears to reset on serial open, rerun the command after setup completes; the CLI already retries around common boot logs.

## Expected Evidence

For a completed hardware-debug task, gather concrete evidence:

- `state` output showing the expected screen/app state.
- A captured PNG that visually matches the physical screen.
- ACK output for injected input.
- A second state/frame capture proving the input changed the UI as expected.
