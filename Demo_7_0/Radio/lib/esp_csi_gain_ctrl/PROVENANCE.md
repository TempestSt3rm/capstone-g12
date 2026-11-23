# Espressif gain-control component

Vendored from the official `espressif/esp_csi_gain_ctrl` component, version
**0.1.5**, under Apache-2.0 (see LICENSE).

Source: https://components.espressif.com/components/espressif/esp_csi_gain_ctrl/versions/0.1.5

Archive: https://components-file.espressif.com/components/espressif/esp_csi_gain_ctrl/0.1.5/download/df8d9bdd-f537-4c5f-aeea-5d79b7bed259.zip

- Archive SHA-256: `2960f378807c92a095135dff88694c3837315d9fab962a2f689945f93c65930b`
- Header: `esp_csi_gain_ctrl/include/esp_csi_gain_ctrl.h`, unchanged.
- Binary: `esp_csi_gain_ctrl/4.4/esp32s3/libesp_csi_gain_ctrl.a`, unchanged.
- Binary SHA-256: `7cfa57c6c294ba41fbd1aec25e5e972ccc1dd3664e05223d058f191c56a35aca`

`Radio/gain_library.py` links this archive only into the S3 receiver. Its
ESP-IDF major/minor version and chip target are checked at compile time.
Updating the framework requires selecting and verifying the matching archive.
This demo only extracts gain metadata; it does not force gain, establish a
baseline, compensate amplitudes, or modify CSI bytes.
