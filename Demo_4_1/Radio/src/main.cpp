#include <Arduino.h>
#include <WiFi.h>
#include <WiFiUdp.h>
#include <esp_wifi.h>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>
#include "local_config.h"

constexpr uint16_t PORT = 5000;
constexpr uint32_t CAPTURE_TIMEOUT_MS = 2000;
constexpr size_t MAX_CSI_BYTES = 1024;

struct Sample {
    uint32_t token;
    uint16_t len;
    int8_t data[MAX_CSI_BYTES];
};

WiFiUDP udp;
QueueHandle_t samples;
portMUX_TYPE capture_mux = portMUX_INITIALIZER_UNLOCKED;
bool armed = false;
uint32_t generation = 0;
uint8_t hotspot_mac[6] = {};
bool online = false;
bool pending = false;
uint32_t packet_id = 0;
uint32_t started_at = 0;
IPAddress peer_ip;
uint16_t peer_port = 0;
String cached_response;
uint32_t cached_id = 0;
IPAddress cached_ip;
uint16_t cached_port = 0;

void disarm() {
    portENTER_CRITICAL(&capture_mux);
    armed = false;
    portEXIT_CRITICAL(&capture_mux);
    pending = false;
}

void csi_callback(void*, wifi_csi_info_t* info) {
    if (!info || !info->buf || !info->len || info->len > MAX_CSI_BYTES) return;
    Sample sample = {};
    bool selected = false;
    portENTER_CRITICAL(&capture_mux);
    if (armed && memcmp(info->mac, hotspot_mac, 6) == 0) {
        sample.token = generation;
        armed = false;
        selected = true;
    }
    portEXIT_CRITICAL(&capture_mux);
    if (!selected) return;
    // Preserve every CSI byte, even when first_word_invalid flags the first four.
    sample.len = info->len;
    memcpy(sample.data, info->buf, sample.len);
    // Wi-Fi task: only copy to a queue; format/send in loop().
    xQueueOverwrite(samples, &sample);
}

void send_response(const String& response, IPAddress address, uint16_t port) {
    if (!udp.beginPacket(address, port)) return;
    udp.write(reinterpret_cast<const uint8_t*>(response.c_str()), response.length());
    udp.endPacket();
}

bool parse_ping(const char* text, size_t len, uint32_t& id) {
    if (len < 6 || len > 15 || memcmp(text, "PING ", 5) != 0) return false;
    uint64_t value = 0;
    for (size_t i = 5; i < len; ++i) {
        if (text[i] < '0' || text[i] > '9') return false;
        value = value * 10 + (text[i] - '0');
        if (value > UINT32_MAX) return false;
    }
    id = static_cast<uint32_t>(value);
    return true;
}

void setup() {
    Serial.begin(115200);
    samples = xQueueCreate(1, sizeof(Sample));
    if (!samples) {
        Serial.println("Cannot allocate CSI queue");
        while (true) delay(1000);
    }
    WiFi.mode(WIFI_STA);
    WiFi.setAutoReconnect(true);
    // Use DHCP to obtain an address from the laptop hotspot.
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
    WiFi.setSleep(false);

    wifi_csi_config_t config = {};
    // Capture all available training fields without merging their values.
    config.lltf_en = true;
    config.htltf_en = true;
    config.stbc_htltf2_en = true;

    config.ltf_merge_en = false;
    config.channel_filter_en = false;
    config.manu_scale = true;
    config.shift = 0;

    ESP_ERROR_CHECK(esp_wifi_set_csi_config(&config));
    ESP_ERROR_CHECK(esp_wifi_set_csi_rx_cb(csi_callback, nullptr));
    ESP_ERROR_CHECK(esp_wifi_set_csi(true));
    Serial.println("Connecting to hotspot...");
}

void loop() {
    if (WiFi.status() != WL_CONNECTED) {
        if (online) {
            disarm();
            udp.stop();
            cached_response = "";
            online = false;
            Serial.println("Disconnected; reconnecting...");
        }
        delay(10);
        return;
    }
    if (!online) {
        // Wi-Fi APIs may acquire driver locks. Never call them while holding
        // capture_mux: the Wi-Fi task also acquires it in csi_callback().
        const uint8_t* bssid = WiFi.BSSID();
        if (!bssid) { delay(10); return; }
        portENTER_CRITICAL(&capture_mux);
        memcpy(hotspot_mac, bssid, 6);
        portEXIT_CRITICAL(&capture_mux);
        if (!udp.begin(PORT)) { delay(100); return; }
        online = true;
        Serial.printf("Ready: %s UDP %u\n", WiFi.localIP().toString().c_str(), PORT);
    }

    Sample sample;
    if (xQueueReceive(samples, &sample, 0) == pdTRUE && pending &&
        sample.token == generation) {
        String response;
        response.reserve(64 + sample.len * 5);
        response = "{\"packet_id\":";
        response += packet_id;
        response += ",\"csi\":[";
        for (size_t i = 0; i < sample.len; ++i) {
            if (i) response += ',';
            response += static_cast<int>(sample.data[i]);
        }
        response += "]}";
        disarm();
        cached_response = response;
        cached_id = packet_id;
        cached_ip = peer_ip;
        cached_port = peer_port;
        send_response(response, peer_ip, peer_port);
    }
    if (pending && millis() - started_at >= CAPTURE_TIMEOUT_MS) {
        disarm(); // No fake CSI or extra error fields on the wire.
        Serial.println("CSI capture timed out");
    }

    const int size = udp.parsePacket();
    if (size > 0) {
        const IPAddress address = udp.remoteIP();
        const uint16_t port = udp.remotePort();
        char request[16];
        const int len = udp.read(request, sizeof(request));
        uint32_t id;
        if (size <= 15 && len == size && parse_ping(request, len, id)) {
            if (cached_response.length() && id == cached_id &&
                address == cached_ip && port == cached_port) {
                send_response(cached_response, address, port);
            } else if (!pending) {
                packet_id = id;
                peer_ip = address;
                peer_port = port;
                started_at = millis();
                pending = true;
                portENTER_CRITICAL(&capture_mux);
                ++generation;
                armed = true;
                portEXIT_CRITICAL(&capture_mux);
            }
            // Repeated PINGs stimulate downlink frames while capture is armed.
        }
    }
    delay(1);
}
