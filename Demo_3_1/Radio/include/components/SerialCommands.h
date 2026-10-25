#pragma once
#include <Arduino.h>
#include "Protocol.h"

namespace csi {
enum class LineResult { None, Complete, TooLong };

// Bounded line framing for USB commands and the ESP32 TCP connection.
class SerialCommands {
public:
    explicit SerialCommands(Stream& stream, size_t maxLineBytes = 512)
        : stream_(stream), maxLineBytes_(maxLineBytes) {}
    LineResult readLine(String& line);
    bool writeLine(const String& line);
    bool writeFrame(const Frame& frame);
    bool respondAlive(const String& line, const char* role);
    void resetInput();
private:
    static constexpr size_t ReadBudget = 512;
    Stream& stream_;
    size_t maxLineBytes_;
    String pending_;
    bool overflow_ = false;
};
} // namespace csi
