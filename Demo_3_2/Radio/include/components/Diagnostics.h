#pragma once
#include <Arduino.h>
#include <WiFi.h>
#include <esp_system.h>

namespace csi {
// Wi-Fi event callbacks run separately from the CSI callback. Serial logs never
// enter either measurement TCP stream.
inline void beginDiagnostics(const char* role, uint32_t baud) {
    Serial.begin(baud);
    Serial.printf("\n[%s %lu ms] Demo_3_2 boot; reset_reason=%d free_heap=%u\n",
        role, millis(), static_cast<int>(esp_reset_reason()), ESP.getFreeHeap());
    WiFi.onEvent([role](WiFiEvent_t event, WiFiEventInfo_t info) {
        switch (event) {
        case ARDUINO_EVENT_WIFI_STA_START:
            Serial.printf("[%s %lu ms] Wi-Fi station started; waiting for hotspot\n", role, millis());
            break;
        case ARDUINO_EVENT_WIFI_STA_CONNECTED:
            Serial.printf("[%s %lu ms] Associated with hotspot; channel=%u\n",
                role, millis(), info.wifi_sta_connected.channel);
            break;
        case ARDUINO_EVENT_WIFI_STA_GOT_IP:
            Serial.printf("[%s %lu ms] Wi-Fi CONNECTED IP=%s gateway=%s MAC=%s RSSI=%d dBm\n",
                role, millis(), WiFi.localIP().toString().c_str(),
                WiFi.gatewayIP().toString().c_str(), WiFi.macAddress().c_str(), WiFi.RSSI());
            break;
        case ARDUINO_EVENT_WIFI_STA_DISCONNECTED:
            Serial.printf("[%s %lu ms] Wi-Fi DISCONNECTED reason=%u; firmware will retry\n",
                role, millis(), info.wifi_sta_disconnected.reason);
            break;
        default: break;
        }
    });
}
} // namespace csi
