# Demo 5_1 — live CSI dashboard and I/Q output

Uses Demo 4_2's architecture:

```text
Laptop (Server) --PING--> TX access point --ARM / PROBE--> RX
Laptop (DataProcessing) <--CSI JSON over UDP------------- RX
```

TX hosts the Wi-Fi network, RX joins it automatically, and the laptop requests
one numbered capture at a time. The server validates each response and passes it
directly to data processing, which prints every I/Q pair immediately. Collection
runs continuously by default; no recording or offline processing step is needed.
The ARM/ARMED/DONE handshake, retries, cached responses, and CSI settings are
unchanged from Demo 4_2. This is sequential live capture, not a fixed-rate stream.

## Run

From the repository root (`capstone-g12`):

### Dashboard

Install the dashboard dependencies before connecting to the radio Wi-Fi:

```powershell
python -m venv Demo_5_1/.venv
Demo_5_1/.venv/Scripts/python.exe -m pip install -r Demo_5_1/requirements.txt
```

After flashing the boards and joining their network (instructions below), start
collection and the dashboard together:

```powershell
Demo_5_1/.venv/Scripts/python.exe -m Demo_5_1.DataProcessing.dashboard 192.168.4.1
```

Open **http://127.0.0.1:8050**. One background collector continuously pings the TX;
the browser updates one Plotly chart every 200 ms. Every capture contributes to
the statistics, even if multiple captures arrive between browser refreshes.
Opening more tabs does not create more collectors. Plotly assets are served
locally, so the dashboard works on the radio Wi-Fi without internet access.

- Blue bars: mean amplitude over the last **200 captures**, in raw CSI units.
  Each capture's amplitude is `sqrt(I² + Q²)` before averaging.
- Amber whiskers: each index's **10th to 90th percentile** amplitude over the same
  window, using linear interpolation between adjacent sorted samples.
- Default indices: **0–191** (192 pairs). To include index 192, use `--pairs 193`;
  an index without data stays empty.
- Short frames omit missing indices from the statistics; they do not add zeros.
  An index stays visible while it has valid samples in the window, then becomes empty.
  History expires by capture, including frames where a particular index is absent.
- The window fills from the first capture. **Reset window** clears its history
  while collection continues. Capture count remains cumulative.
- The status line shows packet ID, window fill, received pair count, and data age.
  On timeout or a network error, the collector reports the issue and retries
  after one second while the chart retains the last data.

Use `--window 200`, `--refresh-ms 200`, `--dashboard-port 8050`, `--port 5000`,
or `--timeout 10` to override the defaults. Press Ctrl+C in the terminal to stop
both the server and collection. Radio firmware is unchanged by the dashboard.
Indices remain raw buffer pair offsets across training fields, including the
potentially invalid first word; this dashboard does not decode subcarrier IDs.

### Radio setup and terminal-only output

1. Pick one transmitter and one receiver. Choose the PlatformIO environment for
   each board and role. You can mix board types because all targets use the same
   capture protocol.

   | Board | Transmitter | Receiver |
   | --- | --- | --- |
   | ESP32-U (`esp32dev`) | `tx_esp32u` | `rx_esp32u` |
   | ESP32 Pico | `tx_pico` | `rx_pico` |
   | ESP32-S3 WROOM | `tx_wroom` | `rx_wroom` |

   To flash an ESP32-U transmitter and a WROOM receiver, use these commands:

   ```powershell
   pio run -d Demo_5_1/Radio -e tx_esp32u -t upload --upload-port COM3
   pio run -d Demo_5_1/Radio -e rx_wroom -t upload --upload-port COM4
   ```

   Replace the COM ports with your devices' ports. The serial monitor uses 115200 baud.

2. Connect the laptop to `CAPSTONE_DEMO_5_1`, password `capstone51`.
   Wait for RX to register on the transmitter's serial monitor.

3. Run the live processor with Python 3 (standard library only):

   ```powershell
   python -m Demo_5_1.DataProcessing.live_csi 192.168.4.1
   ```

   Use the TX IP shown in its serial monitor if different. Press Ctrl+C to stop.
   To stop automatically after ten captures:

   ```powershell
   python -m Demo_5_1.DataProcessing.live_csi 192.168.4.1 --count 10
   ```

   `--port` defaults to 5000 and `--timeout` to 10 seconds per capture. A missing
   response is retried every 250 ms with the same packet ID; a timeout exits with
   an error. Allow Python's local UDP traffic through the laptop firewall.

## Output

```text
packet_id=123 pairs=3 (I,Q) 0:(12, -3) 1:(7, 4) 2:(-2, 0)
```

One flushed line per capture, with zero-based pair indices. ESP32 sends bytes as
`[imaginary, real, ...]`; the processor prints `(I=real, Q=imaginary)`. All pairs
are retained, including any invalid first word, matching Demo 4_2. Indices are
raw buffer pair offsets across training fields, not decoded subcarrier IDs.

`Radio/` holds the two firmware roles and shared UDP protocol, `Server/ping.py`
handles capture transport, and `DataProcessing/live_csi.py` handles live output.
The original raw JSON collector is also available with
`python -m Demo_5_1.Server.ping 192.168.4.1 --count 10`.

Run the host-side checks with:

```powershell
Demo_5_1/.venv/Scripts/python.exe -m unittest discover -s Demo_5_1/tests -v
```
