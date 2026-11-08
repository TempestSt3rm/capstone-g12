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


class LiveWindow:
    """Bounded capture history shared by the collector and browser callbacks."""

    def __init__(self, window=200, pairs=192):
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
                          received_pairs=self.received_pairs, status=self.status,
                          age=None if self.updated_at is None else time.monotonic() - self.updated_at)
        means, p10, p90 = [], [], []
        for index in range(self.pairs):
            values = sorted(row[index] for row in rows if row[index] is not None)
            means.append(math.fsum(values) / len(values) if values else None)
            p10.append(percentile(values, 0.1) if values else None)
            p90.append(percentile(values, 0.9) if values else None)
        return dict(result, mean=means, p10=p10, p90=p90, size=len(rows))


def percentile(sorted_values, fraction):
    """Linearly interpolate between adjacent ranks at (n - 1) * fraction."""
    position = (len(sorted_values) - 1) * fraction
    lower, upper = math.floor(position), math.ceil(position)
    return sorted_values[lower] + (sorted_values[upper] - sorted_values[lower]) * (position - lower)


def collect(state, stop, tx_ip, port, timeout):
    # Only this thread pings. Browser tabs share its history and never trigger captures.
    while not stop.is_set():
        try:
            for response in stream_captures(tx_ip, port, timeout=timeout):
                if stop.is_set():
                    return
                state.add(response)
        except (OSError, TimeoutError) as exc:
            state.set_status(f"Capture interrupted: {exc}. Retrying…")
            if stop.wait(1):
                return


def make_figure(state, snapshot):
    indices = list(range(state.pairs))
    low, high = snapshot["p10"], snapshot["p90"]
    midpoint = [(lo + hi) / 2 if lo is not None else None for lo, hi in zip(low, high)]
    half_range = [(hi - lo) / 2 if lo is not None else None for lo, hi in zip(low, high)]
    figure = go.Figure([
        go.Bar(x=indices, y=snapshot["mean"], name="Mean amplitude",
               marker_color="#0284c7", hovertemplate="Index %{x}<br>Mean amplitude %{y:.2f}<extra></extra>"),
        # Center whiskers on the percentile interval, since a skewed distribution
        # can put the mean outside that interval.
        go.Scatter(x=indices, y=midpoint, mode="markers", name="10–90th percentile",
                   marker=dict(size=3, color="#d97706"),
                   error_y=dict(type="data", array=half_range, visible=True,
                                color="#d97706", thickness=1.5, width=2),
                   customdata=list(zip(low, high)),
                   hovertemplate="Index %{x}<br>10th percentile %{customdata[0]:.2f}<br>90th percentile %{customdata[1]:.2f}<extra></extra>")
    ])
    figure.update_layout(template="plotly_white", margin=dict(l=65, r=25, t=35, b=60),
                         xaxis=dict(title="CSI pair index", range=[-0.75, state.pairs - 0.25], dtick=8),
                         yaxis=dict(title="Amplitude √(I² + Q²) · raw units", rangemode="tozero"),
                         legend=dict(orientation="h", y=1.12), bargap=0.12,
                         uirevision="live-csi", transition_duration=0)
    return figure


def create_app(state, refresh_ms=200):
    app = Dash(__name__, title="Demo 5_1 · Live CSI", update_title=None)
    app.layout = html.Main([
        html.H1("CSI amplitude", style={"marginBottom": "8px"}),
        html.P(f"Window: {state.window} captures"),
        html.Div(id="capture-status", role="status", style={"padding": "16px 0", "fontWeight": "600"}),
        html.Button("Reset window", id="reset-window", n_clicks=0),
        dcc.Graph(id="amplitudes", figure=make_figure(state, state.snapshot()),
                  config={"displaylogo": False, "responsive": True}, style={"height": "68vh"}),
        dcc.Interval(id="refresh", interval=refresh_ms, n_intervals=0),
    ], style={"fontFamily": "Segoe UI, sans-serif", "maxWidth": "1600px", "margin": "auto", "padding": "28px"})

    @app.callback(Output("amplitudes", "figure"), Output("capture-status", "children"),
                  Input("refresh", "n_intervals"))
    def refresh(_):
        snapshot = state.snapshot()
        age = "no data yet" if snapshot["age"] is None else f"last capture {snapshot['age']:.1f}s ago"
        status = (f"{snapshot['status']} | Captures: {snapshot['total']} | "
                  f"Window: {snapshot['size']}/{state.window} | Packet: {snapshot['packet_id']} | "
                  f"Received pairs: {snapshot['received_pairs']} | {age}")
        return make_figure(state, snapshot), status

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
    parser.add_argument("--pairs", type=int, default=192, help="Number of displayed pair indices (default: 0–191)")
    parser.add_argument("--refresh-ms", type=int, default=200)
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535 or not 1 <= args.dashboard_port <= 65535:
        parser.error("ports must be 1..65535")
    if args.window < 1 or not 1 <= args.pairs <= 512 or args.refresh_ms < 50:
        parser.error("window must be positive; pairs must be 1..512; refresh-ms must be at least 50")
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("timeout must be finite and positive")
    state = LiveWindow(args.window, args.pairs)
    app = create_app(state, args.refresh_ms)
    stop = threading.Event()
    worker = threading.Thread(target=collect, args=(state, stop, args.tx_ip, args.port, args.timeout), daemon=True)
    worker.start()
    try:
        print(f"Live CSI dashboard: http://127.0.0.1:{args.dashboard_port}", flush=True)
        # No reloader: it would start a second collector and duplicate requests.
        app.run(host="127.0.0.1", port=args.dashboard_port, debug=False, use_reloader=False)
    finally:
        stop.set()
        worker.join(timeout=1)


if __name__ == "__main__":
    main()
