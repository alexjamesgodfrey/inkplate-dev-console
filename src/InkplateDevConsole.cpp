#include "InkplateDevConsole.h"

#include <Inkplate.h>

void InkplateDevConsole::begin(Stream& stream) {
  Config config;
  begin(stream, config);
}

void InkplateDevConsole::begin(Stream& stream, const Config& config) {
  stream_ = &stream;
  config_ = config;
  keepAwake_ = config.keepAwakeDefault;
  line_.reserve(config.maxLineLength);
}

void InkplateDevConsole::poll() {
  if (stream_ == nullptr) {
    return;
  }

  while (stream_->available() > 0) {
    char ch = static_cast<char>(stream_->read());
    if (ch == '\r') {
      continue;
    }
    if (ch == '\n') {
      handleLine(line_);
      line_ = "";
      continue;
    }

    if (line_.length() < config_.maxLineLength) {
      line_ += ch;
    } else {
      line_ = "";
      printAck(*stream_, "input", false, "line too long");
    }
  }
}

bool InkplateDevConsole::consumeTap(int& x, int& y) {
  if (!tapPending_) {
    return false;
  }

  x = tapX_;
  y = tapY_;
  tapPending_ = false;
  return true;
}

void InkplateDevConsole::queueTap(int x, int y) {
  tapX_ = x;
  tapY_ = y;
  tapPending_ = true;
}

bool InkplateDevConsole::frameFromInkplate(Inkplate& display, Frame& frame, const char* format) {
  const int width = display.width();
  const int height = display.height();
  const int rowBytes = (width + 7) / 8;

  frame.width = width;
  frame.height = height;
  frame.rowBytes = rowBytes;
  frame.bytes = rowBytes * height;
  frame.data = display._partial;
  frame.format = format;

  return frame.data != nullptr;
}

void InkplateDevConsole::handleLine(String line) {
  line.trim();
  if (!line.startsWith("dev:")) {
    return;
  }

  String command = line.substring(4);
  command.trim();
  handleCommand(command);
}

void InkplateDevConsole::handleCommand(String command) {
  String lowered = command;
  lowered.toLowerCase();

  if (lowered == "help" || lowered == "?") {
    printHelp();
    return;
  }

  if (lowered == "state") {
    printState();
    return;
  }

  if (lowered == "frame") {
    printFrame();
    return;
  }

  if (lowered.startsWith("tap ")) {
    int x = 0;
    int y = 0;
    if (!parseTwoInts(command.substring(4), x, y)) {
      printAck(*stream_, "tap", false, "expected dev:tap <x> <y>");
      return;
    }
    queueTap(x, y);
    printAck(*stream_, "tap", true, String("queued ") + x + "," + y);
    return;
  }

  if (lowered.startsWith("square ") || lowered.startsWith("target ")) {
    const int space = command.indexOf(' ');
    String name = command.substring(space + 1);
    name.trim();
    name.toLowerCase();

    int x = 0;
    int y = 0;
    if (!resolveNamedPoint(name, x, y)) {
      printAck(*stream_, lowered.startsWith("square ") ? "square" : "target", false, String("unknown target: ") + name);
      return;
    }
    queueTap(x, y);
    printAck(*stream_, lowered.startsWith("square ") ? "square" : "target", true, String("queued ") + name + " at " + x + "," + y);
    return;
  }

  if (lowered == "back") {
    int x = 0;
    int y = 0;
    if (!resolveNamedPoint("back", x, y)) {
      x = 30;
      y = 30;
    }
    queueTap(x, y);
    printAck(*stream_, "back", true, String("queued ") + x + "," + y);
    return;
  }

  if (lowered.startsWith("awake ")) {
    String value = lowered.substring(6);
    value.trim();
    if (value == "on" || value == "1" || value == "true") {
      keepAwake_ = true;
      printAck(*stream_, "awake", true, "on");
      return;
    }
    if (value == "off" || value == "0" || value == "false") {
      keepAwake_ = false;
      printAck(*stream_, "awake", true, "off");
      return;
    }
    printAck(*stream_, "awake", false, "expected on or off");
    return;
  }

  if (customCommandCallback_ != nullptr && customCommandCallback_(command, *stream_, context_)) {
    return;
  }

  printAck(*stream_, "unknown", false, command);
}

void InkplateDevConsole::printState() {
  if (stateCallback_ == nullptr) {
    printAck(*stream_, "state", false, "state callback is not configured");
    return;
  }

  stream_->print("DEV_STATE ");
  stateCallback_(*stream_, context_);
  stream_->println();
}

void InkplateDevConsole::printFrame() {
  if (frameCallback_ == nullptr) {
    printAck(*stream_, "frame", false, "frame callback is not configured");
    return;
  }

  Frame frame;
  String error;
  if (!frameCallback_(frame, error, context_)) {
    if (error.isEmpty()) {
      error = "frame callback failed";
    }
    printAck(*stream_, "frame", false, error);
    return;
  }

  if (frame.data == nullptr || frame.width <= 0 || frame.height <= 0 || frame.bytes <= 0) {
    printAck(*stream_, "frame", false, "invalid frame");
    return;
  }

  stream_->print("DEV_FRAME_BEGIN {\"width\":");
  stream_->print(frame.width);
  stream_->print(",\"height\":");
  stream_->print(frame.height);
  stream_->print(",\"rowBytes\":");
  stream_->print(frame.rowBytes > 0 ? frame.rowBytes : (frame.width + 7) / 8);
  stream_->print(",\"bytes\":");
  stream_->print(frame.bytes);
  stream_->print(",\"encoding\":\"hex\",\"format\":");
  printJsonString(*stream_, frame.format == nullptr ? "1bpp-lsb-black1" : frame.format);
  stream_->println("}");

  const int chunkBytes = config_.frameChunkBytes == 0 ? 64 : config_.frameChunkBytes;
  for (int offset = 0; offset < frame.bytes; offset += chunkBytes) {
    const int count = min(chunkBytes, frame.bytes - offset);
    printFrameChunk(frame.data, offset, count);
    delay(1);
  }

  stream_->println("DEV_FRAME_END");
}

void InkplateDevConsole::printHelp() {
  stream_->print("DEV_HELP dev:state | dev:frame | dev:tap <x> <y> | dev:square <name> | dev:back | dev:awake <on|off>");
  if (config_.extraHelp != nullptr && config_.extraHelp[0] != '\0') {
    stream_->print(" | ");
    stream_->print(config_.extraHelp);
  }
  stream_->println();
}

bool InkplateDevConsole::parseTwoInts(const String& value, int& x, int& y) const {
  char tail = '\0';
  return sscanf(value.c_str(), "%d %d %c", &x, &y, &tail) == 2;
}

bool InkplateDevConsole::resolveNamedPoint(const String& name, int& x, int& y) {
  if (namedPointCallback_ == nullptr) {
    return false;
  }
  return namedPointCallback_(name, x, y, context_);
}

void InkplateDevConsole::printFrameChunk(const uint8_t* data, int offset, int count) {
  static const char hex[] = "0123456789abcdef";
  stream_->print("DEV_FRAME ");
  for (int i = 0; i < count; ++i) {
    const uint8_t b = data[offset + i];
    stream_->write(hex[b >> 4]);
    stream_->write(hex[b & 0x0f]);
  }
  stream_->println();
}

void InkplateDevConsole::printAck(Print& out, const String& command, bool ok, const String& message) {
  out.print("DEV_ACK {\"command\":");
  printJsonString(out, command);
  out.print(",\"ok\":");
  out.print(ok ? "true" : "false");
  if (!message.isEmpty()) {
    out.print(",\"message\":");
    printJsonString(out, message);
  }
  out.println("}");
}

void InkplateDevConsole::printJsonString(Print& out, const String& value) {
  out.write('"');
  for (size_t i = 0; i < value.length(); ++i) {
    const char ch = value.charAt(i);
    const uint8_t uch = static_cast<uint8_t>(ch);
    switch (ch) {
      case '"':
        out.print("\\\"");
        break;
      case '\\':
        out.print("\\\\");
        break;
      case '\n':
        out.print("\\n");
        break;
      case '\r':
        out.print("\\r");
        break;
      case '\t':
        out.print("\\t");
        break;
      default:
        if (uch < 0x20) {
          out.print("\\u00");
          static const char hex[] = "0123456789abcdef";
          out.write(hex[(uch >> 4) & 0x0f]);
          out.write(hex[uch & 0x0f]);
        } else {
          out.write(ch);
        }
        break;
    }
  }
  out.write('"');
}
