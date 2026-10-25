#include "components/RadioLink.h"
#include <cstring>
#include <lwip/sockets.h>
#include <errno.h>

namespace csi {
bool TcpFrameWriter::enqueue(const Frame& frame) {
    bool wasEmpty=queue_.empty();
    bool accepted=queue_.enqueue(frame);
    if (wasEmpty && !queue_.empty()) lastProgress_=millis();
    return accepted;
}
bool TcpFrameWriter::service(WiFiClient& client) {
    if (queue_.empty()) return true;
    const Frame& frame=queue_.front();
    int payloadSize=Protocol::payloadBytes(frame.header.code);
    if (payloadSize<0 || client.fd()<0) return false;
    uint8_t wire[sizeof(Header)+MaxPayload];
    size_t size=sizeof(Header)+payloadSize;
    memcpy(wire,&frame.header,sizeof(Header));
    memcpy(wire+sizeof(Header),frame.payload,payloadSize);
    // Bypass WiFiClient::write(), whose internal retry loop can wait for seconds.
    int written=lwip_send(client.fd(),wire+queue_.offset(),size-queue_.offset(),MSG_DONTWAIT);
    if (written>0) {
        queue_.consume(static_cast<size_t>(written),size);
        lastProgress_=millis();
        return true;
    }
    if (written==0) return false;
    if (errno!=EAGAIN && errno!=EWOULDBLOCK && errno!=EINTR) return false;
    return millis()-lastProgress_<5000;
}
namespace {
esp_err_t configureRadio(wifi_interface_t interface) {
    const char* role = "Wi-Fi radio";
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
    if (error == ESP_OK) WiFi.begin(settings_.ssid, settings_.password);
    lastAttempt_ = millis() - 1000;
    lastWifiAttempt_ = millis();
    return error;
}
void DirectWifiLink::tick() {
    if (WiFi.status() != WL_CONNECTED) {
        if (client_.connected())
            Serial.printf("[RX %lu ms] Wi-Fi lost; closing transmitter TCP (status=%d)\n", millis(), WiFi.status());
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
        if (client_.connect(settings_.transmitterIp, settings_.port, 500)) client_.setNoDelay(true);
        else Serial.printf("[RX %lu ms] Cannot connect to transmitter %s:%u\n",
            millis(), settings_.transmitterIp.toString().c_str(), settings_.port);
    }
    if (connected() && !writer_.service(client_)) {
        Serial.println("Receiver TCP write failed or stalled; reconnecting");
        disconnect();
    }
}
bool DirectWifiLink::connected() { return client_.connected(); }
void DirectWifiLink::disconnect() { client_.stop(); reader_.reset(); writer_.reset(); }

ReadResult DirectWifiLink::read(Frame& frame) { return reader_.read(client_, frame); }
bool DirectWifiLink::send(const Frame& frame) {
    if (!connected()) {
        Serial.printf("[RX %lu ms] Cannot send code=%u id=%lu: TCP disconnected\n",
            millis(), frame.header.code, frame.header.packetId);
        disconnect(); return false;
    }
    if (writer_.enqueue(frame)) return true;
    Serial.printf("[RX %lu ms] TCP queue full: code=%u id=%lu; connection retained\n",
        millis(), frame.header.code, frame.header.packetId);
    return false;
}
esp_err_t DirectWifiTransmitter::begin() {
    if (!WiFi.mode(WIFI_STA)) return ESP_FAIL;
    WiFi.setAutoReconnect(true);
    if (!WiFi.config(settings_.transmitterIp, settings_.gateway, settings_.subnet)) return ESP_FAIL;
    esp_err_t error = configureRadio(WIFI_IF_STA);
    if (error != ESP_OK) return error;
    WiFi.begin(settings_.ssid, settings_.password);
    server_.begin();
    server_.setNoDelay(true);
    Serial.printf("Transmitter joining %s; fixed IP=%s, receiver TCP=%u, MAC=%s\n",
        settings_.ssid, settings_.transmitterIp.toString().c_str(), settings_.port,
        WiFi.macAddress().c_str());
    return ESP_OK;
}
bool DirectWifiTransmitter::acceptReceiver() {
    WiFiClient incoming = server_.available();
    if (!incoming) return false;
    if (connected()) { incoming.stop(); return false; }
    disconnect();
    client_ = incoming;
    client_.setNoDelay(true);
    Serial.printf("[TX %lu ms] Receiver TCP connected from %s:%u\n",
        millis(), client_.remoteIP().toString().c_str(), client_.remotePort());
    return true;
}
void DirectWifiTransmitter::tick() {
    static uint32_t lastReconnect = 0;
    if (WiFi.status() != WL_CONNECTED) {
        disconnect();
        if (millis()-lastReconnect >= 5000) { lastReconnect=millis(); WiFi.reconnect(); }
        return;
    }
    if (connected() && !writer_.service(client_)) {
        Serial.printf("[TX %lu ms] Receiver TCP write failed or stalled; closing connection\n", millis());
        disconnect();
    }
}
bool DirectWifiTransmitter::connected() { return client_.connected(); }
void DirectWifiTransmitter::disconnect() { client_.stop(); reader_.reset(); writer_.reset(); }
ReadResult DirectWifiTransmitter::read(Frame& frame) { return reader_.read(client_, frame); }
bool DirectWifiTransmitter::send(const Frame& frame) {
    if (!connected()) {
        Serial.printf("[TX %lu ms] Cannot send code=%u id=%lu: TCP disconnected\n",
            millis(), frame.header.code, frame.header.packetId);
        disconnect(); return false;
    }
    if (writer_.enqueue(frame)) return true;
    Serial.printf("[TX %lu ms] TCP queue full: code=%u id=%lu; connection retained\n",
        millis(), frame.header.code, frame.header.packetId);
    return false;
}
} // namespace csi
