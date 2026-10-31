# Demo 2.1 – ESP32 CSI Collection

This demo collects real WiFi CSI measurements using an ESP32 connected to a local WiFi network.

## Setup

- ESP32-PICO-KIT
- VS Code with PlatformIO
- Arduino framework
- Python with pyserial and matplotlib

## How It Works

1. Connect the ESP32 to a local WiFi network.
2. Send ping requests from a laptop to the ESP32.
3. Collect CSI measurements through the ESP32 CSI API.
4. Send the average absolute CSI values over Serial.
5. Save the measurements to a CSV file using Python.
6. Plot the measurements over time.

## Files

- `src/main.cpp` – ESP32 firmware for CSI collection
- `src/secrets.example.h` – Example WiFi configuration
- `capture_csi.py` – Saves CSI measurements to CSV
- `plot_csi.py` – Plots CSI measurements over time
- `platformio.ini` – PlatformIO configuration

## Observations

CSI values fluctuated even when there was no movement. When moving a hand near the ESP32, the values appeared to fluctuate more.
Further testing is needed to confirm the difference between still and moving conditions.
The current measurement uses the average absolute value of the raw CSI buffer. It is a simple exploratory feature, not a calibrated CSI amplitude or validated presence detector.

## Next Steps

Compare CSI measurements under still and moving conditions and investigate whether the changes can be used for basic presence detection.