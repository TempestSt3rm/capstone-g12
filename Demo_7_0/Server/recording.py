"""Append-only raw sessions; display transforms never touch these records."""

from datetime import datetime, timezone
import json
from pathlib import Path
import threading
import time
import uuid


POSITIONS_M = tuple(step / 2 for step in range(1, 8))
RECORDING_SECONDS = 10


class LabeledRecorder:
    """Thread-safe, ten-second JSONL sets; timestamps gate packets, not UI polling.

    In-progress/interrupted files end in .partial.jsonl. Only a successfully
    finished ten-second set is renamed to .jsonl. Timers never touch radio state.
    """

    def __init__(self, directory=None, session_id=None):
        self.directory = Path(directory) if directory else Path(__file__).resolve().parents[1] / "data" / "labeled"
        self.session_id = session_id or str(uuid.uuid4())
        self.lock = threading.RLock()
        self.file = None
        self.timer = None
        self.closed = False
        self.error = None
        self.path = None
        self.recording_id = None
        self.label_m = None
        self.orientation = ""
        self.count = 0
        self.completed_sets = 0
        self.started_ns = None
        self.message = "Select a distance, then Record for 10 seconds."

    def start(self, label_m, orientation=""):
        if type(label_m) not in (int, float) or label_m not in POSITIONS_M:
            raise ValueError("Choose a distance from 0.5 to 3.5 m in 0.5 m steps.")
        if not isinstance(orientation, str) or len(orientation) > 200:
            raise ValueError("Orientation must be text of at most 200 characters.")
        with self.lock:
            if self.closed or self.error:
                raise ValueError("Recording is unavailable; restart the dashboard after resolving the error.")
            if self.file is not None:
                raise ValueError("A 10-second recording is already running.")
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
            self.recording_id = str(uuid.uuid4())
            self.label_m, self.orientation = float(label_m), orientation.strip()
            self.count = 0
            name = f"{stamp}_{self.label_m:.1f}m_{self.recording_id}"
            self.path = self.directory / f"{name}.partial.jsonl"
            self.final_path = self.directory / f"{name}.jsonl"
            try:
                self.directory.mkdir(parents=True, exist_ok=True)
                self.file = self.path.open("x", encoding="utf-8", buffering=1)
                self.file.write(json.dumps({"distance_m": self.label_m}, separators=(",", ":")) + "\n")
                self.started_at_utc = datetime.now(timezone.utc).isoformat()
                self.started_ns = time.monotonic_ns()
                self.deadline_ns = self.started_ns + RECORDING_SECONDS * 1_000_000_000
                self.message = "Recording"
                self._schedule_finish(RECORDING_SECONDS)
            except OSError as exc:
                self._fail_locked(exc)
                raise

    def _schedule_finish(self, delay):
        self.timer = threading.Timer(delay, self._finish, args=(self.recording_id,))
        self.timer.daemon = True
        self.timer.start()

    def _finish(self, recording_id):
        with self.lock:
            if self.file is None or self.recording_id != recording_id:
                return
            remaining = (self.deadline_ns - time.monotonic_ns()) / 1e9
            if remaining > 0:
                self._schedule_finish(remaining)
                return
            try:
                self._complete_locked()
            except OSError as exc:
                self._fail_locked(exc)

    def _complete_locked(self):
        self.file.close()
        self.file = None
        if self.timer is not None:
            self.timer.cancel()
        self.path.rename(self.final_path)
        self.path = self.final_path
        self.completed_sets += 1
        self.message = "Saved 10-second set" if self.count else "Saved 10-second set with 0 packets; check the radio connection"

    def add(self, response, received_monotonic_ns=None):
        received_ns = time.monotonic_ns() if received_monotonic_ns is None else received_monotonic_ns
        with self.lock:
            if self.error:
                raise OSError(self.error)
            if self.file is None:
                return
            if received_ns < self.started_ns:
                return  # A packet received before Record must not be relabeled.
            if received_ns >= self.deadline_ns:
                try:
                    self._complete_locked()
                except OSError as exc:
                    self._fail_locked(exc)
                    raise
                return
            record = dict(response)
            record.update(type="capture", distance_m=self.label_m, orientation=self.orientation,
                          recording_id=self.recording_id, session_id=self.session_id,
                          recording_duration_s=RECORDING_SECONDS,
                          recording_started_at_utc=self.started_at_utc,
                          received_at_utc=datetime.now(timezone.utc).isoformat(),
                          received_monotonic_ns=received_ns,
                          elapsed_s=(received_ns - self.started_ns) / 1e9)
            try:
                self.file.write(json.dumps(record, separators=(",", ":"), allow_nan=False) + "\n")
                self.count += 1
            except OSError as exc:
                self._fail_locked(exc)
                raise

    def _fail_locked(self, exc):
        self.error = str(exc)
        self.message = f"Recording failed: {exc}. File remains partial."
        if self.timer is not None:
            self.timer.cancel()
        if self.file is not None:
            try:
                self.file.close()
            except OSError:
                pass
            self.file = None

    def fail(self, exc):
        with self.lock:
            self._fail_locked(exc)

    def snapshot(self):
        with self.lock:
            active = self.file is not None
            remaining = max(0, (self.deadline_ns - time.monotonic_ns()) / 1e9) if active else 0
            return dict(active=active, remaining=remaining, count=self.count,
                        label_m=self.label_m, orientation=self.orientation,
                        path=str(self.path) if self.path else None, message=self.message,
                        completed_sets=self.completed_sets, unavailable=self.closed or bool(self.error))

    def close(self):
        with self.lock:
            self.closed = True
            if self.timer is not None:
                self.timer.cancel()
            if self.file is not None:
                try:
                    if time.monotonic_ns() >= self.deadline_ns:
                        self._complete_locked()
                    else:
                        self.file.close()
                        self.file = None
                        self.message = "Interrupted before 10 seconds; file remains partial"
                except OSError as exc:
                    self._fail_locked(exc)


class RawRecorder:
    def __init__(self, path=None, tx_ip="192.168.4.1", port=5000):
        self.session_id = str(uuid.uuid4())
        self.receiver_settings = {}
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
        self.path = Path(path) if path else Path(__file__).resolve().parents[1] / "data" / f"{stamp}.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open("x", encoding="utf-8", buffering=1)
        self.write(dict(type="session", schema_version=1, session_id=self.session_id,
                        started_at_utc=datetime.now(timezone.utc).isoformat(), demo="7_0",
                        tx_ip=tx_ip, port=port,
                        hardware={"tx": "ESP32-U", "rx1": "ESP32-S3-WROOM-1"},
                        csi_order="imag,real", capture_mode="single_rx1", label=None,
                        radio_requested=dict(channel=6, bandwidth="HT40"),
                        csi_config_source="receiver_config records and per-capture csi_config"))

    def write(self, record):
        self.file.write(json.dumps(record, separators=(",", ":"), allow_nan=False) + "\n")

    def add(self, response):
        self.receiver_config(response["rx_id"], response["csi_config"])
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
