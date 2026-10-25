#include "components/CsiCapture.h"
#include <cstring>

namespace csi {
esp_err_t CsiCapture::begin() {
    if (samples_) return ESP_ERR_INVALID_STATE;
    samples_ = xQueueCreate(64, sizeof(CsiSample));
    if (!samples_) return ESP_ERR_NO_MEM;
    wifi_csi_config_t config = {};
    config.lltf_en = config.htltf_en = config.stbc_htltf2_en = true;
    // Preserve separate, unmerged, unsmoothed LTF blocks.
    esp_err_t error = esp_wifi_set_csi_config(&config);
    if (error == ESP_OK) error = esp_wifi_set_csi_rx_cb(callback, this);
    if (error == ESP_OK) error = esp_wifi_set_csi(true);
    wifi_promiscuous_filter_t filter = {};
    filter.filter_mask = WIFI_PROMIS_FILTER_MASK_DATA;
    if (error == ESP_OK) error = esp_wifi_set_promiscuous_filter(&filter);
    if (error == ESP_OK) error = esp_wifi_set_promiscuous(true);
    if (error != ESP_OK) {
        esp_wifi_set_csi_rx_cb(nullptr, nullptr);
        vQueueDelete(samples_); samples_ = nullptr;
    }
    return error;
}
void CsiCapture::setPeer(const uint8_t mac[6]) {
    portENTER_CRITICAL(&mutex_); memcpy(peer_, mac, 6); portEXIT_CRITICAL(&mutex_);
}
uint32_t CsiCapture::start(uint32_t count, uint8_t requiredMask, uint8_t channel) {
    cancel();
    xQueueReset(samples_);
    portENTER_CRITICAL(&mutex_);
    settings_.channel = channel;
    target_ = count; accepted_ = dropped_ = 0; requiredMask_ = requiredMask;
    ++generation_; active_ = true;
    uint32_t token = generation_;
    portEXIT_CRITICAL(&mutex_);
    return token;
}
void CsiCapture::cancel() {
    portENTER_CRITICAL(&mutex_); active_ = false; portEXIT_CRITICAL(&mutex_);
}
bool CsiCapture::take(CsiSample& sample) { return samples_ && xQueueReceive(samples_, &sample, 0) == pdTRUE; }
uint32_t CsiCapture::dropped() {
    portENTER_CRITICAL(&mutex_); uint32_t result = dropped_; portEXIT_CRITICAL(&mutex_); return result;
}
void CsiCapture::callback(void* context, wifi_csi_info_t* info) {
    static_cast<CsiCapture*>(context)->receive(info);
}
void CsiCapture::receive(const wifi_csi_info_t* info) {
    // This runs in the Wi-Fi task. Read cached state only; never call Wi-Fi APIs.
    portENTER_CRITICAL(&mutex_);
    bool active = active_;
    uint8_t channel = settings_.channel;
    portEXIT_CRITICAL(&mutex_);
    if (!active || !info || !info->buf || info->rx_ctrl.channel != channel ||
        info->rx_ctrl.cwb != 0 || info->rx_ctrl.secondary_channel != 0 ||
        info->rx_ctrl.sig_mode > 1) return;
    // With all three options enabled and no secondary channel, HT20 LTF blocks
    // each contain exactly 64 complex pairs. Packet STBC determines the third block.
    uint8_t mask = info->rx_ctrl.sig_mode == 1 ? (info->rx_ctrl.stbc ? 7 : 3) : 1;
    if (info->len != Protocol::csiBytes(mask)) return;
    CsiSample sample;
    portENTER_CRITICAL(&mutex_);
    bool eligible = active_ && (mask & requiredMask_) == requiredMask_ &&
                    memcmp(info->mac, peer_, 6) == 0;
    sample.generation = generation_;
    sample.sequence = accepted_;
    portEXIT_CRITICAL(&mutex_);
    if (!eligible) return;
    sample.ltfMask=mask;
    sample.csiBytes=info->len;
    memcpy(sample.data, info->buf, info->len);
    portENTER_CRITICAL(&mutex_);
    // Reject a callback that overlapped a cancellation or a new batch.
    if (active_ && sample.generation == generation_) {
        if (xQueueSend(samples_, &sample, 0) == pdTRUE) {
            if (++accepted_ == target_) active_ = false;
        } else ++dropped_; // Do not count a skipped frame toward the requested total.
    }
    portEXIT_CRITICAL(&mutex_);
}
} // namespace csi
