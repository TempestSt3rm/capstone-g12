#include <Arduino.h>
#include <WiFi.h>
#include <esp_wifi.h>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>
#include "Config.h"
#include "Protocol.h"
#include "UdpSocket.h"

struct Sample {
    uint32_t token;
    uint16_t len;
    int8_t data[MAX_CSI_BYTES];
};

UdpSocket udp;
QueueHandle_t samples;
portMUX_TYPE capture_mux = portMUX_INITIALIZER_UNLOCKED;
bool armed = false;
uint32_t generation = 0;
uint8_t transmitter_mac[6] = {};
IPAddress transmitter_ip;
bool online = false;
bool pending = false;
Request current;
Request cached_request;
String cached_response;
uint32_t started_at = 0;
uint32_t last_hello_at = 0;

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
    if (armed && memcmp(info->mac, transmitter_mac, 6) == 0) {
        sample.token = generation;
        armed = false;
        selected = true;
    }
    portEXIT_CRITICAL(&capture_mux);
    if (!selected) return;
    // Preserve ALL bytes, including any invalid first word. Same imag/real
    // order, all training fields, and manual scaling as Demo 4_1.
    sample.len = info->len;
    memcpy(sample.data, info->buf, sample.len);
    xQueueOverwrite(samples, &sample);
}

void send_cached() {
    // Logical RX -> laptop reply. The TX access point forwards station traffic.
    if (udp.send(cached_response, cached_request.computer, cached_request.port))
        udp.send(control("DONE", cached_request), transmitter_ip, TX_PORT);
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
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD); // DHCP from TX access point.
    WiFi.setSleep(false);
    ESP_ERROR_CHECK(esp_wifi_set_bandwidth(WIFI_IF_STA, WIFI_BW_HT40));
    wifi_csi_config_t config = {};
    config.lltf_en = true;
    config.htltf_en = true;
    config.stbc_htltf2_en = true;
    config.ltf_merge_en = false;
    config.channel_filter_en = false;
    config.manu_scale = false;
    config.shift = 0;
    ESP_ERROR_CHECK(esp_wifi_set_csi_config(&config));
    ESP_ERROR_CHECK(esp_wifi_set_csi_rx_cb(csi_callback, nullptr));
    ESP_ERROR_CHECK(esp_wifi_set_csi(true));
    Serial.println("RX connecting to TX access point...");
}

void loop() {
    if (WiFi.status() != WL_CONNECTED) {
        if (online) {
            disarm();
            udp.close();
            cached_response = "";
            online = false;
            Serial.println("RX disconnected; reconnecting...");
        }
        delay(10);
        return;
    }
    if (!online) {
        // Driver calls stay OUTSIDE the lock shared with the Wi-Fi callback.
        const uint8_t* bssid = WiFi.BSSID();
        if (!bssid) { delay(10); return; }
        portENTER_CRITICAL(&capture_mux);
        memcpy(transmitter_mac, bssid, 6);
        portEXIT_CRITICAL(&capture_mux);
        transmitter_ip = WiFi.gatewayIP();
        if (!udp.begin(RX_PORT)) { delay(100); return; }
        online = true;
        last_hello_at = millis() - 1000;
        Serial.printf("RX ready: IP=%s TX=%s\n", WiFi.localIP().toString().c_str(),
                      transmitter_ip.toString().c_str());
    }

    // Sample first: complete a selected capture before processing another ARM.
    Sample sample;
    if (xQueueReceive(samples, &sample, 0) == pdTRUE && pending && sample.token == generation) {
        String response;
        response.reserve(64 + sample.len * 5);
        response = "{\"packet_id\":";
        response += current.id;
        response += ",\"csi\":[";
        for (size_t i = 0; i < sample.len; ++i) {
            if (i) response += ',';
            response += static_cast<int>(sample.data[i]);
        }
        response += "]}";
        disarm();
        cached_request = current;
        cached_response = response;
        send_cached();
    }
    if (pending && millis() - started_at >= CAPTURE_TIMEOUT_MS) {
        disarm();
        Serial.printf("CSI timeout: %lu\n", static_cast<unsigned long>(current.id));
    }

    char buffer[384];
    IPAddress source;
    uint16_t port;
    const int len = udp.receive(buffer, sizeof(buffer) - 1, source, port);
    if (len > 0 && source == transmitter_ip && port == TX_PORT) {
        buffer[len] = '\0';
        Request incoming;
        if (parse_control(String(buffer), "ARM", incoming)) {
            if (cached_response.length() && same_request(incoming, cached_request)) {
                send_cached(); // Lost UDP response: reuse the SAME CSI sample.
            } else if (pending && same_request(incoming, current)) {
                udp.send(control("ARMED", current), transmitter_ip, TX_PORT);
            } else {
                disarm();
                current = incoming;
                started_at = millis();
                pending = true;
                portENTER_CRITICAL(&capture_mux);
                ++generation;
                armed = true;
                portEXIT_CRITICAL(&capture_mux);
                udp.send(control("ARMED", current), transmitter_ip, TX_PORT);
            }
        }
        // PROBE payloads only stimulate CSI; they never arm another request.
    }
    if (!pending && millis() - last_hello_at >= 1000) {
        last_hello_at = millis();
        udp.send("HELLO", transmitter_ip, TX_PORT);
    }
    delay(1);
}
