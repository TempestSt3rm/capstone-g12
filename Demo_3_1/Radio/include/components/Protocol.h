#pragma once
#include <Arduino.h>

namespace csi {
constexpr uint16_t BytesPerLtf=128; // 64 signed imaginary/real pairs per HT20 training field.
constexpr size_t MaxPayload=3*BytesPerLtf;
// CSI packets use codes 1..7 directly as their three-bit LTF mask.
// Codes with the high bit set are fixed-size control messages between boards.
enum class Message : uint8_t { Hello=0x80, Welcome, Ready, Start, Armed, Probe, Done, Error, Cancel };
enum class ErrorCode : uint16_t { InvalidCommand=1, NotReady, Busy, Timeout, Disconnected,
    InvalidFrame, Sequence, InvalidLayout, WriteFailed, NoMemory, HeartbeatTimeout };
#pragma pack(push,1)
struct Header { uint8_t code=0; uint32_t packetId=0; };
struct BatchRequest { uint32_t count; uint32_t timeoutMs; uint8_t requiredMask; };
struct BatchSummary { uint32_t count; uint32_t dropped; };
#pragma pack(pop)
static_assert(sizeof(Header)==5,"CSI header must be exactly mask + uint32 packet ID");
static_assert(sizeof(BatchRequest)==9,"Batch request layout changed");
static_assert(sizeof(BatchSummary)==8,"Batch summary layout changed");
struct Frame { Header header; uint8_t payload[MaxPayload]={}; };
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
