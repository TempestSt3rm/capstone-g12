#pragma once

// TX hosts this network. Connect the laptop to it; RX joins automatically.
constexpr char WIFI_SSID[] = "CAPSTONE_DEMO_4_2";
constexpr char WIFI_PASSWORD[] = "capstone42";
constexpr uint8_t WIFI_CHANNEL = 6;
constexpr uint16_t TX_PORT = 5000;
constexpr uint16_t RX_PORT = 5001;
constexpr uint32_t CAPTURE_TIMEOUT_MS = 4000;
constexpr uint32_t PROBE_INTERVAL_MS = 75;
constexpr size_t MAX_CSI_BYTES = 1024;
