"""Link the official ESP32-S3 / ESP-IDF 4.4 gain component (RX only)."""
from pathlib import Path
Import("env")
archive_dir = Path(env.subst("$PROJECT_DIR")) / "lib/esp_csi_gain_ctrl/lib/esp32s3"
env.Append(LIBPATH=[str(archive_dir)], LIBS=["esp_csi_gain_ctrl"])
