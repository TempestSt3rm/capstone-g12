"""Fit the selected amplitude + signal classifier for live inference.

The same VarianceThreshold -> StandardScaler -> LogisticRegression pipeline
used in the experiments is fitted on all accepted recordings at startup.
Training and live packets share the exact feature builder. Nothing is recorded.
"""

import json
from pathlib import Path

import numpy as np
from sklearn.feature_selection import VarianceThreshold
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler


DEMO = Path(__file__).resolve().parent
DISTANCES = tuple(index / 2 for index in range(1, 8))
LAYOUT_FIELDS = ("csi_len", "sig_mode", "bandwidth", "stbc", "csi_config",
                 "channel", "secondary_channel", "antenna")


class PositionModel:
    def __init__(self, cleaned=None, raw_dir=None):
        cleaned = Path(cleaned) if cleaned else DEMO / "data/cleaned/samples.jsonl"
        raw_dir = Path(raw_dir) if raw_dir else DEMO / "data/raw"
        with cleaned.open(encoding="utf-8") as stream:
            rows = [json.loads(line) for line in stream]
        if not rows:
            raise ValueError("No cleaned training packets; run python -m Demo_7_2.prepare first")
        if len({row["layout_id"] for row in rows}) != 1:
            raise ValueError("Live classifier requires one CSI layout in the training data")
        sources = {}
        for filename in sorted({row["source"]["file"] for row in rows}):
            path = raw_dir / filename
            if path.resolve().parent != raw_dir.resolve():
                raise ValueError("Training source must be directly inside the raw-data folder")
            with path.open(encoding="utf-8-sig") as stream:
                sources[filename] = {line: json.loads(text) for line, text in enumerate(stream, 1)}
        packets = [sources[row["source"]["file"]][row["source"]["line"]] for row in rows]
        for row, packet in zip(rows, packets):
            if (row["target"]["distance_m"] != packet.get("distance_m") or
                    row["group"]["recording_id"] != packet.get("recording_id")):
                raise ValueError("Cleaned/raw provenance mismatch; rerun prepare")
        self.layout = {key: packets[0].get(key) for key in LAYOUT_FIELDS}
        self.exclude_first_word = any(packet.get("first_word_invalid") is True for packet in packets)
        X = np.asarray([self.features(packet) for packet in packets], dtype=np.float64)
        distances = [row["target"]["distance_m"] for row in rows]
        if any(type(distance) not in (float, int) or distance not in DISTANCES for distance in distances):
            raise ValueError("Training labels must be distances from 0.5 to 3.5 m")
        y = np.asarray([DISTANCES.index(distance) for distance in distances])
        if set(y) != set(range(7)):
            raise ValueError("Collect training packets at all seven distances first")
        self.pipeline = make_pipeline(
            VarianceThreshold(), StandardScaler(),
            LogisticRegression(C=1.0, max_iter=3000, class_weight="balanced", random_state=42),
        )
        self.pipeline.fit(X, y)
        self.training_packets = len(rows)
        self.training_recordings = len({row["group"]["recording_id"] for row in rows})

    def features(self, packet):
        for key, expected in self.layout.items():
            if packet.get(key) != expected:
                raise ValueError(f"Radio layout mismatch: {key}. Restore the training radio settings.")
        if packet.get("rx_state") != 0 or packet.get("gain_available") is not True:
            raise ValueError("Packet has an RX error or missing AGC/FFT gain")
        raw = packet.get("csi_raw")
        if (not isinstance(raw, list) or len(raw) != self.layout["csi_len"] or len(raw) % 2 or
                any(type(value) is not int or not -128 <= value <= 127 for value in raw)):
            raise ValueError("Invalid CSI buffer")
        if type(packet.get("first_word_invalid")) is not bool:
            raise ValueError("Missing CSI validity flag")
        if packet["first_word_invalid"] and not self.exclude_first_word:
            raise ValueError("Invalid first CSI word; this model was trained with valid first words")
        iq = np.asarray(raw[4:] if self.exclude_first_word else raw, dtype=np.float64)
        amplitude = np.hypot(iq[0::2], iq[1::2])
        signal = []
        for key, low, high in (("rssi", -128, 127), ("agc_gain", 0, 255), ("fft_gain", -128, 127)):
            value = packet.get(key)
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"Invalid signal metadata: {key}")
            signal.append(value)
        # Full amplitude vector first, followed by RSSI, AGC, FFT, exactly as in
        # amplitude_and_signal in the offline experiments. MCS is not an input.
        return np.concatenate((amplitude, signal))

    def predict(self, packet):
        return int(self.pipeline.predict(self.features(packet).reshape(1, -1))[0])
