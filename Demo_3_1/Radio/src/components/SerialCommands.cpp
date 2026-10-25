#include "components/SerialCommands.h"

namespace csi {
LineResult SerialCommands::readLine(String& line) {
    // Bound work per call so input cannot starve measurement timeouts.
    for (size_t n = 0; n < ReadBudget && stream_.available(); ++n) {
        char c = static_cast<char>(stream_.read());
        if (c == '\n') {
            bool overflow = overflow_;
            line = pending_;
            line.trim();
            resetInput();
            return overflow ? LineResult::TooLong : LineResult::Complete;
        }
        if (!overflow_ && pending_.length() < maxLineBytes_) pending_ += c;
        else overflow_ = true;
    }
    return LineResult::None;
}
bool SerialCommands::writeLine(const String& line) {
    String wire = line + '\n';
    size_t offset = 0;
    uint32_t started = millis();
    while (offset < wire.length()) {
        offset += stream_.write(reinterpret_cast<const uint8_t*>(wire.c_str()) + offset,
                                wire.length() - offset);
        if (offset == wire.length()) return true;
        if (millis() - started >= 2000) return false;
        yield();
    }
    return true;
}
bool SerialCommands::writeFrame(const Frame& frame) {
    // Serial-only framing. The board-to-board protocol stays unchanged.
    static const char marker[] = "CSI_DATA:";
    size_t offset = 0;
    uint32_t started = millis();
    while (offset < sizeof(marker) - 1) {
        offset += stream_.write(reinterpret_cast<const uint8_t*>(marker) + offset,
                                sizeof(marker) - 1 - offset);
        if (offset < sizeof(marker) - 1 && millis() - started >= 2000) return false;
        yield();
    }
    return Protocol::write(stream_, frame);
}
bool SerialCommands::respondAlive(const String& line, const char* role) {
    if (!line.equalsIgnoreCase("alive")) return false;
    writeLine(String("ALIVE ") + role);
    return true;
}
void SerialCommands::resetInput() {
    pending_ = "";
    overflow_ = false;
}
} // namespace csi
