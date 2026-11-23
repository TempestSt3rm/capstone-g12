#include <Arduino.h>
#include <WiFi.h>
#include <esp_wifi.h>
#include "Config.h"
#include "Protocol.h"
#include "UdpSocket.h"
#include "CaptureSequence.h"

struct Receiver {
    IPAddress ip;
    bool registered = false;
    uint32_t seen_at = 0;
    String csi_settings;
};

UdpSocket udp;
Receiver receivers[RECEIVER_COUNT];
CaptureSequence sequence;
Request current;
bool have_request = false;
bool receiver_armed = false;
uint32_t started_at = 0;
uint32_t last_send_at = 0;

void start_stage() {
    receiver_armed = false;
    started_at = millis();
    last_send_at = millis() - PROBE_INTERVAL_MS;
}

void setup() {
    Serial.begin(115200);
    WiFi.mode(WIFI_AP);
    // One receiver plus the laptop need two station slots.
    if (!WiFi.softAP(WIFI_SSID, WIFI_PASSWORD, WIFI_CHANNEL, false, RECEIVER_COUNT + 1)) {
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
    Serial.println("ESP32-U TX waiting for RX1 ESP32-S3 and laptop...");
}

void loop() {
    char buffer[768];
    IPAddress source;
    uint16_t port;
    const int len = udp.receive(buffer, sizeof(buffer) - 1, source, port);
    if (len > 0) {
        buffer[len] = '\0';
        const String message(buffer);
        uint32_t receiver_id;
        const int settings_start = message.indexOf(' ', 6);
        if (message.startsWith("HELLO ") && port == RX_PORT &&
            settings_start > 6 &&
            parse_number(message.substring(6, settings_start), receiver_id) && receiver_id >= 1 && receiver_id <= RECEIVER_COUNT &&
            message.substring(settings_start + 1).startsWith("{") && message.endsWith("}")) {
            Receiver& receiver = receivers[receiver_id - 1];
            const String settings = message.substring(settings_start + 1);
            if (!receiver.registered || source != receiver.ip || settings != receiver.csi_settings) {
                Serial.printf("RX%lu registered: %s\n", static_cast<unsigned long>(receiver_id), source.toString().c_str());
                Serial.printf("RX%lu CSI settings: %s\n", static_cast<unsigned long>(receiver_id), settings.c_str());
            }
            receiver.ip = source;
            receiver.csi_settings = settings;
            receiver.seen_at = millis();
            receiver.registered = true;
        } else if (message.startsWith("GET_CONFIG ")) {
            uint32_t request_id;
            if (parse_number(message.substring(11), request_id)) {
                for (unsigned index = 0; index < RECEIVER_COUNT; ++index) {
                    const Receiver& receiver = receivers[index];
                    if (!receiver.registered || millis() - receiver.seen_at >= 6000) continue;
                    String report = "{\"type\":\"receiver_config\",\"schema_version\":1,\"request_id\":";
                    report += request_id;
                    report += ",\"receiver_id\":";
                    report += index + 1;
                    report += ",\"csi_config\":";
                    report += receiver.csi_settings;
                    report += '}';
                    udp.send(report, source, port);
                }
            }
        } else if (message.startsWith("PING ")) {
            uint32_t id;
            bool all_ready = true;
            for (const Receiver& receiver : receivers)
                all_ready = all_ready && receiver.registered && millis() - receiver.seen_at < 6000;
            if (parse_number(message.substring(5), id) && all_ready) {
                Request incoming;
                incoming.id = id;
                incoming.computer = source;
                incoming.port = port;
                const bool same_computer = have_request && source == current.computer && port == current.port;
                const bool older = same_computer && id != current.id && static_cast<int32_t>(id - current.id) <= 0;
                if (!older && (!sequence.receiver() || same_computer) &&
                    (!have_request || !same_request(current, incoming))) {
                    current = incoming;
                    have_request = true;
                    sequence.begin();
                    start_stage();
                }
                // Duplicate PINGs never restart RX1 or reset the stage timeout.
            }
        } else if (message.startsWith("RECEIVED ") && sequence.receiver() &&
                   source == current.computer && port == current.port) {
            const int split = message.indexOf(' ', 9);
            uint32_t id;
            if (split > 0 && parse_number(message.substring(9, split), id) && id == current.id &&
                parse_number(message.substring(split + 1), receiver_id) && sequence.acknowledge(receiver_id)) {
                if (sequence.receiver()) start_stage();
            }
        } else if (sequence.receiver()) {
            Receiver& receiver = receivers[sequence.receiver() - 1];
            Request reply;
            if (source == receiver.ip && port == RX_PORT &&
                parse_control(message, "ARMED", reply) && same_request(reply, current)) {
                receiver_armed = true;
                last_send_at = millis() - PROBE_INTERVAL_MS;
            }
        }
    }

    if (sequence.receiver() && millis() - started_at >= CAPTURE_TIMEOUT_MS) {
        Serial.printf("RX%u timeout for packet %lu\n", sequence.receiver(), static_cast<unsigned long>(current.id));
        sequence.cancel();
    }
    if (sequence.receiver() && millis() - last_send_at >= PROBE_INTERVAL_MS) {
        last_send_at = millis();
        Receiver& receiver = receivers[sequence.receiver() - 1];
        if (!receiver_armed) {
            udp.send(control("ARM", current), receiver.ip, RX_PORT);
        } else {
            String probe = String("PROBE ") + current.id + " ";
            for (size_t i = 0; i < 256; ++i) probe += 'x';
            udp.send(probe, receiver.ip, RX_PORT);
            // Repeat ARM after the probe so a lost data packet or laptop ACK
            // causes RX to resend its cached sample, never take another one.
            receiver_armed = false;
        }
    }
    delay(1);
}
