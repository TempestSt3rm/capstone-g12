#include <Arduino.h>
#include <WiFi.h>
#include "esp_wifi.h"

#include "secrets.h"

void csi_callback(void* ctx, wifi_csi_info_t* info) {
  if (info == nullptr || info->buf == nullptr) {
    return;
  }

  int sum = 0;

  for (int i = 0; i < info->len; i++) {
    sum += abs(info->buf[i]);
  }

  float average = (float)sum / info->len;

  // CSV format: time_ms,csi_average
  Serial.print(millis());
  Serial.print(",");
  Serial.println(average);
}

void setup() {
  Serial.begin(115200);
  delay(1000);

  WiFi.mode(WIFI_STA);

  Serial.println("Connecting to WiFi...");
  WiFi.begin(ssid, password);

  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }

  Serial.println();
  Serial.println("WiFi connected!");

  Serial.print("IP address: ");
  Serial.println(WiFi.localIP());

  wifi_csi_config_t csi_config = {
    .lltf_en = true,
    .htltf_en = true,
    .stbc_htltf2_en = true,
    .ltf_merge_en = true,
    .channel_filter_en = false,
    .manu_scale = false,
    .shift = 0
  };

  esp_wifi_set_csi_config(&csi_config);
  esp_wifi_set_csi_rx_cb(csi_callback, NULL);
  esp_wifi_set_csi(true);

  Serial.println("CSI enabled!");
}

void loop() {
  delay(1000);
}