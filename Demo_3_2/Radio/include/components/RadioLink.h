#pragma once
#include <WiFi.h>
#include <esp_wifi.h>
#include "Protocol.h"
#include "TcpFrameQueue.h"

namespace csi {
struct RadioSettings {
    const char* ssid;
    const char* password;
    uint8_t channel;
    uint16_t port;
    IPAddress transmitterIp;
    IPAddress gateway;
    IPAddress subnet;
};

// Small outgoing FIFO. Each service call makes one nonblocking socket write;
// a full socket never holds up command reads, heartbeats, or serial diagnostics.
class TcpFrameWriter {
public:
    bool enqueue(const Frame& frame);
    bool service(WiFiClient& client);
    bool canSendData() const { return queue_.canSendData(); }
    void discardProbes() { queue_.discardProbes(); }
    void reset() { queue_.reset(); }
private:
    TcpFrameQueue queue_;
    uint32_t lastProgress_=0;
};

// Moving receiver: joins the laptop hotspot and connects to the transmitter.
class DirectWifiLink {
public:
    explicit DirectWifiLink(const RadioSettings& settings)
        : settings_(settings) {}
    esp_err_t begin();
    void tick();
    bool connected();
    void disconnect();
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

// Fixed transmitter: hotspot station with a TCP listener for one moving receiver.
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
    void discardProbes() { writer_.discardProbes(); }
private:
    RadioSettings settings_;
    WiFiServer server_;
    WiFiClient client_;
    FrameReader reader_;
    TcpFrameWriter writer_;
};
} // namespace csi
