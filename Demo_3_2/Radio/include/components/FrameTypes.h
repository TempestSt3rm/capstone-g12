#pragma once
#include <cstddef>
#include <cstdint>

namespace csi {
constexpr uint16_t BytesPerLtf=128; // 64 signed imaginary/real pairs per HT20 training field.
constexpr size_t MaxPayload=3*BytesPerLtf;
// CSI packets use codes 1..7 directly as their three-bit LTF mask.
// Codes with the high bit set are fixed-size control messages between boards.
enum class Message : uint8_t { Hello=0x80, Welcome, Ready, Start, Armed, Probe, Done, Error, Cancel, Peer };
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
} // namespace csi
