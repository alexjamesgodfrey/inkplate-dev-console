#pragma once

#include <Arduino.h>

class Inkplate;

class InkplateDevConsole {
 public:
  struct Config {
    size_t maxLineLength = 160;
    uint16_t frameChunkBytes = 64;
    bool keepAwakeDefault = true;
    const char* extraHelp = nullptr;
  };

  struct Frame {
    int width = 0;
    int height = 0;
    int rowBytes = 0;
    int bytes = 0;
    const uint8_t* data = nullptr;
    const char* format = "1bpp-lsb-black1";
  };

  using StateCallback = void (*)(Print& out, void* context);
  using FrameCallback = bool (*)(Frame& frame, String& error, void* context);
  using NamedPointCallback = bool (*)(const String& name, int& x, int& y, void* context);
  using CustomCommandCallback = bool (*)(const String& command, Print& out, void* context);

  InkplateDevConsole() = default;

  void begin(Stream& stream);
  void begin(Stream& stream, const Config& config);
  void poll();

  void setContext(void* context) { context_ = context; }
  void setStateCallback(StateCallback callback) { stateCallback_ = callback; }
  void setFrameCallback(FrameCallback callback) { frameCallback_ = callback; }
  void setNamedPointCallback(NamedPointCallback callback) { namedPointCallback_ = callback; }
  void setCustomCommandCallback(CustomCommandCallback callback) { customCommandCallback_ = callback; }

  bool consumeTap(int& x, int& y);
  void queueTap(int x, int y);

  bool keepAwake() const { return keepAwake_; }
  void setKeepAwake(bool enabled) { keepAwake_ = enabled; }

  static bool frameFromInkplate(Inkplate& display, Frame& frame, const char* format = "1bpp-lsb-black1");
  static void printAck(Print& out, const String& command, bool ok, const String& message = "");
  static void printJsonString(Print& out, const String& value);

 private:
  void handleLine(String line);
  void handleCommand(String command);
  void printState();
  void printFrame();
  void printHelp();
  bool parseTwoInts(const String& value, int& x, int& y) const;
  bool resolveNamedPoint(const String& name, int& x, int& y);
  void printFrameChunk(const uint8_t* data, int offset, int count);

  Stream* stream_ = nullptr;
  Config config_;
  String line_;
  void* context_ = nullptr;

  StateCallback stateCallback_ = nullptr;
  FrameCallback frameCallback_ = nullptr;
  NamedPointCallback namedPointCallback_ = nullptr;
  CustomCommandCallback customCommandCallback_ = nullptr;

  bool tapPending_ = false;
  int tapX_ = 0;
  int tapY_ = 0;
  bool keepAwake_ = true;
};
