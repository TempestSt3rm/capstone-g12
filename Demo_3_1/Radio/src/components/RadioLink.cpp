#include "components/RadioLink.h"
#include <cstring>
#include <lwip/sockets.h>
#include <errno.h>

namespace csi {
bool TcpFrameWriter::enqueue(const Frame& frame) {
    if (count_==Capacity) return false;
    if (!count_) lastProgress_=millis();
    frames_[(head_+count_)%Capacity]=frame;
    ++count_;
    return true;
}
bool TcpFrameWriter::service(WiFiClient& client) {
    if (!count_) return true;
    const Frame& frame=frames_[head_];
    int payloadSize=Protocol::payloadBytes(frame.header.code);
    if (payloadSize<0 || client.fd()<0) return false;
    uint8_t wire[sizeof(Header)+MaxPayload];
    size_t size=sizeof(Header)+payloadSize;
    memcpy(wire,&frame.header,sizeof(Header));
    memcpy(wire+sizeof(Header),frame.payload,payloadSize);
    // Bypass WiFiClient::write(), whose internal retry loop can wait for seconds.
    int written=lwip_send(client.fd(),wire+offset_,size-offset_,MSG_DONTWAIT);
    if (written>0) {
        offset_+=static_cast<size_t>(written);
        lastProgress_=millis();
        if (offset_==size) { offset_=0; head_=(head_+1)%Capacity; --count_; }
        return true;
    }
    if (written==0) return false;
    if (errno!=EAGAIN && errno!=EWOULDBLOCK && errno!=EINTR) return false;
    return millis()-lastProgress_<5000;
}
namespace {
esp_err_t configureRadio(wifi_interface_t interface) {
    const char* role = interface == WIFI_IF_AP ? "transmitter/AP" : "receiver/STA";
    // The SDK supports B, BG, or BGN; the 11N bit alone is not a valid mode.
    esp_err_t error = esp_wifi_set_protocol(interface,
        WIFI_PROTOCOL_11B | WIFI_PROTOCOL_11G | WIFI_PROTOCOL_11N);
    if (error != ESP_OK) {
        Serial.printf("%s: esp_wifi_set_protocol(BGN) failed: %s\n", role, esp_err_to_name(error));
        return error;
    }
    error = esp_wifi_set_bandwidth(interface, WIFI_BW_HT20);
    if (error != ESP_OK) {
        Serial.printf("%s: esp_wifi_set_bandwidth(HT20) failed: %s\n", role, esp_err_to_name(error));
        return error;
    }
    error = esp_wifi_set_ps(WIFI_PS_NONE);
    if (error != ESP_OK)
        Serial.printf("%s: esp_wifi_set_ps(NONE) failed: %s\n", role, esp_err_to_name(error));
    return error;
}
}
esp_err_t DirectWifiLink::begin() {
    if (!WiFi.mode(WIFI_STA)) {
        Serial.println("receiver/STA: WiFi.mode failed");
        return ESP_FAIL;
    }
    WiFi.setAutoReconnect(true);
    esp_err_t error = configureRadio(WIFI_IF_STA);
    if (error == ESP_OK) WiFi.begin(settings_.ssid, settings_.password, settings_.channel);
    lastAttempt_ = millis() - 1000;
    lastWifiAttempt_ = millis();
    return error;
}
void DirectWifiLink::tick() {
    if (WiFi.status() != WL_CONNECTED) {
        disconnect();
        if (millis() - lastWifiAttempt_ >= 5000) {
            lastWifiAttempt_ = millis();
            WiFi.reconnect();
        }
        return;
    }
    if (!connected() && millis() - lastAttempt_ >= 1000) {
        disconnect();
        lastAttempt_ = millis();
        if (client_.connect(WiFi.gatewayIP(), settings_.port, 500)) client_.setNoDelay(true);
    }
    if (connected() && !writer_.service(client_)) {
        Serial.println("Receiver TCP write failed or stalled; reconnecting");
        disconnect();
    }
}
bool DirectWifiLink::connected() { return client_.connected(); }
void DirectWifiLink::disconnect() { client_.stop(); reader_.reset(); writer_.reset(); }
void DirectWifiLink::peerMac(uint8_t mac[6]) const { memcpy(mac, WiFi.BSSID(), 6); }
ReadResult DirectWifiLink::read(Frame& frame) { return reader_.read(client_, frame); }
bool DirectWifiLink::send(const Frame& frame) {
    if (connected() && writer_.enqueue(frame)) return true;
    disconnect();
    return false;
}
esp_err_t DirectWifiTransmitter::begin() {
    if (!WiFi.mode(WIFI_AP)) {
        Serial.println("transmitter/AP: WiFi.mode failed");
        return ESP_FAIL;
    }
    if (!WiFi.softAP(settings_.ssid, settings_.password, settings_.channel, false, 1)) {
        Serial.println("transmitter/AP: WiFi.softAP failed");
        return ESP_FAIL;
    }
    esp_err_t error = configureRadio(WIFI_IF_AP);
    if (error == ESP_OK) {
        server_.begin();
        server_.setNoDelay(true);
        Serial.printf("Transmitter ready: AP=%s channel=%u IP=%s TCP=%u; waiting for receiver\n",
            settings_.ssid, settings_.channel, WiFi.softAPIP().toString().c_str(), settings_.port);
    }
    return error;
}
bool DirectWifiTransmitter::acceptReceiver() {
    WiFiClient incoming = server_.available();
    if (!incoming) return false;
    if (connected()) { incoming.stop(); return false; }
    disconnect();
    client_ = incoming;
    client_.setNoDelay(true);
    return true;
}
void DirectWifiTransmitter::tick() {
    if (connected() && !writer_.service(client_)) disconnect();
}
bool DirectWifiTransmitter::connected() { return client_.connected(); }
void DirectWifiTransmitter::disconnect() { client_.stop(); reader_.reset(); writer_.reset(); }
ReadResult DirectWifiTransmitter::read(Frame& frame) { return reader_.read(client_, frame); }
bool DirectWifiTransmitter::send(const Frame& frame) {
    if (connected() && writer_.enqueue(frame)) return true;
    disconnect();
    return false;
}
} // namespace csi
