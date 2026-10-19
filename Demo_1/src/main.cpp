#include <Arduino.h>
#include <WiFi.h>
#include <esp_wifi.h>

const char* SSID = "BELL935";
const char* PASSWORD = "A6C257A7A69C";

volatile uint32_t csi_count = 0;

// Called whenever Wi-Fi driver receives CSI data
void csi_callback(void* ctx, wifi_csi_info_t* info) {
    if (!info || !info->buf) return;

    csi_count++;

    // Print only every 20th sample
    // if (csi_count % 20 != 0) return;

    Serial.printf("RSSI: %d | CSI length: %d | ",
                  info->rx_ctrl.rssi, info->len);

    // Skip invalid first four bytes if flagged
    int start = info->first_word_invalid ? 4 : 0;

    // Print first 10 complex CSI values
    for (int i = start; i + 1 < info->len && i < start + 20; i += 2) {
        int imag = info->buf[i];
        int real = info->buf[i + 1];

        Serial.printf("(%d,%d) ", real, imag);
    }

    Serial.println();
}

void setup() {
    Serial.begin(115200);
    delay(1000);

    WiFi.mode(WIFI_STA);
    WiFi.begin(SSID, PASSWORD);

    Serial.print("Connecting");

    while (WiFi.status() != WL_CONNECTED) {
        delay(500);
        Serial.print(".");
    }

    Serial.println("\nWiFi connected!");
    Serial.println(WiFi.localIP());

    wifi_csi_config_t config = {};
    config.lltf_en = true;
    config.htltf_en = true;
    config.stbc_htltf2_en = true;
    config.ltf_merge_en = true;
    config.channel_filter_en = false;
    config.manu_scale = false;
    config.shift = 0;

    esp_err_t err;

    err = esp_wifi_set_csi_config(&config);
    Serial.printf("CSI config: %s\n", esp_err_to_name(err));

    err = esp_wifi_set_csi_rx_cb(csi_callback, NULL);
    Serial.printf("CSI callback: %s\n", esp_err_to_name(err));

    err = esp_wifi_set_csi(true);
    Serial.printf("CSI enabled: %s\n", esp_err_to_name(err));
}

void loop() {
    delay(1000);
}