#include "components/Protocol.h"
#include <cstring>
namespace csi {
bool Protocol::parseNumber(const String& text,uint32_t& number) {
    if (!text.length() || text.length()>10) return false;
    uint64_t value=0;
    for (size_t i=0;i<text.length();++i) {
        if (text[i]<'0' || text[i]>'9') return false;
        value=value*10+(text[i]-'0');
        if (value>UINT32_MAX) return false;
    }
    number=static_cast<uint32_t>(value); return true;
}
uint16_t Protocol::csiBytes(uint8_t mask) {
    if (!isSample(mask)) return 0;
    return BytesPerLtf*((mask&1)+((mask>>1)&1)+((mask>>2)&1));
}
int Protocol::payloadBytes(uint8_t code) {
    if (isSample(code)) return csiBytes(code);
    switch (static_cast<Message>(code)) {
    case Message::Hello: case Message::Welcome: case Message::Ready: case Message::Cancel: return 0;
    case Message::Start: case Message::Armed: return sizeof(BatchRequest);
    case Message::Probe: return 128;
    case Message::Done: return sizeof(BatchSummary);
    case Message::Error: return sizeof(uint16_t);
    default: return -1;
    }
}
Frame Protocol::frame(uint8_t code,uint32_t id,const void* payload) {
    Frame result; result.header.code=code; result.header.packetId=id;
    int size=payloadBytes(code);
    if (payload && size>0) memcpy(result.payload,payload,size);
    return result;
}
bool Protocol::write(Stream& stream,const Frame& frame) {
    int payloadSize=payloadBytes(frame.header.code);
    if (payloadSize<0) return false;
    uint8_t wire[sizeof(Header)+MaxPayload];
    size_t size=sizeof(Header)+payloadSize;
    memcpy(wire,&frame.header,sizeof(Header));
    memcpy(wire+sizeof(Header),frame.payload,payloadSize);
    size_t offset=0;
    uint32_t started=millis();
    while (offset<size) {
        offset+=stream.write(wire+offset,size-offset);
        if (offset==size) return true;
        if (millis()-started>=2000) return false;
        yield();
    }
    return true;
}
ReadResult FrameReader::read(Stream& stream,Frame& frame) {
    for (size_t n=0;n<4096 && stream.available();++n) {
        bytes_[used_++]=static_cast<uint8_t>(stream.read());
        int payloadSize=Protocol::payloadBytes(bytes_[0]);
        if (payloadSize<0) { reset(); return ReadResult::Invalid; }
        if (used_<sizeof(Header)+static_cast<size_t>(payloadSize)) continue;
        memcpy(&frame.header,bytes_,sizeof(Header));
        memcpy(frame.payload,bytes_+sizeof(Header),payloadSize);
        reset(); return ReadResult::Complete;
    }
    return ReadResult::None;
}
} // namespace csi
