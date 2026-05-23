#include <Arduino.h>
#include <ArduinoJson.h>
#include <Inkplate.h>
#include <InkplateDevConsole.h>

Inkplate display(INKPLATE_1BIT);
InkplateDevConsole devConsole;

void printState(Print& out, void*) {
  JsonDocument doc;
  doc["screen"] = "example";
  doc["uptimeMs"] = millis();
  serializeJson(doc, out);
}

bool getFrame(InkplateDevConsole::Frame& frame, String& error, void*) {
  if (!InkplateDevConsole::frameFromInkplate(display, frame)) {
    error = "display framebuffer is not allocated";
    return false;
  }
  return true;
}

bool resolvePoint(const String& name, int& x, int& y, void*) {
  if (name == "center") {
    x = display.width() / 2;
    y = display.height() / 2;
    return true;
  }
  return false;
}

void setup() {
  Serial.begin(115200);
  display.begin();
  display.clearDisplay();
  display.setCursor(40, 80);
  display.setTextSize(3);
  display.print("InkplateDevConsole");
  display.display();

  devConsole.setStateCallback(printState);
  devConsole.setFrameCallback(getFrame);
  devConsole.setNamedPointCallback(resolvePoint);
  devConsole.begin(Serial);

  Serial.println("Send dev:help");
}

void loop() {
  devConsole.poll();

  int x = 0;
  int y = 0;
  if (devConsole.consumeTap(x, y)) {
    display.fillCircle(x, y, 8, BLACK);
    display.partialUpdate();
  }
}
