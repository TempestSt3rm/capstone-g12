#pragma once
#include "RadioLink.h"
#include "SerialCommands.h"

namespace csi {
class TransmitterApp {
public:
    TransmitterApp(SerialCommands& host, DirectWifiTransmitter& radio, uint32_t probeIntervalUs)
        : host_(host),radio_(radio),probeIntervalUs_(probeIntervalUs) {}
    bool begin();
    void tick();
private:
    void hostCommand(const String& line);
    void receiverMessage(const Frame& frame);
    void newSession();
    void error(ErrorCode code);
    void receiverLost(ErrorCode reason=ErrorCode::Disconnected);
    bool allocateBatch(uint32_t count);
    void releaseBatch();
    void dumpNext();
    SerialCommands& host_;
    DirectWifiTransmitter& radio_;
    uint32_t probeIntervalUs_;
    bool fault_=false, connected_=false, receiverReady_=false, hostWaiting_=false;
    bool hostReady_=false, active_=false, armed_=false;
    uint32_t session_=0,batch_=0,received_=0,started_=0,lastProbe_=0,lastHeard_=0;
    BatchRequest request_ = {};
    Frame* samples_ = nullptr;
    BatchSummary summary_ = {};
    uint32_t dumped_ = 0;
    bool dumping_ = false;
};
} // namespace csi
