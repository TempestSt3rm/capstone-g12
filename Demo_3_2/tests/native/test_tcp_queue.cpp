#include "components/TcpFrameQueue.h"
#include <cassert>
#include <cstring>
#include <iostream>
#include <vector>

using namespace csi;
Frame frame(Message code, uint32_t id) {
    Frame result;
    result.header.code=static_cast<uint8_t>(code);
    result.header.packetId=id;
    return result;
}
size_t payloadBytes(uint8_t code) {
    switch (static_cast<Message>(code)) {
    case Message::Probe: return 128;
    case Message::Start: return sizeof(BatchRequest);
    default: return 0; // Remaining controls exercised here have no payload.
    }
}
std::vector<uint8_t> wire(const Frame& frame) {
    std::vector<uint8_t> bytes(sizeof(Header)+payloadBytes(frame.header.code));
    std::memcpy(bytes.data(), &frame.header, sizeof(Header));
    std::memcpy(bytes.data()+sizeof(Header), frame.payload, bytes.size()-sizeof(Header));
    return bytes;
}
void drain(TcpFrameQueue& queue, std::vector<uint8_t>& output, size_t chunk=3) {
    while (!queue.empty()) {
        auto bytes=wire(queue.front());
        size_t n=std::min(chunk,bytes.size()-queue.offset());
        output.insert(output.end(),bytes.begin()+queue.offset(),bytes.begin()+queue.offset()+n);
        queue.consume(n,bytes.size());
    }
}
void pop(TcpFrameQueue& queue) {
    queue.consume(wire(queue.front()).size()-queue.offset(),wire(queue.front()).size());
}
int main() {
    // A single probe must survive delayed TCP writes and repeated heartbeats.
    TcpFrameQueue queue;
    assert(queue.enqueue(frame(Message::Probe, 42)));
    for (int i=0;i<1000;++i) {
        assert(queue.enqueue(frame(Message::Welcome, 123)));
    }
    assert(queue.enqueue(frame(Message::Start, 43)));
    assert(queue.size()<=3);
    bool foundStart=false, foundProbe=false;
    while (!queue.empty()) {
        if (queue.front().header.code==static_cast<uint8_t>(Message::Start)) foundStart=true;
        if (queue.front().header.code==static_cast<uint8_t>(Message::Probe)) foundProbe=true;
        pop(queue);
    }
    assert(foundStart && foundProbe);

    // An unsent probe can be cancelled explicitly when its request ends.
    assert(queue.enqueue(frame(Message::Probe,1)));
    queue.discardProbes();
    assert(queue.empty());

    // Never truncate a probe whose first bytes are already on TCP. Remaining
    // bytes must precede CANCEL/START, even when new controls remove other probes.
    Frame probe=frame(Message::Probe, 50);
    Frame cancel=frame(Message::Cancel, 50);
    Frame start=frame(Message::Start, 51);
    auto expected=wire(probe);
    auto cancelBytes=wire(cancel), startBytes=wire(start);
    expected.insert(expected.end(),cancelBytes.begin(),cancelBytes.end());
    expected.insert(expected.end(),startBytes.begin(),startBytes.end());
    auto probeBytes=wire(probe);
    std::vector<uint8_t> actual(probeBytes.begin(),probeBytes.begin()+3);
    assert(queue.enqueue(probe));
    queue.consume(3,probeBytes.size());
    queue.discardProbes();
    assert(queue.enqueue(cancel));
    assert(queue.enqueue(start));
    drain(queue,actual,2);
    assert(actual==expected);

    // Unsent probes may be removed across a ring-buffer wrap without dropping
    // or reordering essential frames.
    queue.reset();
    for (int i=1;i<=8;++i) assert(queue.enqueue(frame(Message::Cancel,i)));
    for (int i=0;i<6;++i) pop(queue);
    assert(queue.enqueue(frame(Message::Probe,99)));
    assert(queue.enqueue(frame(Message::Start,9)));
    assert(queue.size()==4); // Control traffic has not silently evicted the probe.
    queue.discardProbes();
    assert(queue.size()==3);
    for (int id : {7,8,9}) {
        assert(queue.front().header.packetId==static_cast<uint32_t>(id));
        pop(queue);
    }

    // Essential traffic reports backpressure honestly; it is never overwritten.
    for (int i=1;i<=8;++i) assert(queue.enqueue(frame(Message::Cancel,i)));
    assert(!queue.enqueue(frame(Message::Start,9)));
    for (int id=1;id<=8;++id) {
        assert(queue.front().header.packetId==static_cast<uint32_t>(id));
        pop(queue);
    }

    // Coalescing applies only to the same heartbeat/session. Session transitions
    // must retain their distinct WELCOME messages and their order.
    assert(queue.enqueue(frame(Message::Welcome,100)));
    queue.consume(1,wire(queue.front()).size());
    assert(queue.enqueue(frame(Message::Welcome,100)));
    assert(queue.enqueue(frame(Message::Welcome,101)));
    assert(queue.size()==2);
    assert(queue.front().header.packetId==100); pop(queue);
    assert(queue.front().header.packetId==101); pop(queue);
    std::cout << "TCP queue regression checks passed\n";
}
