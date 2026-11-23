"""Verify data preservation through collection, display and partial-cycle failures."""

import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from Demo_7_1.Server.recording import RawRecorder
from Demo_7_1.DataProcessing.dashboard import MultiWindow, collect, make_figure
from test_transport import response, settings


class RecordingTests(unittest.TestCase):
    def test_raw_session_keeps_metadata_and_invalid_bytes_through_display_and_timeout(self):
        state, stop = MultiWindow(pairs=2), threading.Event()
        raw = json.loads(response(123, 1, [-128, 127, 0, 3, 1, 4]))

        def stream(*args, **kwargs):
            yield raw
            raise TimeoutError("Missing RX2 for packet_id=123")

        def finish(*args, **kwargs):
            stop.set()
            return iter(())

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "session.jsonl"
            recorder = RawRecorder(path)
            calls = iter((stream, finish))
            try:
                with patch("Demo_7_1.DataProcessing.dashboard.stream_captures",
                           side_effect=lambda *args, **kwargs: next(calls)()):
                    collect(state, stop, "127.0.0.1", 5000, .1, recorder)
            finally:
                recorder.close()
            figure = make_figure(state.receivers[1], state.receivers[1].snapshot())
            self.assertEqual(len(figure.data[0].x), 2)
            state.reset()
            lines = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual([line["type"] for line in lines], ["session", "receiver_config", "capture", "capture_error"])
            self.assertNotIn("radio", lines[0])
            self.assertEqual(lines[1]["csi_config"], raw["csi_config"])
            capture = lines[2]
            for key, value in raw.items():
                self.assertEqual(capture[key], value)
            self.assertIsNone(capture["label"])
            self.assertIn("received_at_utc", capture)
            self.assertGreater(capture["received_monotonic_ns"], 0)
            self.assertIn("RX2", lines[3]["message"])
            with self.assertRaises(FileExistsError):
                RawRecorder(path)

    def test_disk_failure_stops_collection_visibly(self):
        state, stop = MultiWindow(), threading.Event()
        class BrokenRecorder:
            def add(self, response):
                raise OSError("disk full")
        with patch("Demo_7_1.DataProcessing.dashboard.stream_captures",
                   return_value=iter([json.loads(response(1, 1))])):
            collect(state, stop, "127.0.0.1", 5000, .1, BrokenRecorder())
        self.assertTrue(stop.is_set())
        self.assertIn("disk full", state.receivers[1].snapshot()["status"])
        self.assertEqual(state.receivers[1].snapshot()["total"], 0)

    def test_discovery_is_saved_before_captures_and_reconnect_changes_are_retained(self):
        state, stop = MultiWindow(), threading.Event()
        changed = json.loads(response(2, 1))
        changed["csi_config"] = settings(manu_scale=True, shift=2)

        def stream(*args, on_config, **kwargs):
            on_config(1, settings())
            on_config(2, settings(manu_scale=True, shift=1))
            yield json.loads(response(1, 1))
            yield changed
            stop.set()

        with tempfile.TemporaryDirectory() as folder:
            recorder = RawRecorder(Path(folder) / "session.jsonl")
            try:
                with patch("Demo_7_1.DataProcessing.dashboard.stream_captures", side_effect=stream):
                    collect(state, stop, "127.0.0.1", 5000, .1, recorder)
            finally:
                recorder.close()
            lines = [json.loads(line) for line in recorder.path.read_text().splitlines()]
            self.assertEqual([line["type"] for line in lines],
                             ["session", "receiver_config", "receiver_config", "capture", "receiver_config", "capture"])
            self.assertEqual(lines[1]["csi_config"], settings())
            self.assertEqual(lines[2]["csi_config"], settings(manu_scale=True, shift=1))
            self.assertEqual(lines[4]["csi_config"], changed["csi_config"])
            self.assertEqual(lines[5]["csi_config"], changed["csi_config"])

    def test_config_recording_failure_stops_before_capture(self):
        state, stop = MultiWindow(), threading.Event()
        class BrokenRecorder:
            def receiver_config(self, receiver_id, config):
                raise OSError("disk full")
        def stream(*args, on_config, **kwargs):
            on_config(1, settings())
            yield json.loads(response(1, 1))
        with patch("Demo_7_1.DataProcessing.dashboard.stream_captures", side_effect=stream):
            collect(state, stop, "127.0.0.1", 5000, .1, BrokenRecorder())
        self.assertTrue(stop.is_set())
        self.assertIn("Recording failed: disk full", state.receivers[1].snapshot()["status"])
        self.assertEqual(state.receivers[1].snapshot()["total"], 0)
