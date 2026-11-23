import copy
import json
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from unittest.mock import patch

from Demo_7_0.Server.ping import decode_response, stream_captures
from Demo_7_0.Server.recording import RawRecorder
from Demo_7_0.DataProcessing.dashboard import MultiWindow, collect, create_app


def settings():
    return dict(lltf_en=True, htltf_en=True, stbc_htltf2_en=True,
                ltf_merge_en=False, channel_filter_en=False, manu_scale=False, shift=0)


def sample(request_id=123, packet_id=1, **overrides):
    result = dict(schema_version=1, request_id=request_id, packet_id=packet_id,
                  rx_id=1, timestamp_us=0xFFFFFFFF, tx_mac=[0, 17, 34, 51, 68, 85],
                  rssi=-52, noise_floor=-95, agc_gain=42, fft_gain=-8,
                  gain_available=True, channel=6, bandwidth=40, secondary_channel=1,
                  antenna=0, rate=0, sig_mode=1, mcs=0, stbc=0, sgi=0,
                  aggregation=0, first_word_invalid=True, csi_len=4,
                  csi_raw=[3, 4, -128, 127], csi_config=settings())
    result.update(overrides)
    return result


class CaptureTests(unittest.TestCase):
    def test_validation_preserves_raw_bytes_and_signed_gain(self):
        record = sample(csi_raw=[-128, 127] * 512, csi_len=1024)
        self.assertEqual(decode_response(json.dumps(record).encode(), 123), record)
        for field, value in (("fft_gain", -129), ("fft_gain", 128), ("agc_gain", -1),
                             ("agc_gain", 256), ("agc_gain", True), ("fft_gain", None),
                             ("timestamp_us", -1), ("packet_id", 2**32), ("rx_id", 2),
                             ("tx_mac", [0]*5), ("csi_raw", [True, 0]),
                             ("csi_len", 3), ("first_word_invalid", 1),
                             ("bandwidth", 1), ("request_id", 124)):
            with self.subTest(field=field, value=value):
                self.assertIsNone(decode_response(json.dumps(sample(**{field: value})).encode(), 123))
        for payload in (b"[]", b"invalid", b"null"):
            self.assertIsNone(decode_response(payload, 123))

    def test_single_rx_discovery_retry_stale_reply_and_request_rollover(self):
        errors, requests = [], []
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as tx, \
                socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as rx:
            for sock in (tx, rx):
                sock.bind(("127.0.0.1", 0))
                sock.settimeout(3)

            def radio():
                try:
                    data, laptop = tx.recvfrom(2048)
                    self.assertTrue(data.startswith(b"GET_CONFIG "))
                    query = int(data.split()[1])
                    report = dict(type="receiver_config", schema_version=1,
                                  request_id=query, receiver_id=1, csi_config=settings())
                    tx.sendto(json.dumps(report).encode(), laptop)
                    for index, request_id in enumerate((0xFFFFFFFF, 0)):
                        data, laptop = tx.recvfrom(2048)
                        requests.append(data)
                        self.assertEqual(data, f"PING {request_id}".encode())
                        # A late cached reply from another request cannot advance capture.
                        rx.sendto(json.dumps(sample(request_id ^ 1)).encode(), laptop)
                        if index == 0:
                            retry, same_laptop = tx.recvfrom(2048)
                            self.assertEqual((retry, same_laptop), (data, laptop))
                        record = sample(request_id, index + 1)
                        rx.sendto(json.dumps(record).encode(), laptop)
                        ack, _ = tx.recvfrom(2048)
                        self.assertEqual(ack, f"RECEIVED {request_id} 1".encode())
                        # Cached duplicate must not become a second recorded capture.
                        rx.sendto(json.dumps(record).encode(), laptop)
                except BaseException as exc:
                    errors.append(exc)

            worker = threading.Thread(target=radio, daemon=True)
            worker.start()
            with patch("Demo_7_0.Server.ping.secrets.randbits", return_value=0xFFFFFFFF):
                records = list(stream_captures("127.0.0.1", tx.getsockname()[1],
                                               count=2, timeout=2, on_config=lambda *_: None))
            worker.join(timeout=4)
            self.assertFalse(worker.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual([r["packet_id"] for r in records], [1, 2])
            self.assertEqual([r["request_id"] for r in records], [0xFFFFFFFF, 0])
            self.assertEqual(len(requests), 2)  # Never waits for an RX2.

    def test_dashboard_http_gain_metadata_and_one_graph(self):
        state = MultiWindow(window=2, pairs=2)
        app = create_app(state)
        client = app.server.test_client()
        self.assertEqual(client.get("/").status_code, 200)
        layout = client.get("/_dash-layout").get_data(as_text=True)
        self.assertIn("amplitudes-1", layout)
        self.assertNotIn("amplitudes-2", layout)
        state.add(sample())
        response = client.post("/_dash-update-component", json={
            "output": "..amplitudes-1.figure...capture-status-1.children..",
            "outputs": [{"id": "amplitudes-1", "property": "figure"},
                        {"id": "capture-status-1", "property": "children"}],
            "inputs": [{"id": "refresh", "property": "n_intervals", "value": 1}],
            "state": [], "changedPropIds": ["refresh.n_intervals"],
        })
        self.assertEqual(response.status_code, 200)
        result = response.get_json()["response"]
        self.assertEqual(result["amplitudes-1"]["figure"]["data"][0]["y"][0], 5)
        status = json.dumps(result["capture-status-1"]["children"])
        self.assertIn("AGC gain: 42", status)
        self.assertIn("FFT gain: -8", status)
        self.assertIn("Noise floor: -95", status)

    def test_recording_preserves_every_field_despite_display_limit_and_reset(self):
        raw = sample()
        before = copy.deepcopy(raw)
        state = MultiWindow(pairs=1)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "raw.jsonl"
            recorder = RawRecorder(path)
            recorder.add(raw)
            state.add(raw)
            state.reset()
            recorder.close()
            rows = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual(rows[0]["hardware"]["rx1"], "ESP32-S3-WROOM-1")
            for key, value in before.items():
                self.assertEqual(rows[-1][key], value)
            self.assertEqual(raw, before)
            self.assertEqual(state.receivers[1].snapshot()["size"], 0)
            with self.assertRaises(FileExistsError):
                RawRecorder(path)

    def test_recording_failure_stops_collection(self):
        state, stop = MultiWindow(), threading.Event()

        class BrokenRecorder:
            def add(self, _):
                raise OSError("disk full")

        with patch("Demo_7_0.DataProcessing.dashboard.stream_captures",
                   return_value=iter([sample()])):
            collect(state, stop, "127.0.0.1", 5000, .1, BrokenRecorder())
        self.assertTrue(stop.is_set())
        self.assertIn("disk full", state.receivers[1].snapshot()["status"])
        self.assertEqual(state.receivers[1].snapshot()["total"], 0)


if __name__ == "__main__":
    unittest.main()
