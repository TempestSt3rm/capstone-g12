#pragma once
#include "components/RadioLink.h"
#include "components/CsiCapture.h"
#include "components/ReceiverApp.h"

// Settings for the two Demo #3 boards.
namespace demo3 {
const csi::RadioSettings Radio = {"Capstone-Demo3", "demo3-csi-link", 6, 3333};
inline csi::CaptureSettings captureSettings() {
    csi::CaptureSettings settings;
    settings.channel = Radio.channel;
    return settings;
}
const csi::ReceiverSettings Receiver = {};
constexpr uint32_t SerialBaud = 115200;
constexpr uint32_t ProbeIntervalUs = 10000; // 10 ms between probes; prioritize link servicing.
constexpr uint32_t LoopDelayMs = 1; // Give radio/system tasks time to run each loop.
} // namespace demo3
