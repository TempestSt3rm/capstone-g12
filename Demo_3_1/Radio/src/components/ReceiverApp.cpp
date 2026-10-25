#include "components/ReceiverApp.h"
#include <cstring>
namespace csi {
bool ReceiverApp::begin() {
    esp_err_t result=link_.begin();
    if (result==ESP_OK) result=capture_.begin();
    fault_=result!=ESP_OK;
    if (fault_) Serial.printf("Receiver initialization failed: %s\n",esp_err_to_name(result));
    return !fault_;
}
void ReceiverApp::reset() { capture_.cancel(); connected_=haveSession_=collecting_=false; }
void ReceiverApp::error(ErrorCode code) {
    capture_.cancel(); collecting_=false;
    uint16_t value=static_cast<uint16_t>(code);
    link_.send(Protocol::frame(Message::Error,batch_+sent_,&value));
}
void ReceiverApp::command(const Frame& frame) {
    Message kind=static_cast<Message>(frame.header.code);
    if (kind==Message::Welcome) {
        lastHeard_=millis();
        if (!haveSession_ || frame.header.packetId!=session_) {
            capture_.cancel(); collecting_=false;
            session_=frame.header.packetId; haveSession_=true;
            Serial.println("Receiver handshake complete");
        }
        link_.send(Protocol::frame(Message::Ready,session_)); return;
    }
    if (!haveSession_) return;
    lastHeard_=millis();
    if (kind==Message::Cancel) { capture_.cancel(); collecting_=false; return; }
    if (kind!=Message::Start) return;
    if (collecting_) { error(ErrorCode::Busy); return; }
    batch_=frame.header.packetId; sent_=0;
    memcpy(&request_,frame.payload,sizeof(request_));
    if (!batch_ || !request_.count || request_.count>100000 ||
        request_.count>UINT32_MAX-batch_ || !Protocol::csiBytes(request_.requiredMask) ||
        request_.timeoutMs<100 || request_.timeoutMs>600000) { error(ErrorCode::InvalidCommand); return; }
    token_=capture_.start(request_.count,request_.requiredMask);
    started_=millis(); collecting_=true;
    lastSampleSend_=micros()-settings_.sampleSendIntervalUs;
    link_.send(Protocol::frame(Message::Armed,batch_,&request_));
}
void ReceiverApp::tick() {
    if (fault_) return;
    if (!link_.connected() && connected_) reset();
    link_.tick();
    if (!link_.connected()) return;
    if (!connected_) {
        reset(); connected_=true;
        uint8_t mac[6]; link_.peerMac(mac); capture_.setPeer(mac);
        lastHello_=millis()-settings_.handshakeIntervalMs; lastHeard_=millis();
    }
    if (millis()-lastHello_>=settings_.handshakeIntervalMs) {
        link_.send(Protocol::frame(Message::Hello)); lastHello_=millis();
    }
    Frame frame;
    for (uint8_t n=0;n<8;++n) {
        ReadResult result=link_.read(frame);
        if (result==ReadResult::None) break;
        if (result==ReadResult::Invalid) { link_.disconnect(); reset(); return; }
        command(frame);
    }
    if (millis()-lastHeard_>=settings_.linkTimeoutMs) {
        Serial.printf("Receiver heartbeat timeout after sending %lu/%lu samples\n",
                      static_cast<unsigned long>(sent_),static_cast<unsigned long>(request_.count));
        link_.disconnect(); reset(); return;
    }
    if (!collecting_) return;
    if (millis()-started_>=request_.timeoutMs) { error(ErrorCode::Timeout); return; }
    if (!link_.canSendData() || micros()-lastSampleSend_<settings_.sampleSendIntervalUs) return;
    CsiSample sample;
    // At most one CSI frame per loop. Incoming commands were serviced first.
    if (capture_.take(sample)) {
        if (sample.generation!=token_) return;
        if (sample.sequence!=sent_) { error(ErrorCode::Sequence); return; }
        frame=Protocol::frame(sample.ltfMask,batch_+sent_,sample.data);
        if (!link_.send(frame)) { reset(); return; }
        lastSampleSend_=micros();
        if (++sent_==request_.count) {
            capture_.cancel(); collecting_=false;
            BatchSummary summary={sent_,capture_.dropped()};
            link_.send(Protocol::frame(Message::Done,batch_+sent_,&summary)); return;
        }
    }
}
} // namespace csi
