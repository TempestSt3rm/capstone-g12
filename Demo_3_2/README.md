# Demo 3.2: sequential CSI collection over the laptop hotspot

Demo_3_1 preserves the USB host connection. Demo_3_2 uses TCP for all measurement
commands and returned CSI. The laptop remains connected to its upstream Wi-Fi
as a station and provides a private 2.4 GHz hotspot to both boards. Internet is
not required by the collector; it can remain available through the upstream link.

```text
Laptop: upstream Wi-Fi STA + private hotspot AP
    collect.py --TCP :3334--> fixed transmitter ESP32-S3
                                  | TCP :3333 through laptop hotspot
                                  v
                             moving receiver ESP32-PICO
                                  | CSI result
                                  v
                             transmitter --TCP--> collect.py
```

The application chain is laptop -> transmitter -> receiver -> transmitter ->
laptop. Both ESP32s are stations; neither board creates an AP. The hotspot must
allow traffic between its clients. All boards must remain within hotspot range.

## Hotspot and addressing

In Windows Settings -> Network & internet -> Mobile hotspot, share the existing
Wi-Fi connection over Wi-Fi and select **2.4 GHz**. The screenshot's **Any available**
setting may choose a band the ESP32-PICO cannot use. Adapter/driver support is
required for simultaneous upstream Wi-Fi and hotspot operation.

The credentials supplied for this laptop are configured directly in
`Radio/include/radio_config.h`:

- SSID: `JEFFREY_ASUS_TEMPEST`
- Password: `Capstone_G12`
- Laptop hotspot gateway: `192.168.137.1`, subnet `255.255.255.0` (verified with ipconfig).
- Transmitter: static `192.168.137.2`.
- Receiver: DHCP from the laptop.
- Receiver connection to transmitter: TCP `3333`.
- Collector connection to transmitter: TCP `3334`.

The transmitter IP must be unused by other hotspot clients and must not be
allocated to another client by DHCP. If it conflicts, choose an unused address
on the hotspot subnet and update `transmitterIp` in `radio_config.h` and `HOST`
in `Collector/collect.py`. If the hotspot subnet changes, also update gateway
and subnet in `radio_config.h`. Reflash both roles after radio config changes.
Wi-Fi association discovers the current hotspot channel; no hard-coded channel
6 assumption remains. Recollect data if the channel or physical setup changes.

Windows hotspot setup reference:
[Microsoft Mobile hotspot guide](https://support.microsoft.com/en-us/windows/experience/connectivity-networking/use-your-windows-device-as-a-mobile-hotspot).

## Flash and collect

Run from `Demo_3_2`:

```powershell
python -m pip install -r requirements.txt
pio run -d Radio -e transmitter -t upload --upload-port COM4
pio run -d Radio -e receiver -t upload --upload-port COM5
```

Replace the COM ports with the actual ports. Board targets remain
`freenove_esp32_s3_wroom` (transmitter) and `pico32` (ESP32-PICO-KIT receiver).
USB is used for flashing and optional 115200-baud diagnostics. Both boards may
then use independent power. No serial port is opened by collect.py.

Keep the transmitter fixed. Move the receiver to the position being recorded.
Edit `Collector/collect.py`: `LABEL`, `SAMPLE_COUNT`, and optionally
`SAMPLE_TIMEOUT_MS` (per measurement). For a first smoke test use 10 samples
and a fresh label. Then run:

```powershell
python Collector/collect.py
```

The TCP connection has a 10-second connection timeout. The collector retries
READY for up to 20 seconds until the transmitter confirms the receiver handshake.
Receiver Wi-Fi and TCP reconnect automatically. Power-on order does not matter.
If a link fails during a recording, collection aborts; restore the link and use
a new label or move the partial files before rerunning. It never silently counts
a failed measurement or appends data across reconnects.

Record known labels `x0y0`, `x0y1`, `x1y0`, `x1y1` in separate runs. Output is
`Data/<label>.bin` (framed binary CSI) and `Data/<label>.csv` (model features).
Files remain `.partial` until the full recording succeeds. Existing recordings
and partials are protected against accidental overwrite.

## Exactly one measurement at a time

```mermaid
sequenceDiagram
    participant PC as collect.py
    participant TX as Transmitter STA
    participant RX as Receiver STA
    RX->>TX: TCP connect; HELLO
    TX->>RX: PEER (transmitter STA MAC); WELCOME session
    RX->>TX: READY session
    PC->>TX: TCP connect; READY
    TX->>RX: PEER; WELCOME new session
    RX->>TX: READY session
    TX->>PC: READY
    loop Once for each requested sample
        PC->>TX: COLLECT 1 mask timeout
        TX->>RX: START request ID
        RX->>RX: Arm one CSI measurement
        RX->>TX: ARMED
        TX->>PC: BATCH request ID 1 mask
        TX->>RX: One padded PROBE
        RX->>TX: CSI sample; DONE
        TX->>PC: CSI sample; DONE
        PC->>PC: Validate and save before next request
    end
```

COLLECT is the application-level ping; this uses TCP, not ICMP ping. The
transmitter accepts only one sample per command, holds that sample until the
receiver completes, then relays it. Python waits for the matching DONE before
issuing another COLLECT. This intentionally trades throughput for a simple
sequential chain. Host disconnect cancels acquisition and clears buffered state.
Receiver disconnect, malformed layout, missing/out-of-order IDs, or timeouts fail
the request. A new connection and READY handshake begin a fresh session.

After ARMED, the transmitter queues exactly one application PROBE for that
measurement, then waits for the receiver's CSI and DONE or the existing timeout.
It does not repeat probes while waiting or automatically retry a failed sample.
TCP may retransmit bytes internally as part of reliable transport. Heartbeat
traffic continues so both boards can detect connection loss. An unsent probe is
removed only when its measurement ends or is cancelled; incoming control traffic
does not evict the one pending probe. Partially transmitted frames always finish
to preserve TCP framing. Repeated
heartbeats for the same session are coalesced while queued. A full outgoing queue
reports backpressure without closing a healthy TCP connection; an essential
measurement send that cannot be queued fails the request explicitly. Actual
socket errors or five seconds without send progress still trigger reconnection.

The TCP host stream uses ASCII handshake/command lines followed by binary
`CSI_DATA:` framing. Each frame contains a one-byte LTF mask/control code,
a little-endian uint32 packet ID, and its fixed-size payload. Receiver-to-TX TCP
uses the same header/payload without the host marker. Data packets carry
128 signed CSI bytes per available LTF. The default mask `0b011` selects LLTF
and HT-LTF (256 CSV columns); first-word masking remains consistent with 3.1.
The receiver returns its actual available LTF mask; the requested fields must
all be present. Packet IDs associate transactions, not exact RF packets.

## What RF link is being measured

Ordinary STA-to-STA TCP passes through the laptop AP. Merely capturing its
downlink would measure the laptop's transmitter. To retain a board-to-board RF
measurement, the receiver enables **promiscuous data reception** while remaining
associated and filters CSI to the transmitter's **STA MAC**, delivered in PEER
before the readiness handshake. It listens to the transmitter's original uplink
frames over the air, including the single padded probe sent toward the hotspot.
It rejects laptop-origin frames and other stations. It also requires the current
associated channel, HT20 layout, and the requested LTF fields. The next eligible
frame after arming may be a control/handshake TCP data frame; it is not guaranteed
to be one particular PROBE. Keep both boards on the same hotspot.

Espressif documents promiscuous reception for station CSI in its
[Wi-Fi CSI guide](https://docs.espressif.com/projects/esp-idf/en/v4.4.7/esp32/api-guides/wifi.html#wi-fi-channel-state-information).
This RF behavior still needs verification on the physical board pair. If READY
works but CSI times out, check transmitter-to-receiver RF range, 2.4 GHz,
HT20/HT-LTF availability, and the hotspot's channel/bandwidth. Client isolation
can prevent the initial receiver TCP handshake. No application listener runs
on the laptop, so there is no laptop inbound collector port to open.

## Train and predict

Set `MODE = "train"` in `DataProcessing/train_csi.py`, then run:

```powershell
python DataProcessing/train_csi.py
```

Training reads the known-position CSV files listed in `CSV_FILES`. Use a new
recording with `LABEL = "mystery"` in collect.py. Set `MODE = "predict"` in
train_csi.py, then run:

```powershell
python Collector/collect.py
python DataProcessing/train_csi.py
```

These scripts use settings at the top of the file, without command-line arguments.

Results are in `DataProcessing/ml_results`, including `mystery_summary.json`.
Keep Demo_3_2 data and models separate from other demos. Changed RF topology
requires fresh training and fresh-visit evaluation. Model vote fraction is
agreement among predictions, not calibrated confidence or proof of accuracy.

## Validation and hardware acceptance

### Diagnosing RECEIVER_DISCONNECTED after READY

READY confirms that both TCP connections and the receiver handshake worked at
that moment. RECEIVER_DISCONNECTED means the transmitter then lost the receiver
TCP connection; it does not identify the underlying Wi-Fi, reset, or socket cause.

Both roles now print boot reset reason, Wi-Fi association/IP/disconnect events,
and request/arming diagnostics over serial at 115200 baud. Open a serial monitor
on each board before reproducing the failure. Reset each board to capture its
boot log. Serial monitors can stay open while the Wi-Fi collector runs.

```powershell
pio device monitor --port COM4 --baud 115200
# In a second terminal, with the actual receiver port:
pio device monitor --port COM5 --baud 115200
```

Expected progress is TX `Sending START`, RX `START`, RX `Capture armed`, then TX
`Receiver ARMED`. A new boot banner indicates a restart; a Wi-Fi DISCONNECTED
line includes the SDK's reason code. Socket write/queue errors and heartbeat
timeouts are logged separately. Capture the output from boot through the failure
on both boards. CSI callback channel filtering uses a channel cached by the
application when arming, without calling Wi-Fi APIs from the callback.

Use a fresh collection label after a failed run; the previous `.partial` files
are deliberately retained. Close serial monitors before reflashing.

```powershell
pio run -d Radio -e receiver -e transmitter
python -m unittest discover -s tests -v
```

Tests cover TCP fragmentation/EOF, strictly sequential commands, sample and DONE
validation, overwrite protection, failure partials, and synthetic collection
through training/prediction. When `g++` is available, a native test also exercises
the project's real outgoing queue under probe/heartbeat saturation, ring-buffer
wraparound, and partial TCP writes. It uses no Arduino library replacements.
Builds and host tests do not establish real CSI
reception or localization accuracy.

On hardware: collect 10 samples; verify 10 CSV rows with the expected 256 columns.
Power off the receiver mid-capture and check that only partial recordings remain.
Restore power and collect under a new label. Repeat with transmitter restart,
hotspot off/on, receiver-first startup, and laptop TCP disconnection. Then
record fresh known-position and mystery visits and evaluate actual accuracy.
