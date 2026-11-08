#include <Arduino.h>
#include <WiFi.h>
#include <esp_wifi.h>
#include "Config.h"
#include "Protocol.h"
#include "UdpSocket.h"

UdpSocket udp;
IPAddress receiver_ip;
bool have_receiver = false;
uint32_t receiver_seen_at = 0;
Request current;
bool have_request = false;
bool pending = false;
bool receiver_armed = false;
uint32_t started_at = 0;
uint32_t last_send_at = 0;

void setup() {
    Serial.begin(115200);
    WiFi.mode(WIFI_AP);
    if (!WiFi.softAP(WIFI_SSID, WIFI_PASSWORD, WIFI_CHANNEL, false, 2)) {
        Serial.println("Cannot start access point");
        while (true) delay(1000);
    }
    ESP_ERROR_CHECK(esp_wifi_set_bandwidth(WIFI_IF_AP, WIFI_BW_HT40));
    if (!udp.begin(TX_PORT)) {
        Serial.println("Cannot open TX UDP socket");
        while (true) delay(1000);
    }
    Serial.printf("TX ready: SSID=%s IP=%s UDP=%u\n", WIFI_SSID,
                  WiFi.softAPIP().toString().c_str(), TX_PORT);
    Serial.println("Connect laptop to this network; waiting for receiver...");
}

void loop() {
    char buffer[96];
    IPAddress source;
    uint16_t port;
    const int len = udp.receive(buffer, sizeof(buffer) - 1, source, port);
    if (len > 0) {
        buffer[len] = '\0';
        const String message(buffer);
        if (message == "HELLO" && port == RX_PORT) {
            if (!have_receiver || source != receiver_ip)
                Serial.printf("RX registered: %s\n", source.toString().c_str());
            receiver_ip = source;
            receiver_seen_at = millis();
            have_receiver = true;
        } else if (message.startsWith("PING ")) {
            uint32_t id;
            if (parse_number(message.substring(5), id) && have_receiver &&
                millis() - receiver_seen_at < 6000) {
                Request incoming;
                incoming.id = id;
                incoming.computer = source;
                incoming.port = port;
                const bool same_computer = have_request && source == current.computer &&
                                           port == current.port;
                const bool older = same_computer && id != current.id &&
                                   static_cast<int32_t>(id - current.id) <= 0;
                if (!older && (!pending || same_computer)) {
                    if (!pending || !same_request(current, incoming)) {
                        current = incoming;
                        have_request = true;
                        pending = true;
                        receiver_armed = false;
                        started_at = millis();
                        last_send_at = millis() - PROBE_INTERVAL_MS;
                    }
                    // A duplicate PING does not restart the capture window.
                }
            }
        } else if (have_receiver && source == receiver_ip && port == RX_PORT && pending) {
            Request reply;
            if (parse_control(message, "ARMED", reply) && same_request(reply, current)) {
                receiver_armed = true;
                last_send_at = millis() - PROBE_INTERVAL_MS;
            } else if (parse_control(message, "DONE", reply) && same_request(reply, current)) {
                pending = false;
            }
        }
    }

    if (pending && millis() - started_at >= CAPTURE_TIMEOUT_MS) pending = false;
    if (pending && millis() - last_send_at >= PROBE_INTERVAL_MS) {
        last_send_at = millis();
        if (!receiver_armed) {
            udp.send(control("ARM", current), receiver_ip, RX_PORT);
        } else {
            // Ordinary AP-to-station UDP traffic, as in Demo 4_1. RX captures
            // the next eligible TX/AP frame after arming, not a proven match
            // between the UDP payload and a particular Wi-Fi CSI callback.
            String probe = String("PROBE ") + current.id + " ";
            for (size_t i = 0; i < 256; ++i) probe += 'x';
            udp.send(probe, receiver_ip, RX_PORT);
        }
    }
    delay(1);
}
