#include <Arduino.h>
#include <WiFi.h>
#include <esp_wifi.h>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>
#include "local_config.h"

constexpr size_t MAX_CSI_BYTES = 1024; // Current S3 SDK: at most 612 bytes.
constexpr uint32_t CAPTURE_TIMEOUT_MS = 2000;
constexpr uint32_t HANDSHAKE_TIMEOUT_MS = 5000;
constexpr uint32_t RETRY_MS = 2000;
enum class State { CONNECT_WIFI, CONNECT_SERVER, WAIT_READY, IDLE, WAIT_CSI, FAULT };

struct Sample {
    uint32_t request_id;
    uint32_t token; // Internal generation prevents stale callbacks across reconnects.
    wifi_pkt_rx_ctrl_t rx_ctrl;
    uint8_t mac[6];
    bool first_word_invalid;
    uint16_t len;
    bool oversize;
    int8_t data[MAX_CSI_BYTES];
};

WiFiClient server;
QueueHandle_t samples;
portMUX_TYPE capture_mux = portMUX_INITIALIZER_UNLOCKED;
bool armed = false;
uint32_t armed_id = 0;
uint32_t generation = 0;
uint32_t armed_at = 0;
uint8_t capture_bssid[6] = {};
State state = State::CONNECT_WIFI;
uint32_t state_since = 0;
uint32_t last_retry = 0;
uint32_t active_id = 0;
uint32_t active_token = 0;
uint32_t last_id = 0;
bool have_last_id = false;
String input;
bool input_overflow = false;
String cached_response;

void transition(State next) {
    static const char* names[] = {"CONNECT_WIFI", "CONNECT_SERVER", "WAIT_READY",
                                  "IDLE", "WAIT_CSI", "FAULT"};
    state = next;
    state_since = millis();
    Serial.printf("State: %s\n", names[static_cast<size_t>(next)]);
}

void disarm() {
    portENTER_CRITICAL(&capture_mux);
    armed = false;
    portEXIT_CRITICAL(&capture_mux);
}

void csi_callback(void*, wifi_csi_info_t* info) {
    if (!info || !info->buf || !info->len) return;
    uint32_t id = 0, token = 0;
    bool selected = false;
    portENTER_CRITICAL(&capture_mux);
    if (armed && millis() - armed_at < CAPTURE_TIMEOUT_MS &&
        memcmp(info->mac, capture_bssid, 6) == 0) {
        id = armed_id;
        token = generation;
        armed = false; // Select exactly one eligible frame per transaction.
        selected = true;
    }
    portEXIT_CRITICAL(&capture_mux);
    if (!selected) return;

    Sample sample = {};
    sample.request_id = id;
    sample.token = token;
    memcpy(&sample.rx_ctrl, &info->rx_ctrl, sizeof(sample.rx_ctrl));
    memcpy(sample.mac, info->mac, 6);
    sample.first_word_invalid = info->first_word_invalid;
    sample.len = info->len;
    sample.oversize = info->len > MAX_CSI_BYTES;
    if (!sample.oversize) memcpy(sample.data, info->buf, info->len);
    // Bounded, non-blocking copy. No allocation, JSON, Serial or TCP here.
    // Wi-Fi callbacks run sequentially; a newer generation replaces a stale one.
    xQueueOverwrite(samples, &sample);
}

bool send_line(const String& message) {
    String wire = message + '\n';
    size_t offset = 0;
    uint32_t started = millis();
    while (offset < wire.length() && server.connected()) {
        offset += server.write(reinterpret_cast<const uint8_t*>(wire.c_str()) + offset,
                               wire.length() - offset);
        if (millis() - started > 2000) break;
        if (offset < wire.length()) delay(1);
    }
    if (offset == wire.length()) return true;
    server.stop(); // Do not reuse a stream containing a partial JSON frame.
    return false;
}

String error_json(uint32_t id, const char* code) {
    return String("{\"type\":\"ERROR\",\"request_id\":") + id +
        ",\"code\":\"" + code + "\"}";
}

String sample_json(const Sample& sample) {
    String out;
    out.reserve(6500);
    out = "{\"type\":\"CSI\",\"schema_version\":1,\"request_id\":";
    out += sample.request_id;
    out += ",\"measurement_source\":\"next_ap_frame_after_arm\",\"mac\":\"";
    char hex[3];
    for (size_t i = 0; i < 6; ++i) {
        snprintf(hex, sizeof(hex), "%02x", sample.mac[i]);
        if (i) out += ':';
        out += hex;
    }
    out += "\",\"first_word_invalid\":";
    out += sample.first_word_invalid ? "true" : "false";
    out += ",\"len\":";
    out += sample.len;
    out += ",\"rx_ctrl\":{";
    const auto& rx = sample.rx_ctrl;
    // All named fields of wifi_pkt_rx_ctrl_t on the configured ESP32-S3 SDK.
    // Reserved bits are retained separately in rx_ctrl_raw_hex.
    out += "\"rssi\":"; out += static_cast<int>(rx.rssi);
#define RX_FIELD(field) out += ",\"" #field "\":"; out += static_cast<uint32_t>(rx.field)
    RX_FIELD(rate); RX_FIELD(sig_mode); RX_FIELD(mcs); RX_FIELD(cwb);
    RX_FIELD(smoothing); RX_FIELD(not_sounding); RX_FIELD(aggregation);
    RX_FIELD(stbc); RX_FIELD(fec_coding); RX_FIELD(sgi);
    out += ",\"noise_floor\":"; out += static_cast<int>(rx.noise_floor);
    RX_FIELD(ampdu_cnt); RX_FIELD(channel); RX_FIELD(secondary_channel);
    RX_FIELD(timestamp); RX_FIELD(ant); RX_FIELD(sig_len); RX_FIELD(rx_state);
#undef RX_FIELD
    out += "},\"rx_ctrl_raw_hex\":\"";
    const auto* bytes = reinterpret_cast<const uint8_t*>(&sample.rx_ctrl);
    for (size_t i = 0; i < sizeof(sample.rx_ctrl); ++i) {
        snprintf(hex, sizeof(hex), "%02x", bytes[i]);
        out += hex;
    }
    out += "\",\"csi_config\":{\"lltf_en\":true,\"htltf_en\":true,"
        "\"stbc_htltf2_en\":true,\"ltf_merge_en\":false,"
        "\"channel_filter_en\":false,\"manu_scale\":false,\"shift\":0},"
        "\"csi_order\":\"imag_real\",\"csi\":[";
    // Preserve every byte, including invalid first bytes. Never shift indices.
    for (size_t i = 0; i < sample.len; ++i) {
        if (i) out += ',';
        out += static_cast<int>(sample.data[i]);
    }
    out += "]}";
    return out;
}

void finish(const String& response) {
    disarm();
    last_id = active_id;
    have_last_id = true;
    cached_response = response;
    send_line(response);
    transition(State::IDLE);
}

bool parse_id(const String& text, uint32_t& id) {
    if (!text.length() || text.length() > 10) return false;
    uint64_t value = 0;
    for (size_t i = 0; i < text.length(); ++i) {
        if (text[i] < '0' || text[i] > '9') return false;
        value = value * 10 + (text[i] - '0');
        if (value > UINT32_MAX) return false;
    }
    id = static_cast<uint32_t>(value);
    return true;
}

void command(const String& line) {
    if (state == State::WAIT_READY) {
        if (line == "READY") {
            send_line("{\"type\":\"READY\",\"schema_version\":1}");
            transition(State::IDLE);
        } else send_line(error_json(0, "EXPECTED_READY"));
        return;
    }
    if (line.startsWith("PROBE ")) {
        // Downlink traffic stimulates CSI, but is not a new capture request.
        // Only CSI/ERROR terminates the outstanding PING transaction.
        return;
    }
    uint32_t id;
    if (!line.startsWith("PING ") || !parse_id(line.substring(5), id)) {
        send_line(error_json(0, "INVALID_COMMAND"));
        return;
    }
    if (state == State::WAIT_CSI) {
        if (id == active_id)
            send_line(String("{\"type\":\"ARMED\",\"request_id\":") + id + "}");
        else send_line(error_json(id, "BUSY"));
        return;
    }
    if (have_last_id && id == last_id) {
        send_line(cached_response); // Retry returns the same sample.
        return;
    }
    if (have_last_id && id < last_id) {
        send_line(error_json(id, "STALE_REQUEST"));
        return;
    }
    xQueueReset(samples);
    active_id = id;
    transition(State::WAIT_CSI);
    portENTER_CRITICAL(&capture_mux);
    armed_id = id;
    active_token = ++generation;
    armed_at = millis();
    armed = true;
    portEXIT_CRITICAL(&capture_mux);
    send_line(String("{\"type\":\"ARMED\",\"request_id\":") + id + "}");
}

void reset_session() {
    disarm();
    server.stop();
    input = "";
    input_overflow = false;
    have_last_id = false;
    cached_response = "";
    xQueueReset(samples);
}

void setup() {
    Serial.begin(115200);
    samples = xQueueCreate(1, sizeof(Sample));
    if (!samples) { transition(State::FAULT); return; }
    WiFi.mode(WIFI_STA);
    WiFi.setAutoReconnect(true);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
    WiFi.setSleep(false);
    wifi_csi_config_t config = {};
    config.lltf_en = true;
    config.htltf_en = true;
    config.stbc_htltf2_en = true;
    // Preserve unmerged training fields rather than averaged HT-LTF values.
    config.ltf_merge_en = false;
    config.channel_filter_en = false;
    config.manu_scale = false;
    config.shift = 0;
    esp_err_t err = esp_wifi_set_csi_config(&config);
    if (err == ESP_OK) err = esp_wifi_set_csi_rx_cb(csi_callback, nullptr);
    if (err == ESP_OK) err = esp_wifi_set_csi(true);
    if (err != ESP_OK) {
        Serial.printf("CSI initialization failed: %s\n", esp_err_to_name(err));
        transition(State::FAULT);
    }
    last_retry = millis() - RETRY_MS;
}

void loop() {
    if (state == State::FAULT) { delay(100); return; }
    if (WiFi.status() != WL_CONNECTED) {
        if (state != State::CONNECT_WIFI) {
            reset_session();
            transition(State::CONNECT_WIFI);
        }
        delay(10);
        return;
    }
    if (state == State::CONNECT_WIFI) {
        const uint8_t* bssid = WiFi.BSSID();
        portENTER_CRITICAL(&capture_mux);
        memcpy(capture_bssid, bssid, 6);
        portEXIT_CRITICAL(&capture_mux);
        transition(State::CONNECT_SERVER);
    }
    if (state == State::CONNECT_SERVER) {
        if (millis() - last_retry >= RETRY_MS) {
            last_retry = millis();
            if (server.connect(SERVER_HOST, SERVER_PORT, 1000)) {
                server.setNoDelay(true);
                String hello = String("{\"type\":\"HELLO\",\"schema_version\":1,\"device_mac\":\"") +
                    WiFi.macAddress() + "\",\"target\":\"esp32s3\",\"sdk\":\"" +
                    ESP.getSdkVersion() + "\",\"capture_timeout_ms\":" +
                    CAPTURE_TIMEOUT_MS + "}";
                send_line(hello);
                transition(State::WAIT_READY);
            }
        }
        delay(1);
        return;
    }
    if (!server.connected()) {
        reset_session();
        transition(State::CONNECT_SERVER);
        return;
    }
    if (state == State::WAIT_READY && millis() - state_since >= HANDSHAKE_TIMEOUT_MS) {
        reset_session();
        transition(State::CONNECT_SERVER);
        return;
    }
    // Bound input processing so excess input cannot starve the capture timeout.
    for (size_t n = 0; n < 512 && server.available(); ++n) {
        char c = static_cast<char>(server.read());
        if (c == '\n') {
            if (input_overflow) send_line(error_json(0, "COMMAND_TOO_LONG"));
            else { input.trim(); command(input); }
            input = "";
            input_overflow = false;
        } else if (input.length() < 512 && !input_overflow) input += c;
        else input_overflow = true;
    }
    if (state == State::WAIT_CSI) {
        Sample sample;
        if (xQueueReceive(samples, &sample, 0) == pdTRUE && sample.token == active_token) {
            finish(sample.oversize ? error_json(active_id, "CSI_TOO_LARGE") : sample_json(sample));
        } else if (millis() - state_since >= CAPTURE_TIMEOUT_MS) {
            finish(error_json(active_id, "CSI_TIMEOUT"));
        }
    }
    delay(1);
}
