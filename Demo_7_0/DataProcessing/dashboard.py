"""One process for live UDP capture and a local Plotly amplitude dashboard."""

import argparse
from collections import deque
import json
import math
import threading
import time

from dash import Dash, Input, Output, State, ctx, dcc, html
import plotly.graph_objects as go

from .live_csi import iq_pairs
from ..Server.ping import stream_captures
from ..Server.recording import LabeledRecorder, POSITIONS_M


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
        amplitudes = [math.hypot(i, q) for i, q in iq_pairs(response["csi_raw"])]
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
            self.metadata = {key: value for key, value in response.items() if key != "csi_raw"}

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


RECEIVER_LABELS = {1: "RX1 · ESP32-S3"}


class MultiWindow:
    def __init__(self, window=200, pairs=512):
        self.receivers = {rid: LiveWindow(window, pairs) for rid in RECEIVER_LABELS}
        self.window = window

    def add(self, response):
        self.receivers[response["rx_id"]].add(response)

    def set_status(self, message):
        for state in self.receivers.values():
            state.set_status(message)

    def reset(self):
        for state in self.receivers.values():
            state.reset()


def collect(state, stop, tx_ip, port, timeout, recorder=None, labeled_recorder=None):
    class RecordingError(Exception):
        pass

    def recording_failed(exc):
        state.set_status(f"Recording failed: {exc}. Collection stopped.")
        if labeled_recorder is not None:
            labeled_recorder.fail(exc)
        stop.set()

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
                if labeled_recorder is not None:
                    try:
                        labeled_recorder.add(response)
                    except OSError as exc:
                        recording_failed(exc)
                        return
                if recorder is not None:
                    try:
                        recorder.add(response)
                    except OSError as exc:
                        recording_failed(exc)
                        return
                state.add(response)
        except RecordingError as exc:
            recording_failed(exc)
            return
        except (OSError, TimeoutError) as exc:
            state.set_status(f"Capture interrupted: {exc}. Retrying…")
            if recorder is not None:
                try:
                    recorder.event(str(exc))
                except OSError as disk_error:
                    recording_failed(disk_error)
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


def create_app(state, refresh_ms=200, recording_path=None, labeled_recorder=None):
    labeled_recorder = labeled_recorder if labeled_recorder is not None else LabeledRecorder()
    app = Dash(__name__, title="Demo 7_0 · Live CSI", update_title=None)
    app.layout = html.Main([
        html.H1("CSI amplitude", style={"marginBottom": "8px"}),
        html.P(f"Window: {state.window} captures · ESP-32U TX / ESP32-S3 RX"),
        html.P("Data is saved only during a 10-second recording started with Record."),
        html.Section([
            html.H2("Collect a labeled set"),
            html.P("Choose your position and orientation. Record saves the next 10 seconds, then stops automatically."),
            html.Div([
                html.Div([
                    html.Label("Distance (m)", htmlFor="position-label"),
                    dcc.Dropdown(id="position-label", options=[{"label": f"{value:.1f} m", "value": value} for value in POSITIONS_M],
                                 value=0.5, clearable=False, searchable=False, style={"minWidth": "150px"}),
                ]),
                html.Div([
                    html.Label("Orientation (optional)", htmlFor="orientation-label"),
                    dcc.Input(id="orientation-label", type="text", value="", maxLength=200,
                              placeholder="e.g. facing TX, 90 degrees", style={"fontSize": "16px", "padding": "8px"}),
                ]),
                html.Button("Record", id="record-set", n_clicks=0,
                            style={"fontSize": "16px", "padding": "10px 24px"}),
            ], style={"display": "flex", "flexWrap": "wrap", "gap": "20px", "alignItems": "end"}),
            html.Div(id="record-action-result", role="alert"),
            html.Div(id="recording-status", role="status", style={"padding": "12px 0"}),
        ], style={"padding": "20px", "backgroundColor": "#edf5ff", "borderRadius": "8px", "marginBottom": "20px"}),
        html.P("All received indices shown. Latest capture, rolling mean, and 10–90% interval are display-only. First invalid bytes are retained."),
        html.Button("Reset window", id="reset-window", n_clicks=0),
        *[html.Section([
            html.H2(label),
            html.Div(id=f"capture-status-{rid}", role="status", style={"padding": "8px 0"}),
            dcc.Graph(id=f"amplitudes-{rid}", figure=make_figure(state.receivers[rid], state.receivers[rid].snapshot()),
                      config={"displaylogo": False, "responsive": True}, style={"height": "60vh", "minHeight": "330px"}),
        ]) for rid, label in RECEIVER_LABELS.items()],
        dcc.Interval(id="refresh", interval=refresh_ms, n_intervals=0),
    ], style={"fontSize": "16px", "fontFamily": "Segoe UI, sans-serif", "maxWidth": "1600px", "margin": "auto", "padding": "28px"})

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
            def gain(key):
                return metadata.get(key, "Waiting") if metadata.get("gain_available") else "Waiting for gain data"

            status = html.Div([
                html.Div(status),
                html.Div([
                    html.Strong(f"AGC gain: {gain('agc_gain')}"),
                    html.Strong(f"FFT gain: {gain('fft_gain')}"),
                    html.Strong(f"RSSI: {metadata.get('rssi', '—')} dBm"),
                    html.Strong(f"Noise floor: {metadata.get('noise_floor', '—')} dBm"),
                ], style={"display": "flex", "flexWrap": "wrap", "gap": "24px", "padding": "16px 0"}),
                html.Details([html.Summary("Packet details"),
                              html.Pre(json.dumps(metadata, indent=2))]),
            ])
            outputs.extend([make_figure(receiver, snapshot), status])
        return outputs

    @app.callback(Output("reset-window", "children"), Input("reset-window", "n_clicks"),
                  prevent_initial_call=True)
    def reset(_):
        state.reset()
        return "Reset window"

    @app.callback(Output("record-action-result", "children"),
                  Input("record-set", "n_clicks"),
                  State("position-label", "value"), State("orientation-label", "value"),
                  prevent_initial_call=True)
    def record(_, label_m, orientation):
        if ctx.triggered_id != "record-set":
            return ""
        try:
            labeled_recorder.start(label_m, orientation or "")
        except (OSError, ValueError) as exc:
            return str(exc)
        return ""

    @app.callback(Output("recording-status", "children"), Output("record-set", "disabled"),
                  Output("position-label", "disabled"), Output("orientation-label", "disabled"),
                  Input("refresh", "n_intervals"), Input("record-action-result", "children"))
    def recording_status(*_):
        snapshot = labeled_recorder.snapshot()
        if snapshot["active"]:
            message = (f"Recording {snapshot['label_m']:.1f} m | {snapshot['orientation'] or 'orientation unspecified'} | "
                       f"{snapshot['remaining']:.1f}s remaining | {snapshot['count']} packets")
        else:
            message = f"{snapshot['message']} | {snapshot['count']} packets | Completed sets: {snapshot['completed_sets']}"
        return (html.Div([html.Strong(message), html.Div(snapshot["path"] or "JSONL folder: " + str(labeled_recorder.directory))]),
                snapshot["active"] or snapshot["unavailable"], snapshot["active"], snapshot["active"])

    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tx_ip", help="TX IP from its serial monitor, usually 192.168.4.1")
    parser.add_argument("--port", type=int, default=5000, help="TX UDP port")
    parser.add_argument("--dashboard-port", type=int, default=8050)
    parser.add_argument("--window", type=int, default=200, help="Rolling window in captures (default: 200)")
    parser.add_argument("--pairs", type=int, default=512, help="Display up to this many raw pair offsets (default: all, up to 512)")
    parser.add_argument("--labeled-dir", help="Folder for 10-second labeled JSONL sets; default: Demo_7_0/data/labeled")
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
    labeled_recorder = LabeledRecorder(args.labeled_dir)
    app = create_app(state, args.refresh_ms, labeled_recorder=labeled_recorder)
    stop = threading.Event()
    worker = threading.Thread(target=collect, args=(state, stop, args.tx_ip, args.port, args.timeout, None, labeled_recorder), daemon=True)
    worker.start()
    try:
        print(f"Live CSI dashboard: http://127.0.0.1:{args.dashboard_port}", flush=True)
        print(f"Record button saves 10-second JSONL sets to: {labeled_recorder.directory}", flush=True)
        # No reloader: it would start a second collector and duplicate requests.
        app.run(host="127.0.0.1", port=args.dashboard_port, debug=False, use_reloader=False)
    finally:
        stop.set()
        labeled_recorder.close()
        worker.join()


if __name__ == "__main__":
    main()
