#pragma once
#include "FrameTypes.h"

namespace csi {
// Frames keep FIFO order. The application queues one probe per measurement;
// ordinary heartbeat/control traffic must never evict that probe.
class TcpFrameQueue {
public:
    bool enqueue(const Frame& frame) {
        const uint8_t code=frame.header.code;
        // Repeated heartbeats must not consume every slot while TCP is slow.
        if (code==static_cast<uint8_t>(Message::Hello) ||
            code==static_cast<uint8_t>(Message::Welcome) ||
            code==static_cast<uint8_t>(Message::Ready)) {
            for (uint8_t i=0;i<count_;++i)
                if (at(i).header.code==code && at(i).header.packetId==frame.header.packetId)
                    return true;
        }
        if (count_==Capacity) return false;
        at(count_++)=frame;
        return true;
    }
    void discardProbes() {
        uint8_t kept=0;
        for (uint8_t i=0;i<count_;++i) {
            // A partially written TCP frame MUST finish; dropping its remainder
            // would corrupt every following frame on the byte stream.
            if (at(i).header.code==static_cast<uint8_t>(Message::Probe) && !(i==0 && offset_)) continue;
            if (kept!=i) at(kept)=at(i);
            ++kept;
        }
        count_=kept;
        if (!count_) offset_=0;
    }
    bool empty() const { return count_==0; }
    uint8_t size() const { return count_; }
    bool canSendData() const { return count_<Capacity-2; }
    const Frame& front() const { return frames_[head_]; }
    size_t offset() const { return offset_; }
    void consume(size_t bytes, size_t frameBytes) {
        offset_+=bytes;
        if (offset_==frameBytes) {
            offset_=0; head_=(head_+1)%Capacity; --count_;
        }
    }
    void reset() { head_=count_=0; offset_=0; }
private:
    Frame& at(uint8_t index) { return frames_[(head_+index)%Capacity]; }
    static constexpr uint8_t Capacity=8;
    Frame frames_[Capacity];
    uint8_t head_=0, count_=0;
    size_t offset_=0;
};
} // namespace csi
