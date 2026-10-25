# Demo #3: position classification without a LAN router

A fixed transmitter and moving receiver replace the router-based link in Demo #2:

```text
Laptop / collect.py / CSV / SVM
              | USB serial (commands and returned measurements)
Fixed transmitter ESP32 (dedicated SoftAP + TCP gateway)
              | direct 2.4 GHz Wi-Fi
Moving receiver ESP32-PICO (independent power; captures CSI locally)
```

The transmitter creates `Capstone-Demo3` on channel 6. Only the receiver joins
it. The laptop needs neither Wi-Fi nor Internet during collection. This is a
dedicated Wi-Fi network between the boards; no external router or LAN service
is used. The receiver keeps retrying Wi-Fi/TCP connection and the application
handshake, so either board can be powered first.

The receiver is an **ESP32-PICO-KIT-1 V1.0**, configured with the PlatformIO
`pico32` target (ESP32, 4 MB flash). The fixed transmitter uses the
`freenove_esp32_s3_wroom` target. Board targets are set separately in
`Radio/platformio.ini`; select `receiver` or `transmitter` when flashing.

Board references: [Espressif ESP32-PICO-KIT-1 guide](https://docs.espressif.com/projects/esp-dev-kits/en/latest/esp32/esp32-pico-kit-1/user_guide.html)
and [PlatformIO Pico Kit target](https://docs.platformio.org/en/latest/boards/espressif32/pico32.html).

## 1. Flash the boards

From `Demo_3_1`, install Python dependencies and list serial ports:

```powershell
python -m pip install -r requirements.txt
python -m serial.tools.list_ports
```

Upload each role to the appropriate board (replace the example COM ports):

```powershell
pio run -d Radio -e transmitter -t upload --upload-port COM4
pio run -d Radio -e receiver -t upload --upload-port COM5
```

Keep the transmitter powered at a fixed, marked position and orientation.
Connect the transmitter to the laptop using its USB-UART/data port. Power the
receiver independently so it can be moved between positions. Its serial output
is optional diagnostics; collection does not use that port. Close the
PlatformIO serial monitor before running collection; only one program can own
the port. Use the same board pair, channel, orientation, and room layout for all
recordings. Demo radio/capture settings live in `Radio/include/radio_config.h`.

On successful transmitter startup, serial output includes
`Transmitter ready: AP=Capstone-Demo3 ...; waiting for receiver`.
If initialization fails, the preceding line names the failing Wi-Fi operation.
Both boards must use the supported BGN protocol bitmap; an `11N`-only bitmap
is not supported by this SDK.

### Check either board over serial

Open that board's serial monitor at **115200 baud** with **Newline** or
**Both NL & CR**, then send `alive` (case-insensitive). The transmitter replies:

```json
{"type":"ALIVE","role":"transmitter"}
```

The receiver replies on its own serial port:

```json
{"type":"ALIVE","role":"receiver"}
```

This checks local serial/firmware liveness and works while the other board is
powered off or wireless initialization has failed. It does not start a capture,
reset the session, or confirm the wireless link. Close the serial monitor before
running `collect.py` on the transmitter port.

## 2. Record known positions

Move the receiver to each marked position and hold it stationary. The transmitter
stays fixed. Edit the settings near the top of `Collector/collect.py`:

```python
PORT = "COM7"          # Stationary transmitter's serial port
LABEL = "x0y0"         # Position being recorded
SAMPLE_COUNT = 1000
BAUD_RATE = 115200
```

Run the script without arguments:

```powershell
python Collector/collect.py
```

Repeat with `LABEL` set to `x0y1`, `x1y0`, and `x1y1` at the corresponding
receiver positions. Close the transmitter serial monitor before collecting.

Each run writes `Data/<label>.jsonl` (raw CSI and radio metadata) and
`Data/<label>.csv` (model features). It refuses to overwrite an existing
recording. Incomplete runs retain `.partial` files for diagnosis and never
publish a completed CSV. Move those files before repeating the same label.
Paths resolve relative to the scripts; launch these commands from `Demo_3_1`.

## 3. Train and evaluate

```powershell
python DataProcessing/train_csi.py train
```

This uses the same StandardScaler + RBF-SVM approach as Demo #2, with scaling
fit only on training rows. Each default 1000-row recording contributes its first
750 rows to training, a 50-row excluded gap, and its last 200 rows to testing.
Outputs go to `DataProcessing/ml_results`: saved model, evaluation, confusion
matrix, and per-row test predictions. Position labels come from CSV filenames.
For other positions, pass `--files ../Data/A.csv ../Data/B.csv`; these paths
are relative to `DataProcessing`, not the shell. Do not include mystery.csv in
training. The default split needs at least 65 rows per position.

## 4. Predict a mystery position

Return the receiver to one of the known positions. Set `LABEL = "mystery"` in
`Collector/collect.py`, then take a NEW recording and predict:

```powershell
python Collector/collect.py
python DataProcessing/train_csi.py predict
```

Read `DataProcessing/ml_results/mystery_summary.json` for
`most_similar_position`, vote counts, and vote fraction. Per-row predictions
are in `mystery_predictions.csv`. Tied top votes produce a null position and a
list of tied positions. `--mystery ../Data/another.csv` selects another recording.
The model always chooses among known positions; vote fraction is agreement,
not a calibrated probability. It does not recognize an unseen position.

## Measurement protocol and features

The laptop opens the stationary transmitter's 115200-baud serial port. The
transmitter runs the dedicated Wi-Fi AP and a TCP listener on port 3333. The
moving receiver connects to that AP and listener. It sends `HELLO` every second;
the transmitter answers `WELCOME <session>`, and the receiver acknowledges
`READY <session>`. Missing traffic for five seconds causes a reconnect. Wi-Fi
retries continue while the AP is unavailable; TCP connection retries run once
per second after Wi-Fi association.

```mermaid
sequenceDiagram
    participant PC as Laptop
    participant TX as Fixed transmitter (serial gateway)
    participant RX as Moving receiver
    RX->>TX: Connect Wi-Fi/TCP; HELLO (retry)
    TX->>RX: WELCOME session
    RX->>TX: READY session
    PC->>TX: Serial READY
    TX->>RX: WELCOME new session
    RX->>TX: READY new session
    TX->>PC: Serial READY
    PC->>TX: Serial PING id
    TX->>RX: CAPTURE session id
    RX->>RX: Arm local CSI capture
    RX->>TX: ARMED session id
    TX->>PC: Serial ARMED id
    TX->>RX: Padded PROBE traffic
    RX->>RX: Capture next eligible transmitter frame
    RX->>TX: RESULT session id + full CSI JSON
    TX->>PC: Serial CSI JSON
```

A serial `READY` starts a new host session and waits for receiver acknowledgment;
Python waits up to 20 seconds. The transmitter only accepts measurements when
both sides are ready. It generates padded probe traffic every 100 ms after the
receiver reports that capture is armed. The receiver returns complete CSI JSON
or `CSI_TIMEOUT` after two seconds. The gateway allows four seconds for a
receiver result, within Python's five-second measurement deadline. TCP framing
preserves the entire JSON response instead of sending a potentially oversized
UDP datagram. The transmitter forwards receiver data; it never measures CSI.

Each radio message carries the current session and request ID. A new host session
or radio reconnection cancels old capture state; stale-session results are ignored.
Duplicate active IDs do not start another capture, and the last completed ID
returns its cached terminal response. A receiver disconnect during measurement
returns an error and leaves only partial recording files. Start a new collection
session after restoring the link; failed requests are never counted as samples.

Capture accepts only the connected transmitter MAC, channel 6 (or the configured
channel), HT20 packets, and 128-byte LLTF CSI. Both boards enable 802.11b/g/n
with HT20 bandwidth; the capture filter still accepts only HT packets;
only LLTF collection is enabled to keep a fixed feature layout. All signed CSI
bytes are retained in JSON with imaginary/real ordering and receive metadata.
The CSV has 64 imaginary/real pairs. Its first four byte positions are always
zeroed consistently to exclude potentially invalid first-word bytes; indices
are preserved.
The collector rejects malformed samples, changed transmitter identity, and
changes to packet mode within a recording. Never mix Demo #2 and Demo #3 data
or reuse the old model: the transmitter and feature configuration have changed.

CSI callbacks do not contain application payload/request IDs. A sample is the
next eligible transmitter frame after arming, not proof of CSI for one exact
probe packet. Request IDs associate host transactions, not individual RF frames.
The callback copies a bounded sample to a queue; serialization runs in loop().

## Validation and hardware acceptance

```powershell
pio run -d Radio -e receiver -e transmitter
python -m unittest discover -s tests -v
```

Host tests cover fragmented serial responses, validation, timeout handling,
file protection, independent recording/classifier state, and collection through
training/mystery prediction using synthetic CSI. Firmware is checked with both
PlatformIO builds. These checks do not verify radio reception or localization
accuracy; those still require the two boards.

Hardware acceptance: first record a short 10-sample session and verify that
JSON reports the transmitter MAC, expected channel, and 128 CSI bytes. Power off
the receiver during capture: the gateway must report an error and collection
must fail without publishing a CSV. Then verify that the receiver reconnects
automatically when power returns. Also test starting the receiver before the
transmitter and restarting the transmitter while the receiver remains powered.
Then collect the four full position recordings, train, and record fresh mystery
visits at every known position. Compare predictions to the concealed true labels
and report repeated-visit accuracy. A within-recording test alone cannot establish
fresh-visit performance. Matching Demo #2 accuracy remains an empirical question.
If CSI times out, check both boards' power, matching radio settings, board type,
and positioning before collecting the dataset.

SDK references: [ESP32 receiver CSI guide](https://docs.espressif.com/projects/esp-idf/en/v4.4.7/esp32/api-guides/wifi.html#wi-fi-channel-state-information)
and [Espressif SoftAP example](https://github.com/espressif/esp-idf/tree/v4.4.7/examples/wifi/getting_started/softAP).


## Entry points and code organization

Run `Collector/collect.py` to collect data. Run `DataProcessing/train_csi.py`
to train or predict. The Python files under `helpers/` support those scripts.

For firmware, `Radio/src/receiver.cpp` and `Radio/src/transmitter.cpp` are the
two entry points. Select the matching PlatformIO environment when building or
flashing. Supporting C++ implementations are under `src/components/`, with
matching headers under `include/components/`.

```text
Collector/
  collect.py                 RUN THIS to collect measurements
  helpers/
    protocol.py              Serial gateway handshake and measurement requests
    schema.py                CSI validation and feature masking
    recording.py             Recording files and collection loop
DataProcessing/
  train_csi.py               RUN THIS to train or predict
  helpers/
    classifier.py            Dataset split, SVM training, and predictions
Radio/
  platformio.ini             Select receiver or transmitter environment
  src/
    receiver.cpp             RECEIVER ENTRY POINT: setup() and loop()
    transmitter.cpp          TRANSMITTER ENTRY POINT: setup() and loop()
    components/
      TransmitterApp.cpp     Laptop commands, receiver handshake, probe/CSI relay
      ReceiverApp.cpp        Repeated handshake, local capture, Wi-Fi CSI return
      CsiCapture.cpp         CSI callback, filtering, and sample queue
      SerialCommands.cpp     Bounded serial/TCP line reading and writing
      Protocol.cpp           Request ID parsing and JSON responses
      RadioLink.cpp          Wi-Fi/TCP connection and receiver reconnects
  include/
    radio_config.h           Board-link settings
    components/              Matching class headers
```

Start with an entry point, then follow the classes it uses. Edit `radio_config.h`
for board-link settings and `CONFIG` in `train_csi.py` for model settings.
All files stay inside `Demo_3_1`. Collection settings are edited directly in
`collect.py`; training and prediction still use the commands shown above.
