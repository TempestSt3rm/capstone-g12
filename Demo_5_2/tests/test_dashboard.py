"""Rolling statistics and the actual Dash HTTP update path."""

import threading
import unittest
from unittest.mock import patch

from Demo_5_2.DataProcessing.dashboard import MultiWindow, LiveWindow, collect, create_app, make_figure


class DashboardTests(unittest.TestCase):
    def test_amplitude_and_expiry_use_captures_not_refreshes(self):
        state = LiveWindow(window=2, pairs=2)
        state.add({"packet_id": 1, "csi": [3, 4, 0, 12]})
        state.add({"packet_id": 2, "csi": [0, 10, 0, 2]})
        self.assertEqual(state.snapshot()["mean"], [7.5, 7])
        self.assertEqual(state.snapshot()["p10"], [5.5, 3])
        self.assertEqual(state.snapshot()["p90"], [9.5, 11])
        state.snapshot()  # Browser polling must not advance the window.
        state.add({"packet_id": 3, "csi": [0, 6, 0, 4]})
        result = state.snapshot()
        self.assertEqual(result["mean"], [8, 3])
        self.assertEqual(result["p10"], [6.4, 2.2])
        self.assertEqual(result["p90"], [9.6, 3.8])
        self.assertEqual(result["size"], 2)
        self.assertEqual(result["total"], 3)

    def test_missing_pairs_are_not_zero_and_expire(self):
        state = LiveWindow(window=2, pairs=2)
        state.add({"packet_id": 1, "csi": [0, 5, 0, 12]})
        state.add({"packet_id": 2, "csi": [0, 7]})
        result = state.snapshot()
        self.assertEqual(result["mean"], [6, 12])
        self.assertEqual(result["p10"], [5.2, 12])
        self.assertEqual(result["p90"], [6.8, 12])
        state.add({"packet_id": 3, "csi": [0, 9]})
        self.assertEqual(state.snapshot()["mean"], [8, None])
        state.reset()
        self.assertEqual(state.snapshot()["size"], 0)
        self.assertEqual(state.snapshot()["p90"], [None, None])
        state.add({"packet_id": 4, "csi": [0, 2]})
        self.assertEqual(state.snapshot()["mean"], [2, None])
        self.assertEqual(state.snapshot()["p10"], [2, None])
        self.assertEqual(state.snapshot()["p90"], [2, None])

    def test_plot_excludes_indices_zero_through_three(self):
        state = LiveWindow()
        state.add({"packet_id": 1, "csi": [0, 2] * 192})
        state.add({"packet_id": 2, "csi": [0, 8] * 192})
        figure = make_figure(state, state.snapshot())
        self.assertEqual(list(figure.data[0].x), list(range(4, 192)))
        self.assertEqual(list(figure.data[1].x), list(range(4, 192)))
        self.assertEqual(state.window, 200)
        self.assertEqual(list(figure.data[0].y), [5] * 188)
        self.assertEqual(list(figure.data[1].y), [5] * 188)
        for half_range in figure.data[1].error_y.array:
            self.assertAlmostEqual(half_range, 2.4)

    def test_outliers_do_not_expand_percentile_whiskers_to_mean(self):
        state = LiveWindow(pairs=5)
        for index in range(20):
            state.add({"packet_id": index, "csi": [0, 100 if index == 19 else 0] * 5})
        result = state.snapshot()
        self.assertEqual(result["mean"], [5] * 5)
        self.assertEqual(result["p10"], [0] * 5)
        self.assertEqual(result["p90"], [0] * 5)
        figure = make_figure(state, result)
        self.assertEqual(list(figure.data[1].y), [0])
        self.assertEqual(list(figure.data[1].error_y.array), [0])

    def test_dashboard_http_updates_without_starting_collector(self):
        state = MultiWindow()
        app = create_app(state)
        client = app.server.test_client()
        self.assertEqual(client.get("/").status_code, 200)
        self.assertEqual(client.get("/_dash-layout").status_code, 200)
        state.add({"packet_id": 42, "receiver_id": 1, "csi": [3, 4] * 5})
        state.add({"packet_id": 42, "receiver_id": 2, "csi": [0, 12] * 5})
        response = client.post("/_dash-update-component", json={
            "output": "..amplitudes-1.figure...capture-status-1.children...amplitudes-2.figure...capture-status-2.children..",
            "outputs": [{"id": "amplitudes-1", "property": "figure"},
                        {"id": "capture-status-1", "property": "children"},
                        {"id": "amplitudes-2", "property": "figure"},
                        {"id": "capture-status-2", "property": "children"}],
            "inputs": [{"id": "refresh", "property": "n_intervals", "value": 1}],
            "state": [], "changedPropIds": ["refresh.n_intervals"],
        })
        self.assertEqual(response.status_code, 200)
        result = response.get_json()["response"]
        self.assertEqual(result["amplitudes-1"]["figure"]["data"][0]["y"][0], 5)
        self.assertEqual(result["amplitudes-2"]["figure"]["data"][0]["y"][0], 12)
        self.assertIn("Packet: 42", result["capture-status-1"]["children"])
        self.assertIn("Packet: 42", result["capture-status-2"]["children"])
        self.assertEqual(state.receivers[1].snapshot()["total"], 1)
        state.reset()
        self.assertEqual(state.receivers[1].snapshot()["size"], 0)
        self.assertEqual(state.receivers[2].snapshot()["size"], 0)

    def test_collector_recovers_after_timeout(self):
        state, stop = MultiWindow(), threading.Event()

        def recovered_stream(*args, **kwargs):
            yield {"packet_id": 7, "receiver_id": 1, "csi": [3, 4]}
            stop.set()

        with patch("Demo_5_2.DataProcessing.dashboard.stream_captures",
                   side_effect=[TimeoutError("no radio"), recovered_stream()]):
            collect(state, stop, "127.0.0.1", 5000, 0.1)
        self.assertEqual(state.receivers[1].snapshot()["total"], 1)
        self.assertEqual(state.receivers[1].snapshot()["status"], "Receiving")
        self.assertEqual(state.receivers[2].snapshot()["total"], 0)

    def test_receiver_windows_stay_independent(self):
        state = MultiWindow(window=2, pairs=1)
        state.add({"packet_id": 1, "receiver_id": 1, "csi": [0, 10]})
        state.add({"packet_id": 1, "receiver_id": 2, "csi": [0, 100]})
        state.add({"packet_id": 2, "receiver_id": 1, "csi": [0, 20]})
        self.assertEqual(state.receivers[1].snapshot()["mean"], [15])
        self.assertEqual(state.receivers[2].snapshot()["mean"], [100])
        self.assertEqual(state.receivers[2].snapshot()["packet_id"], 1)


if __name__ == "__main__":
    unittest.main()
