import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from Demo_7_0.DataProcessing.dashboard import MultiWindow, collect, create_app
from Demo_7_0.Server.recording import LabeledRecorder, POSITIONS_M
from test_capture import sample


def read_set(path):
    with Path(path).open(encoding="utf-8") as stream:
        distance_line = json.loads(next(stream))
        return distance_line, [json.loads(line) for line in stream]



class LabeledRecordingTests(unittest.TestCase):
    def test_ten_second_boundaries_labels_and_full_raw_roundtrip(self):
        with tempfile.TemporaryDirectory() as folder, \
                patch("Demo_7_0.Server.recording.threading.Timer"), \
                patch("Demo_7_0.Server.recording.time.monotonic_ns", return_value=1_000_000_000) as clock:
            recorder = LabeledRecorder(folder, "session-test")
            raw = sample(extra_future_field=[1, -2, 3])
            recorder.add(raw)  # Preview never creates CSVs.
            self.assertEqual(list(Path(folder).iterdir()), [])
            recorder.start(1.5, 'sideways, "90 degrees"')
            recorder.add(raw, received_monotonic_ns=999_999_999)
            recorder.add(raw, received_monotonic_ns=1_000_000_000)
            recorder.add(raw, received_monotonic_ns=10_999_999_999)
            clock.return_value = 11_000_000_000
            recorder.add(raw, received_monotonic_ns=11_000_000_000)
            recorder.add(raw)
            snapshot = recorder.snapshot()
            self.assertFalse(snapshot["active"])
            self.assertEqual(snapshot["count"], 2)
            self.assertEqual(snapshot["completed_sets"], 1)
            self.assertFalse(snapshot["path"].endswith(".partial.jsonl"))
            first_line, rows = read_set(snapshot["path"])
            self.assertEqual(first_line, {"distance_m": 1.5})
            self.assertEqual(len(rows), 2)
            for row in rows:
                self.assertEqual(row["distance_m"], 1.5)
                self.assertEqual(row["orientation"], 'sideways, "90 degrees"')
                self.assertEqual(row["session_id"], "session-test")
                self.assertEqual(row["recording_duration_s"], 10)
                self.assertEqual(row["csi_raw"], raw["csi_raw"])
                self.assertEqual(row["csi_config"], raw["csi_config"])
                self.assertEqual(row["tx_mac"], raw["tx_mac"])
                self.assertEqual(row["first_word_invalid"], True)
                self.assertEqual(int(row["fft_gain"]), -8)
                self.assertEqual(row["extra_future_field"], [1, -2, 3])
                # Every known scalar field survives JSON encoding as well.
                for key, value in raw.items():
                    if isinstance(value, (int, float)) and not isinstance(value, bool):
                        self.assertEqual(int(row[key]), value)
            recorder.close()

    def test_multiple_distances_and_orientations_produce_separate_sets(self):
        with tempfile.TemporaryDirectory() as folder, \
                patch("Demo_7_0.Server.recording.threading.Timer"), \
                patch("Demo_7_0.Server.recording.time.monotonic_ns", return_value=1_000_000_000) as clock:
            recorder = LabeledRecorder(folder)
            paths = []
            for index, distance in enumerate(POSITIONS_M):
                start = (index * 20 + 1) * 1_000_000_000
                clock.return_value = start
                recorder.start(distance, f"orientation {index}")
                recorder.add(sample())
                clock.return_value = start + 10_000_000_000
                recorder._finish(recorder.recording_id)
                paths.append(recorder.snapshot()["path"])
            self.assertEqual(len(set(paths)), 7)
            self.assertEqual(recorder.completed_sets, 7)
            for index, path in enumerate(paths):
                label, rows = read_set(path)
                self.assertEqual(label, {"distance_m": POSITIONS_M[index]})
                self.assertEqual(rows[0]["orientation"], f"orientation {index}")
            # Repeat the same distance as a new trial, never append to the old one.
            recorder.start(0.5, "another orientation")
            self.assertNotIn(recorder.snapshot()["path"], paths)
            recorder.close()
            self.assertTrue(recorder.snapshot()["path"].endswith(".partial.jsonl"))

    def test_rejects_invalid_positions_and_overlapping_starts(self):
        with tempfile.TemporaryDirectory() as folder, \
                patch("Demo_7_0.Server.recording.threading.Timer"):
            recorder = LabeledRecorder(folder)
            for distance in (None, True, 0, 4, 0.75, "1.0", float("nan")):
                with self.assertRaises(ValueError):
                    recorder.start(distance)
            recorder.start(2)
            with self.assertRaises(ValueError):
                recorder.start(3)
            self.assertEqual(recorder.snapshot()["label_m"], 2)
            recorder.close()

    def test_timer_finishes_with_no_packets_or_browser_refresh(self):
        with tempfile.TemporaryDirectory() as folder:
            recorder = LabeledRecorder(folder)
            try:
                recorder.start(3.5)
                recorder.timer.join(timeout=12)
                self.assertFalse(recorder.snapshot()["active"])
                self.assertEqual(recorder.completed_sets, 1)
                self.assertIn("0 packets", recorder.snapshot()["message"])
                label, rows = read_set(recorder.snapshot()["path"])
                self.assertEqual(label, {"distance_m": 3.5})
                self.assertEqual(rows, [])
            finally:
                recorder.close()

    def test_old_timer_cannot_finish_new_recording(self):
        with tempfile.TemporaryDirectory() as folder, \
                patch("Demo_7_0.Server.recording.threading.Timer"), \
                patch("Demo_7_0.Server.recording.time.monotonic_ns", return_value=1_000_000_000) as clock:
            recorder = LabeledRecorder(folder)
            recorder.start(0.5)
            previous_id = recorder.recording_id
            clock.return_value += 10_000_000_000
            recorder._finish(previous_id)
            recorder.start(1)
            recorder._finish(previous_id)
            self.assertTrue(recorder.snapshot()["active"])
            recorder.close()

    def test_disk_failure_leaves_partial_set_and_stops_collector(self):
        with tempfile.TemporaryDirectory() as folder, \
                patch("Demo_7_0.Server.recording.threading.Timer"):
            recorder = LabeledRecorder(folder)
            recorder.start(1)
            state, stop = MultiWindow(), threading.Event()
            with patch.object(recorder.file, "write", side_effect=OSError("disk full")), \
                    patch("Demo_7_0.DataProcessing.dashboard.stream_captures", return_value=iter([sample()])):
                collect(state, stop, "127.0.0.1", 5000, .1, labeled_recorder=recorder)
            self.assertTrue(stop.is_set())
            self.assertIn("disk full", recorder.snapshot()["message"])
            self.assertTrue(recorder.snapshot()["path"].endswith(".partial.jsonl"))
            self.assertTrue(Path(recorder.snapshot()["path"]).exists())
            self.assertTrue(all(path.name.endswith(".partial.jsonl") for path in Path(folder).glob("*.jsonl")))
            self.assertEqual(recorder.completed_sets, 0)
            self.assertTrue(recorder.snapshot()["unavailable"])
            recorder.close()

    def test_dash_record_and_status_callbacks(self):
        with tempfile.TemporaryDirectory() as folder, \
                patch("Demo_7_0.Server.recording.threading.Timer"), \
                patch("Demo_7_0.Server.recording.time.monotonic_ns", return_value=1_000_000_000) as clock:
            recorder = LabeledRecorder(folder)
            state = MultiWindow()
            app = create_app(state, labeled_recorder=recorder)
            client = app.server.test_client()
            payload = {
                "output": "record-action-result.children",
                "outputs": {"id": "record-action-result", "property": "children"},
                "inputs": [{"id": "record-set", "property": "n_clicks", "value": 1}],
                "state": [{"id": "position-label", "property": "value", "value": 2.5},
                          {"id": "orientation-label", "property": "value", "value": "back to TX"}],
                "changedPropIds": ["record-set.n_clicks"],
            }
            response = client.post("/_dash-update-component", json=payload)
            self.assertEqual(response.status_code, 200)
            self.assertTrue(recorder.snapshot()["active"])
            recorder.add(sample())
            state.add(sample())
            state.reset()  # Graph reset cannot stop or erase the recording.
            status_payload = {
                "output": "..recording-status.children...record-set.disabled...position-label.disabled...orientation-label.disabled..",
                "outputs": [{"id": "recording-status", "property": "children"},
                            {"id": "record-set", "property": "disabled"},
                            {"id": "position-label", "property": "disabled"},
                            {"id": "orientation-label", "property": "disabled"}],
                "inputs": [{"id": "refresh", "property": "n_intervals", "value": 1},
                           {"id": "record-action-result", "property": "children", "value": ""}],
                "state": [], "changedPropIds": ["refresh.n_intervals"],
            }
            response = client.post("/_dash-update-component", json=status_payload)
            self.assertEqual(response.status_code, 200)
            data = response.get_json()["response"]
            self.assertTrue(data["record-set"]["disabled"])
            self.assertTrue(data["position-label"]["disabled"])
            self.assertIn("2.5 m", json.dumps(data["recording-status"]))
            clock.return_value = 11_000_000_000
            recorder._finish(recorder.recording_id)
            data = client.post("/_dash-update-component", json=status_payload).get_json()["response"]
            self.assertFalse(data["record-set"]["disabled"])
            self.assertIn("Saved 10-second set", json.dumps(data["recording-status"]))
            self.assertEqual(recorder.snapshot()["count"], 1)
            recorder.close()


if __name__ == "__main__":
    unittest.main()
