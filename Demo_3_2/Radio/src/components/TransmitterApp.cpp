#include "components/TransmitterApp.h"
#include <esp_system.h>
#include <cstring>
namespace csi {
void TransmitterApp::releaseBatch() {
    radio_.discardProbes();
    dumped_=0;
    dumping_=false;
}
void TransmitterApp::dumpNext() {
    if (dumped_<received_) {
        if (!host_.writeFrame(sample_)) { error(ErrorCode::WriteFailed); return; }
        ++dumped_;
    }
    if (dumped_==received_) {
        if (!host_.writeFrame(Protocol::frame(Message::Done,batch_+received_,&summary_))) {
            error(ErrorCode::WriteFailed);
            return;
        }
        active_=armed_=false;
        releaseBatch();
    }
}
bool TransmitterApp::begin() {
    esp_err_t result=radio_.begin(); fault_=result!=ESP_OK;
    if (fault_) host_.writeLine(String("Transmitter initialization failed: ")+esp_err_to_name(result));
    return !fault_;
}
void TransmitterApp::hostDisconnected() {
    if (active_ && radio_.connected()) radio_.send(Protocol::frame(Message::Cancel,batch_));
    active_=armed_=hostReady_=hostWaiting_=false;
    releaseBatch(); host_.resetInput();
}
void TransmitterApp::newSession() {
    uint32_t previous=session_;
    do { session_=esp_random(); } while (!session_ || session_==previous);
    receiverReady_=false;
    if (radio_.connected()) {
        uint8_t mac[6]; WiFi.macAddress(mac);
        radio_.send(Protocol::frame(Message::Peer,session_,mac));
        radio_.send(Protocol::frame(Message::Welcome,session_));
    }
}
void TransmitterApp::error(ErrorCode code) {
    uint16_t value=static_cast<uint16_t>(code);
    Serial.printf("[TX %lu ms] ERROR=%u request=%lu active=%u armed=%u received=%lu/%lu WiFi=%d receiverTCP=%u\n",
        millis(), value, batch_, active_, armed_, received_, request_.count,
        WiFi.status(), radio_.connected());
    // The laptop has seen no samples during acquisition; during forwarding it has
    // seen only dumped_ samples, not the full received_ count.
    if (active_ && armed_) host_.writeFrame(Protocol::frame(Message::Error,batch_+dumped_,&value));
    else host_.writeLine(String("ERROR ")+value);
    if (active_ && !dumping_ && radio_.connected()) radio_.send(Protocol::frame(Message::Cancel,batch_));
    active_=armed_=false;
    releaseBatch();
}
void TransmitterApp::receiverLost(ErrorCode reason) {
    Serial.printf("[TX %lu ms] Receiver lost: reason=%u last_traffic=%lu ms ago\n",
        millis(), static_cast<unsigned>(reason), millis()-lastHeard_);
    // A completed batch no longer depends on the receiver: finish its TCP response.
    if (active_ && !dumping_) error(reason);
    connected_=receiverReady_=hostReady_=false; radio_.disconnect();
}
void TransmitterApp::hostCommand(const String& line) {
    if (line=="READY") {
        // Python may retry while waiting. Keep the current handshake rather than
        // continually replacing its session before the receiver can acknowledge.
        if (hostWaiting_) return;
        Serial.printf("[TX %lu ms] Laptop READY; starting receiver handshake\n", millis());
        if (active_) radio_.send(Protocol::frame(Message::Cancel,batch_));
        active_=armed_=hostReady_=false; hostWaiting_=true;
        releaseBatch();
        newSession(); return;
    }
    if (active_) { error(ErrorCode::Busy); return; }
    int a=line.indexOf(' ',8), b=a<0 ? -1 : line.indexOf(' ',a+1);
    uint32_t count,mask,timeout;
    if (!line.startsWith("COLLECT ") || a<0 || b<0 ||
        !Protocol::parseNumber(line.substring(8,a),count) ||
        !Protocol::parseNumber(line.substring(a+1,b),mask) ||
        !Protocol::parseNumber(line.substring(b+1),timeout) || count!=1 ||
        mask>7 || !Protocol::csiBytes(mask) || timeout<100 || timeout>600000) {
        error(ErrorCode::InvalidCommand); return;
    }
    if (!hostReady_ || !receiverReady_ || !radio_.connected()) { error(ErrorCode::NotReady); return; }
    // Reserve a new ID range so buffered results from an older batch are rejected.
    batch_=1+esp_random()%(UINT32_MAX-count-1);
    request_={count,timeout,static_cast<uint8_t>(mask)};
    received_=0; active_=true; armed_=false; started_=millis();
    Serial.printf("[TX %lu ms] Sending START id=%lu mask=%u timeout=%lu ms\n",
        millis(), batch_, request_.requiredMask, request_.timeoutMs);
    if (!radio_.send(Protocol::frame(Message::Start,batch_,&request_))) error(ErrorCode::WriteFailed);
}
void TransmitterApp::receiverMessage(const Frame& frame) {
    if (frame.header.code==static_cast<uint8_t>(Message::Hello)) {
        lastHeard_=millis(); radio_.send(Protocol::frame(Message::Welcome,session_)); return;
    }
    if (frame.header.code==static_cast<uint8_t>(Message::Ready)) {
        if (frame.header.packetId!=session_) return;
        if (!receiverReady_)
            Serial.printf("[TX %lu ms] Receiver READY session=%lu\n", millis(), session_);
        lastHeard_=millis(); receiverReady_=true;
        if (hostWaiting_) {
            hostWaiting_=false; hostReady_=true;
            host_.writeLine("\nREADY"); // Line boundary also separates any cancelled prior binary stream.
        }
        return;
    }
    if (!active_ || dumping_) return;
    if (Protocol::isSample(frame.header.code)) {
        if (!armed_) return;
        if (frame.header.packetId!=batch_+received_ || received_>=request_.count) {
            error(ErrorCode::Sequence); return;
        }
        if ((frame.header.code & request_.requiredMask)!=request_.requiredMask) {
            error(ErrorCode::InvalidLayout); return;
        }
        lastHeard_=millis();
        // Retain only this measurement until the receiver confirms DONE.
        sample_=frame;
        ++received_;
        radio_.discardProbes();
        return;
    }
    switch (static_cast<Message>(frame.header.code)) {
    case Message::Armed:
        if (frame.header.packetId!=batch_) return;
        if (armed_ || memcmp(frame.payload,&request_,sizeof(request_))!=0) { error(ErrorCode::InvalidFrame); return; }
        lastHeard_=millis(); armed_=true;
        host_.writeLine(String("BATCH ")+batch_+" "+request_.count+" "+request_.requiredMask);
        // One application probe for this request. Wait for CSI/DONE or timeout;
        // TCP itself handles transport retransmissions. No timed probe loop.
        if (!radio_.send(Protocol::frame(Message::Probe,batch_))) {
            error(ErrorCode::WriteFailed); return;
        }
        Serial.printf("[TX %lu ms] Receiver ARMED id=%lu; single PROBE queued, waiting for CSI/DONE\n",
            millis(), batch_);
        break;
    case Message::Done: {
        if (frame.header.packetId!=batch_+received_) { error(ErrorCode::Sequence); return; }
        BatchSummary summary; memcpy(&summary,frame.payload,sizeof(summary));
        if (received_!=request_.count || summary.count!=received_) { error(ErrorCode::Sequence); return; }
        lastHeard_=millis();
        summary_=summary;
        radio_.discardProbes();
        dumped_=0;
        dumping_=true;
        break;
    }
    case Message::Error:
        if (frame.header.packetId!=batch_+received_) return;
        {
            uint16_t code; memcpy(&code,frame.payload,2);
            error(static_cast<ErrorCode>(code));
        }
        break;
    default: break;
    }
}
void TransmitterApp::tick() {
    String line;
    LineResult input=host_.readLine(line);
    if (input==LineResult::Complete) {
        // Human diagnostics are idle-only; text must never interrupt a CSI binary stream.
        if (!active_ && host_.respondAlive(line,"transmitter")) {}
        else if (!fault_) hostCommand(line);
    } else if (input==LineResult::TooLong) error(ErrorCode::InvalidCommand);
    if (fault_) return;
    if (connected_ && !radio_.connected()) receiverLost();
    if (radio_.acceptReceiver()) { connected_=true; lastHeard_=millis(); newSession(); }
    Frame frame;
    ReadResult result=radio_.read(frame);
    if (result==ReadResult::Complete) receiverMessage(frame);
    else if (result==ReadResult::Invalid) { error(ErrorCode::InvalidFrame); receiverLost(); }
    radio_.tick(); // Flush one pending control/probe frame without waiting on TCP.
    if (connected_ && millis()-lastHeard_>=5000) receiverLost(ErrorCode::HeartbeatTimeout);
    if (!active_) return;
    if (dumping_) { dumpNext(); return; }
    if (millis()-started_>=request_.timeoutMs+2000) { error(ErrorCode::Timeout); return; }
}
} // namespace csi
