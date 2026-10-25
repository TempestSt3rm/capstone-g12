#pragma once
#include "CsiCapture.h"
#include "RadioLink.h"

namespace csi {
struct ReceiverSettings {
    uint32_t handshakeIntervalMs=1000;
    uint32_t linkTimeoutMs=5000;
    uint32_t sampleSendIntervalUs=5000; // Service the link between CSI sends.
};
class ReceiverApp {
public:
    ReceiverApp(DirectWifiLink& link, CsiCapture& capture, const ReceiverSettings& settings)
        : link_(link), capture_(capture), settings_(settings) {}
    bool begin();
    void tick();
private:
    void reset();
    void command(const Frame& frame);
    void error(ErrorCode code);
    DirectWifiLink& link_;
    CsiCapture& capture_;
    ReceiverSettings settings_;
    bool fault_=false, connected_=false, haveSession_=false, collecting_=false;
    uint32_t session_=0, batch_=0, token_=0, sent_=0, started_=0;
    uint32_t lastHello_=0, lastHeard_=0;
    uint32_t lastSampleSend_=0;
    BatchRequest request_ = {};
};
} // namespace csi
