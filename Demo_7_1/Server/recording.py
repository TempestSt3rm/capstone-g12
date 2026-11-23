"""Append-only raw sessions; display transforms never touch these records."""

from datetime import datetime, timezone
import json
from pathlib import Path
import time
import uuid


class RawRecorder:
    def __init__(self, path=None, tx_ip="192.168.4.1", port=5000):
        self.session_id = str(uuid.uuid4())
        self.receiver_settings = {}
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
        self.path = Path(path) if path else Path(__file__).resolve().parents[1] / "data" / f"{stamp}.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open("x", encoding="utf-8", buffering=1)
        self.write(dict(type="session", schema_version=1, session_id=self.session_id,
                        started_at_utc=datetime.now(timezone.utc).isoformat(), demo="7_1",
                        tx_ip=tx_ip, port=port,
                        hardware={"tx": "ESP32-U", "rx1": "ESP32 Pico", "rx2": "ESP32 Pico"},
                        csi_order="imag,real", capture_mode="sequential_rx1_rx2", label=None,
                        radio_requested=dict(channel=6, bandwidth="HT40"),
                        csi_config_source="receiver_config records and per-capture csi_config"))

    def write(self, record):
        self.file.write(json.dumps(record, separators=(",", ":"), allow_nan=False) + "\n")

    def add(self, response):
        self.receiver_config(response["receiver_id"], response["csi_config"])
        self.write(dict(type="capture", session_id=self.session_id,
                        received_at_utc=datetime.now(timezone.utc).isoformat(),
                        received_monotonic_ns=time.monotonic_ns(), label=None, **response))

    def receiver_config(self, receiver_id, config):
        if self.receiver_settings.get(receiver_id) == config:
            return
        self.write(dict(type="receiver_config", schema_version=1, session_id=self.session_id,
                        received_at_utc=datetime.now(timezone.utc).isoformat(),
                        receiver_id=receiver_id, csi_config=config))
        self.receiver_settings[receiver_id] = dict(config)

    def event(self, message):
        self.write(dict(type="capture_error", session_id=self.session_id,
                        received_at_utc=datetime.now(timezone.utc).isoformat(), message=message))

    def close(self):
        self.file.close()
