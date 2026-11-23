"""Live seven-tile position heat map. Run: python -m Demo_7_2.dashboard 192.168.4.1

Uses existing Demo 7_0 board firmware. Fits the selected classifier at startup,
collects one shared live stream, and keeps predictions in memory only.
"""

import argparse
from collections import Counter, deque
import math
from pathlib import Path
import threading
import time

from dash import Dash, Input, Output, html, dcc

from Demo_7_0.Server.ping import stream_captures
from .live_model import DISTANCES, PositionModel


class PredictionWindow:
    def __init__(self, window=200, stale_after=3.0):
        self.window, self.stale_after = window, stale_after
        self.history = deque(maxlen=window)
        self.lock = threading.Lock()
        self.last_packet_at = None
        self.total, self.skipped = 0, 0
        self.status = "Waiting for live packets"

    def add(self, prediction):
        now = time.monotonic()
        with self.lock:
            if self.last_packet_at is not None and now - self.last_packet_at > self.stale_after:
                self.history.clear()
            self.history.append((now, prediction))
            self.last_packet_at = now
            self.total += 1
            self.status = "Receiving"

    def reject(self, message):
        with self.lock:
            self.skipped += 1
            self.history.clear()
            self.last_packet_at = None
            self.status = message

    def set_status(self, message):
        with self.lock:
            self.status = message

    def reset(self):
        with self.lock:
            self.history.clear()
            self.last_packet_at = None
            self.status = "Window cleared; waiting for new predictions"

    def snapshot(self):
        now = time.monotonic()
        with self.lock:
            age = None if self.last_packet_at is None else now - self.last_packet_at
            stale = age is not None and age > self.stale_after
            rows = [] if stale else list(self.history)
            counts = Counter(label for _, label in rows)
            maximum = max(counts.values(), default=0)
            return dict(counts=[counts[index] for index in range(7)], size=len(rows),
                        winners=[index for index in range(7) if counts[index] == maximum] if maximum else [],
                        latest=rows[-1][1] if rows else None,
                        span=rows[-1][0] - rows[0][0] if len(rows) > 1 else 0,
                        total=self.total, skipped=self.skipped, age=age, stale=stale,
                        status="Signal paused; waiting for fresh packets" if stale else self.status)


def collect(state, stop, model, tx_ip, port, timeout):
    while not stop.is_set():
        try:
            for packet in stream_captures(tx_ip, port, timeout=timeout):
                if stop.is_set():
                    return
                try:
                    state.add(model.predict(packet))
                except ValueError as exc:
                    state.reject(str(exc))
        except (OSError, TimeoutError) as exc:
            state.set_status(f"Connection interrupted: {exc}. Retrying…")
            if stop.wait(1):
                return


def tile_color(share):
    low, high = (19, 34, 52), (45, 212, 191)
    values = [round(a + share * (b - a)) for a, b in zip(low, high)]
    return f"rgb({values[0]}, {values[1]}, {values[2]})"


def create_app(state, model, refresh_ms=200):
    app = Dash(__name__, title="Demo 7_2 · Live position", update_title=None,
               assets_folder=str(Path(__file__).resolve().parent / "assets"))
    app.layout = html.Main([
        html.Div("DEMO 7_2 / LIVE INFERENCE", className="eyebrow"),
        html.H1("Live position"),
        html.P(f"Seven distances. Last {state.window} packet predictions.", className="subtitle"),
        html.Section([
            html.Div([html.Div("Most predicted distance", className="small-label"),
                      html.Div(id="position", className="position"),
                      html.Div(id="agreement", className="muted")]),
            html.Div([html.Div(id="connection", role="status", className="connection"),
                      html.Div(id="window-status", className="muted"),
                      html.Div(id="latest", className="muted")], className="summary-right"),
        ], className="summary"),
        html.Div(id="distance-tiles", className="tiles", role="group", **{"aria-label": "Predicted distance distribution"}),
        html.Div([html.Span("Fewer predictions"), html.Span(className="color-scale"), html.Span("More predictions")], className="legend"),
        html.P("Each tile shows how many packets were classified at that distance. Brightness represents its share of the window.", className="explanation"),
        html.Div([html.Button("Clear window", id="clear-window", n_clicks=0),
                  html.Span(id="clear-feedback", role="status")], className="actions"),
        html.Div(id="capture-details", className="muted"),
        html.Footer([
            html.P(f"Logistic regression · CSI amplitude + RSSI / AGC / FFT · trained on {model.training_packets:,} packets from {model.training_recordings} recordings."),
            html.P("One training session. The 0.5 m position was difficult in the recorded experiments. Tile percentages are prediction votes, not calibrated confidence."),
        ]),
        dcc.Interval(id="refresh", interval=refresh_ms, n_intervals=0),
    ])

    @app.callback(Output("distance-tiles", "children"), Output("position", "children"),
                  Output("agreement", "children"), Output("connection", "children"),
                  Output("window-status", "children"), Output("latest", "children"),
                  Output("capture-details", "children"), Input("refresh", "n_intervals"))
    def refresh(_):
        snapshot = state.snapshot()
        size = snapshot["size"]
        tiles = []
        for index, distance in enumerate(DISTANCES):
            count = snapshot["counts"][index]
            share = count / size if size else 0
            tiles.append(html.Div([
                html.Div(f"{distance:.1f} m", className="tile-distance"),
                html.Div(f"{share:.0%}" if size else "—", className="tile-share"),
                html.Div(f"{count} packets", className="tile-count"),
                html.Div(html.Div(style={"width": f"{100 * share:.2f}%"}), className="tile-bar"),
            ], className="tile winner" if index in snapshot["winners"] else "tile",
                style={"backgroundColor": tile_color(share)},
                **{"aria-label": f"{distance:.1f} metres: {count} of {size} predictions"}))
        winners = snapshot["winners"]
        position = (f"{DISTANCES[winners[0]]:.1f} m" if len(winners) == 1 else
                    " / ".join(f"{DISTANCES[index]:.1f} m" for index in winners) if winners else "Waiting…")
        agreement = (f"{max(snapshot['counts']) / size:.0%} of the window" + (" · tied" if len(winners) > 1 else "")) if size else "Move into position while live packets arrive"
        latest = f"Latest prediction: {DISTANCES[snapshot['latest']]:.1f} m" if size else "No fresh prediction"
        age = "no packets yet" if snapshot["age"] is None else f"last accepted packet {snapshot['age']:.1f}s ago"
        details = f"{snapshot['total']:,} predictions since startup · {snapshot['skipped']:,} incompatible packets skipped · {age}"
        return (tiles, position, agreement, snapshot["status"],
                f"Window: {size}/{state.window} packets · {snapshot['span']:.1f}s of movement", latest, details)

    @app.callback(Output("clear-feedback", "children"), Input("clear-window", "n_clicks"), prevent_initial_call=True)
    def clear(_):
        state.reset()
        return "Window cleared"

    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tx_ip", nargs="?", default="192.168.4.1")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--dashboard-port", type=int, default=8050)
    parser.add_argument("--window", type=int, default=200)
    parser.add_argument("--refresh-ms", type=int, default=200)
    parser.add_argument("--timeout", type=float, default=10)
    parser.add_argument("--stale-after", type=float, default=3)
    parser.add_argument("--cleaned", type=Path)
    parser.add_argument("--raw-dir", type=Path)
    args = parser.parse_args()
    if not all(1 <= port <= 65535 for port in (args.port, args.dashboard_port)):
        parser.error("ports must be 1..65535")
    if args.window < 1 or args.refresh_ms < 50:
        parser.error("window must be positive and refresh-ms at least 50")
    if any(not math.isfinite(value) or value <= 0 for value in (args.timeout, args.stale_after)):
        parser.error("timeout and stale-after must be finite and positive")
    print("Fitting amplitude + signal logistic regression on the recorded training data…", flush=True)
    try:
        model = PositionModel(args.cleaned, args.raw_dir)
    except (OSError, ValueError, KeyError) as exc:
        parser.exit(1, f"Could not prepare the live model: {exc}\n")
    print(f"Ready: {model.training_packets:,} training packets, {model.training_recordings} recordings.", flush=True)
    state = PredictionWindow(args.window, args.stale_after)
    app = create_app(state, model, args.refresh_ms)
    stop = threading.Event()
    worker = threading.Thread(target=collect, args=(state, stop, model, args.tx_ip, args.port, args.timeout), daemon=True)
    worker.start()
    try:
        print(f"Live position dashboard: http://127.0.0.1:{args.dashboard_port}", flush=True)
        app.run(host="127.0.0.1", port=args.dashboard_port, debug=False, use_reloader=False)
    finally:
        stop.set()
        worker.join(timeout=args.timeout + 1)


if __name__ == "__main__":
    main()
