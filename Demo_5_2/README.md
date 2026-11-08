# Demo 5_2 — ESP32-U TX, Pico RX1, WROOM RX2

The ESP32-U transmitter hosts the Wi-Fi network. RX1 is a Pico and RX2 is
an ESP32-S3 WROOM.
Each laptop PING uses one packet ID for this sequence:

```text
Laptop → TX: PING
TX → RX1 Pico: ARM / PROBE
RX1 → Laptop: CSI (receiver_id=1)
Laptop → TX: RECEIVED 1
TX → RX2 WROOM: ARM / PROBE
RX2 → Laptop: CSI (receiver_id=2)
Laptop → TX: RECEIVED 2
```

The next cycle starts after both responses arrive. TX waits for the laptop
to acknowledge each receiver before starting the next. Receiver identities are compiled into their
firmware; DHCP address assignment and boot order do not change the order.
The access point allows three stations: the laptop and both receivers.

## Flash the three boards

Run from the repository root (`capstone-g12`). Replace the COM ports below.
The default environment is `tx_esp32u` (`board = esp32dev`).

```powershell
pio run -d Demo_5_2/Radio -e tx_esp32u -t upload --upload-port COM3
pio run -d Demo_5_2/Radio -e rx1_pico -t upload --upload-port COM4
pio run -d Demo_5_2/Radio -e rx2_wroom -t upload --upload-port COM5
```

Use the updated Demo 5_2 firmware on all three boards. RX2 uses the
`freenove_esp32_s3_wroom` board definition. Serial monitors use 115200 baud.
The transmitter logs `RX1 registered` and `RX2 registered` with their IPs.
Connect the laptop to **CAPSTONE_DEMO_5_2**, password **capstone52**.

## Run the dashboard

Install dependencies before joining the radio Wi-Fi:

```powershell
python -m venv Demo_5_2/.venv
Demo_5_2/.venv/Scripts/python.exe -m pip install -r Demo_5_2/requirements.txt
```

Start the dashboard and collection together:

```powershell
Demo_5_2/.venv/Scripts/python.exe -m Demo_5_2.DataProcessing.dashboard 192.168.4.1
```

Open **http://127.0.0.1:8050**. Use the TX IP from its serial monitor if different.
No separate ping process is needed. Assets are served locally without internet.

The dashboard contains two graphs: **RX1 · Pico** and **RX2 · WROOM**:

- 188 bars indexed 4–191, showing mean amplitude `sqrt(I² + Q²)`.
  Indices 0–3 are hidden from both graphs; captured data retains all pairs.
- Whiskers show the 10–90th percentile over a rolling **200-capture** window.
- Each receiver has its own history, status, packet ID, and capture count.
- Browser refresh is every 200 ms. Collection runs independently of browser tabs.
- **Reset window** clears both histories while collection continues.

Options: `--window 200`, `--pairs 192`, `--refresh-ms 200`,
`--dashboard-port 8050`, `--port 5000`, and `--timeout 10`.
Stop the process with Ctrl+C. Stop the Demo 5_1 dashboard first if using port 8050.

Pair indices remain raw buffer offsets across training fields, including any
invalid first word. Missing indices are excluded from statistics, not filled
with zero. Percentiles use linear interpolation between adjacent sorted values.

## Retries and missing receivers

Both receivers must register before TX starts a new cycle. Each receiver sends
`HELLO 1` or `HELLO 2`. TX selects only one receiver at a time, using the same CSI
settings as Demo 5_1. A sample is the next eligible AP frame after arming; its
specific Wi-Fi frame is not proven to match the PROBE payload.

CSI JSON responses contain `packet_id`, `receiver_id`, and `csi`. The laptop
validates each response and sends `RECEIVED <packet_id> <receiver_id>` to TX.
TX repeats ARM/PROBE until the laptop acknowledges. RX caches each response,
so retries resend the same sample. The laptop acknowledges duplicates without
adding them to a graph again. Later receivers cannot advance the laptop before earlier ones.

Each TX stage has a four-second timeout. The laptop retries the same PING every
250 ms and allows ten seconds for the whole cycle. A timeout reports the missing
receiver; the dashboard keeps data already received and retries a fresh cycle
after one second. Data stays in its receiver's graph. Receivers update immediately,
so their histories can contain a partial cycle if a later receiver fails.

## Terminal output and checks

For I/Q output instead of the dashboard (count is complete two-receiver cycles):

```powershell
Demo_5_2/.venv/Scripts/python.exe -m Demo_5_2.DataProcessing.live_csi 192.168.4.1 --count 10
```

For raw JSON use `-m Demo_5_2.Server.ping` with the same arguments.

```powershell
Demo_5_2/.venv/Scripts/python.exe -m unittest discover -s Demo_5_2/tests -v
```

Host checks exercise UDP sequencing, retries, partial captures, independent
statistics, and both Dash graph updates. The native firmware check uses `g++`
to test the actual transmitter sequence controller (skipped if unavailable).
