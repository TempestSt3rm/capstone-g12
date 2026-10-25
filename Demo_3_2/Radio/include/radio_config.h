#pragma once
#include "components/RadioLink.h"
#include "components/CsiCapture.h"
#include "components/ReceiverApp.h"

namespace demo3 {
// Match Windows Mobile hotspot credentials. Change IPs to match ipconfig.
// Reserve an unused IP outside the hotspot DHCP pool for the transmitter.
const csi::RadioSettings Radio = {"JEFFREY_ASUS_TEMPEST", "Capstone_G12", 0, 3333,
    IPAddress(192,168,137,2), IPAddress(192,168,137,1), IPAddress(255,255,255,0)};
constexpr uint16_t HostPort = 3334;
inline csi::CaptureSettings captureSettings() { return {}; } // Actual associated channel is used.
const csi::ReceiverSettings Receiver = {};
constexpr uint32_t SerialBaud = 115200; // Optional diagnostics and flashing only.
constexpr uint32_t LoopDelayMs = 1;
}
