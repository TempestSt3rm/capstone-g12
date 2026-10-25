#pragma once
#include <esp_wifi.h>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>
#include "Protocol.h"

namespace csi {
constexpr size_t MaxCsiBytes = 3 * BytesPerLtf;
struct CsiSample {
    uint8_t ltfMask = 0;
    uint16_t csiBytes = 0;
    uint32_t generation = 0;
    uint32_t sequence = 0;
    int8_t data[MaxCsiBytes] = {};
};
struct CaptureSettings { uint8_t channel = 6; };

// Captures an entire batch into a bounded queue. No transport/serialization in callback.
class CsiCapture {
public:
    explicit CsiCapture(const CaptureSettings& settings) : settings_(settings) {}
    CsiCapture(const CsiCapture&) = delete;
    CsiCapture& operator=(const CsiCapture&) = delete;
    esp_err_t begin();
    void setPeer(const uint8_t mac[6]);
    uint32_t start(uint32_t count, uint8_t requiredMask, uint8_t channel);
    void cancel();
    bool take(CsiSample& sample);
    uint32_t dropped();
private:
    static void callback(void* context, wifi_csi_info_t* info);
    void receive(const wifi_csi_info_t* info);
    CaptureSettings settings_;
    QueueHandle_t samples_ = nullptr;
    portMUX_TYPE mutex_ = portMUX_INITIALIZER_UNLOCKED;
    bool active_ = false;
    uint8_t requiredMask_ = 3;
    uint32_t target_ = 0;
    uint32_t accepted_ = 0;
    uint32_t dropped_ = 0;
    uint32_t generation_ = 0;
    uint8_t peer_[6] = {};
};
} // namespace csi
