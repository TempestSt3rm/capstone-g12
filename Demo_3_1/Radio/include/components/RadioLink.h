#pragma once
#include <WiFi.h>
#include <esp_wifi.h>
#include "Protocol.h"

namespace csi {
struct RadioSettings {
    const char* ssid;
    const char* password;
    uint8_t channel;
    uint16_t port;
};

// Small outgoing FIFO. Each service call makes one nonblocking socket write;
// a full socket never holds up command reads, heartbeats, or serial diagnostics.
class TcpFrameWriter {
public:
    bool enqueue(const Frame& frame);
    bool service(WiFiClient& client);
    bool canSendData() const { return count_ < Capacity - 2; }
    void reset() { head_=count_=0; offset_=0; }
private:
    static constexpr uint8_t Capacity=8;
    Frame frames_[Capacity];
    uint8_t head_=0, count_=0;
    size_t offset_=0;
    uint32_t lastProgress_=0;
};

// Moving receiver: reconnects to the fixed transmitter's AP and TCP listener.
class DirectWifiLink {
public:
    explicit DirectWifiLink(const RadioSettings& settings)
        : settings_(settings) {}
    esp_err_t begin();
    void tick();
    bool connected();
    void disconnect();
    void peerMac(uint8_t mac[6]) const;
    ReadResult read(Frame& frame);
    bool send(const Frame& frame);
    bool canSendData() const { return writer_.canSendData(); }
private:
    RadioSettings settings_;
    WiFiClient client_;
    FrameReader reader_;
    TcpFrameWriter writer_;
    uint32_t lastAttempt_ = 0;
    uint32_t lastWifiAttempt_ = 0;
};

// Fixed transmitter: accepts one moving receiver; USB commands live in TransmitterApp.
class DirectWifiTransmitter {
public:
    explicit DirectWifiTransmitter(const RadioSettings& settings)
        : settings_(settings), server_(settings.port) {}
    esp_err_t begin();
    void tick();
    bool acceptReceiver();
    bool connected();
    void disconnect();
    ReadResult read(Frame& frame);
    bool send(const Frame& frame);
    bool canSendData() const { return writer_.canSendData(); }
private:
    RadioSettings settings_;
    WiFiServer server_;
    WiFiClient client_;
    FrameReader reader_;
    TcpFrameWriter writer_;
};
} // namespace csi
