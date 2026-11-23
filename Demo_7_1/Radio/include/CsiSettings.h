#pragma once

#include <Arduino.h>
#include <esp_wifi.h>

// Serialize the same structure successfully passed to esp_wifi_set_csi_config.
// This SDK has no CSI configuration read-back API.
inline String csi_settings_json(const wifi_csi_config_t& config) {
    String result = "{";
    auto flag = [&](const char* name, bool value) {
        if (result.length() > 1) result += ',';
        result += '"';
        result += name;
        result += "\":";
        result += value ? "true" : "false";
    };
    flag("lltf_en", config.lltf_en);
    flag("htltf_en", config.htltf_en);
    flag("stbc_htltf2_en", config.stbc_htltf2_en);
    flag("ltf_merge_en", config.ltf_merge_en);
    flag("channel_filter_en", config.channel_filter_en);
    flag("manu_scale", config.manu_scale);
    result += ",\"shift\":";
    result += config.shift;
    result += '}';
    return result;
}
