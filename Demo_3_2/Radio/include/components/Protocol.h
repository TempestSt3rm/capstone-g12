#pragma once
#include <Arduino.h>
#include "FrameTypes.h"

namespace csi {
enum class ReadResult { None, Complete, Invalid };
class Protocol {
public:
    static bool parseNumber(const String& text,uint32_t& number);
    static bool isSample(uint8_t code) { return code>=1 && code<=7; }
    static uint16_t csiBytes(uint8_t mask);
    static int payloadBytes(uint8_t code);
    static Frame frame(uint8_t code,uint32_t id=0,const void* payload=nullptr);
    static Frame frame(Message type,uint32_t id=0,const void* payload=nullptr) {
        return frame(static_cast<uint8_t>(type),id,payload);
    }
    static bool write(Stream& stream,const Frame& frame);
};
class FrameReader {
public:
    ReadResult read(Stream& stream,Frame& frame);
    void reset() { used_=0; }
private:
    uint8_t bytes_[sizeof(Header)+MaxPayload]={};
    size_t used_=0;
};
} // namespace csi
