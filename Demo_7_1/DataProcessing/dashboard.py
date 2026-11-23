"""One process for live UDP capture and a local Plotly amplitude dashboard."""

import argparse
from collections import deque
import math
import threading
import time

from dash import Dash, Input, Output, dcc, html
import plotly.graph_objects as go

from .live_csi import iq_pairs
from ..Server.ping import stream_captures
from ..Server.recording import RawRecorder


class LiveWindow:
    """Bounded capture history shared by the collector and browser callbacks."""

    def __init__(self, window=200, pairs=512):
        if window < 1 or pairs < 1:
            raise ValueError("window and pairs must be positive")
        self.window, self.pairs = window, pairs
        self.history = deque(maxlen=window)
        self.lock = threading.Lock()
        self.total = 0
        self.packet_id = None
        self.updated_at = None
        self.received_pairs = 0
        self.status = "Waiting for first capture"
        self.metadata = {}

    def add(self, response):
        amplitudes = [math.hypot(i, q) for i, q in iq_pairs(response["csi"])]
        received = len(amplitudes)
        amplitudes = amplitudes[:self.pairs]
        amplitudes.extend([None] * (self.pairs - len(amplitudes)))
        with self.lock:
            self.history.append(amplitudes)
            self.total += 1
            self.packet_id = response["packet_id"]
            self.updated_at = time.monotonic()
            self.received_pairs = received
            self.status = "Receiving"
            self.metadata = response.get("metadata", {})

    def set_status(self, message):
        with self.lock:
            self.status = message

    def reset(self):
        with self.lock:
            self.history.clear()

    def snapshot(self):
        with self.lock:
            rows = list(self.history)
            result = dict(total=self.total, packet_id=self.packet_id,
                          received_pairs=self.received_pairs, status=self.status, metadata=dict(self.metadata),
                          age=None if self.updated_at is None else time.monotonic() - self.updated_at)
        means, p10, p90 = [], [], []
        for index in range(self.pairs):
            values = sorted(row[index] for row in rows if row[index] is not None)
            means.append(math.fsum(values) / len(values) if values else None)
            p10.append(percentile(values, 0.1) if values else None)
            p90.append(percentile(values, 0.9) if values else None)
        return dict(result, mean=means, p10=p10, p90=p90, size=len(rows),
                    latest=rows[-1] if rows else [None] * self.pairs)


def percentile(sorted_values, fraction):
    """Linearly interpolate between adjacent ranks at (n - 1) * fraction."""
    position = (len(sorted_values) - 1) * fraction
    lower, upper = math.floor(position), math.ceil(position)
    return sorted_values[lower] + (sorted_values[upper] - sorted_values[lower]) * (position - lower)


RECEIVER_LABELS = {1: "RX1 · Pico", 2: "RX2 · Pico"}


class MultiWindow:
    def __init__(self, window=200, pairs=512):
        self.receivers = {rid: LiveWindow(window, pairs) for rid in RECEIVER_LABELS}
        self.window = window

    def add(self, response):
        self.receivers[response["receiver_id"]].add(response)

    def set_status(self, message):
        for state in self.receivers.values():
            state.set_status(message)

    def reset(self):
        for state in self.receivers.values():
            state.reset()


def collect(state, stop, tx_ip, port, timeout, recorder=None):
    class RecordingError(Exception):
        pass

    def report_config(receiver_id, config):
        print(f"RX{receiver_id} CSI settings: {config}", flush=True)
        if recorder is not None:
            try:
                recorder.receiver_config(receiver_id, config)
            except OSError as exc:
                raise RecordingError(str(exc)) from exc

    # Only this thread pings. Browser tabs share its history and never trigger captures.
    while not stop.is_set():
        try:
            for response in stream_captures(tx_ip, port, timeout=timeout, on_config=report_config):
                if stop.is_set():
                    return
                if recorder is not None:
                    try:
                        recorder.add(response)
                    except OSError as exc:
                        state.set_status(f"Recording failed: {exc}. Collection stopped.")
                        stop.set()
                        return
                state.add(response)
        except RecordingError as exc:
            state.set_status(f"Recording failed: {exc}. Collection stopped.")
            stop.set()
            return
        except (OSError, TimeoutError) as exc:
            state.set_status(f"Capture interrupted: {exc}. Retrying…")
            if recorder is not None:
                try:
                    recorder.event(str(exc))
                except OSError as disk_error:
                    state.set_status(f"Recording failed: {disk_error}. Collection stopped.")
                    stop.set()
                    return
            if stop.wait(1):
                return


def make_figure(state, snapshot):
    length = min(state.pairs, max(snapshot["received_pairs"],
                 max((i + 1 for i, value in enumerate(snapshot["mean"]) if value is not None), default=0)))
    indices = list(range(length))
    low, high = snapshot["p10"][:length], snapshot["p90"][:length]
    midpoint = [(lo + hi) / 2 if lo is not None else None for lo, hi in zip(low, high)]
    half_range = [(hi - lo) / 2 if lo is not None else None for lo, hi in zip(low, high)]
    figure = go.Figure([
        go.Bar(x=indices, y=snapshot["mean"][:length], name="Rolling mean amplitude",
               marker_color="#0284c7", hovertemplate="Index %{x}<br>Mean amplitude %{y:.2f}<extra></extra>"),
        # Center whiskers on the percentile interval, since a skewed distribution
        # can put the mean outside that interval.
        go.Scatter(x=indices, y=midpoint, mode="markers", name="10–90th percentile",
                   marker=dict(size=3, color="#d97706"),
                   error_y=dict(type="data", array=half_range, visible=True,
                                color="#d97706", thickness=1.5, width=2),
                   customdata=list(zip(low, high)),
                   hovertemplate="Index %{x}<br>10th percentile %{customdata[0]:.2f}<br>90th percentile %{customdata[1]:.2f}<extra></extra>"),
        go.Scatter(x=indices, y=snapshot["latest"][:length], mode="lines", name="Latest capture",
                   line=dict(color="#7c3aed", width=1.5))
    ])
    figure.update_layout(template="plotly_white", margin=dict(l=65, r=25, t=35, b=60),
                         xaxis=dict(title="Raw CSI pair offset (all training fields)", tick0=0, dtick=16),
                         yaxis=dict(title="Amplitude √(I² + Q²) · driver units", autorange=True),
                         legend=dict(orientation="h", y=1.12), bargap=0.12,
                         uirevision="live-csi", transition_duration=0)
    return figure


def create_app(state, refresh_ms=200, recording_path=None):
    app = Dash(__name__, title="Demo 7_1 · Live CSI", update_title=None)
    app.layout = html.Main([
        html.H1("CSI amplitude", style={"marginBottom": "8px"}),
        html.P(f"Window: {state.window} captures per receiver · sequential RX1 → RX2"),
        html.P(f"Raw recording: {recording_path}" if recording_path else "Preview — no recording"),
        html.P("All received indices shown. Latest capture, rolling mean, and 10–90% interval are display-only. First invalid bytes are retained."),
        html.Button("Reset window", id="reset-window", n_clicks=0),
        *[html.Section([
            html.H2(label),
            html.Div(id=f"capture-status-{rid}", role="status", style={"padding": "8px 0"}),
            dcc.Graph(id=f"amplitudes-{rid}", figure=make_figure(state.receivers[rid], state.receivers[rid].snapshot()),
                      config={"displaylogo": False, "responsive": True}, style={"height": "43vh", "minHeight": "330px"}),
        ]) for rid, label in RECEIVER_LABELS.items()],
        dcc.Interval(id="refresh", interval=refresh_ms, n_intervals=0),
    ], style={"fontFamily": "Segoe UI, sans-serif", "maxWidth": "1600px", "margin": "auto", "padding": "28px"})

    @app.callback(*[output for rid in RECEIVER_LABELS
                    for output in (Output(f"amplitudes-{rid}", "figure"),
                                   Output(f"capture-status-{rid}", "children"))],
                  Input("refresh", "n_intervals"))
    def refresh(_):
        outputs = []
        for receiver in state.receivers.values():
            snapshot = receiver.snapshot()
            age = "no data yet" if snapshot["age"] is None else f"last capture {snapshot['age']:.1f}s ago"
            status = (f"{snapshot['status']} | Captures: {snapshot['total']} | "
                      f"Window: {snapshot['size']}/{state.window} | Packet: {snapshot['packet_id']} | "
                      f"Received pairs: {snapshot['received_pairs']} | {age}")
            metadata = snapshot["metadata"]
            status += (f" | RSSI: {metadata.get('rssi', '—')} dBm"
                       f" | First word invalid: {metadata.get('first_word_invalid', '—')}"
                       f" | Mode/BW/MCS: {metadata.get('sig_mode', '—')}/{metadata.get('cwb', '—')}/{metadata.get('mcs', '—')}")
            outputs.extend([make_figure(receiver, snapshot), status])
        return outputs

    @app.callback(Output("reset-window", "children"), Input("reset-window", "n_clicks"),
                  prevent_initial_call=True)
    def reset(_):
        state.reset()
        return "Reset window"

    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tx_ip", help="TX IP from its serial monitor, usually 192.168.4.1")
    parser.add_argument("--port", type=int, default=5000, help="TX UDP port")
    parser.add_argument("--dashboard-port", type=int, default=8050)
    parser.add_argument("--window", type=int, default=200, help="Rolling window in captures (default: 200)")
    parser.add_argument("--pairs", type=int, default=512, help="Display up to this many raw pair offsets (default: all, up to 512)")
    parser.add_argument("--output", help="New raw JSONL path; default: Demo_7_1/data/<UTC timestamp>.jsonl")
    parser.add_argument("--refresh-ms", type=int, default=200)
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535 or not 1 <= args.dashboard_port <= 65535:
        parser.error("ports must be 1..65535")
    if args.window < 1 or not 1 <= args.pairs <= 512 or args.refresh_ms < 50:
        parser.error("window must be positive; pairs must be 1..512; refresh-ms must be at least 50")
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("timeout must be finite and positive")
    state = MultiWindow(args.window, args.pairs)
    recorder = RawRecorder(args.output, args.tx_ip, args.port)
    app = create_app(state, args.refresh_ms, str(recorder.path))
    stop = threading.Event()
    worker = threading.Thread(target=collect, args=(state, stop, args.tx_ip, args.port, args.timeout, recorder), daemon=True)
    worker.start()
    try:
        print(f"Live CSI dashboard: http://127.0.0.1:{args.dashboard_port}", flush=True)
        print(f"Raw session: {recorder.path}", flush=True)
        # No reloader: it would start a second collector and duplicate requests.
        app.run(host="127.0.0.1", port=args.dashboard_port, debug=False, use_reloader=False)
    finally:
        stop.set()
        worker.join()
        recorder.close()


if __name__ == "__main__":
    main()
