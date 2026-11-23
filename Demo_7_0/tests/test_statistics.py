"""Rolling statistics and the actual Dash HTTP update path."""

import threading
import unittest
from unittest.mock import patch

from Demo_7_0.DataProcessing.dashboard import MultiWindow, LiveWindow, collect, create_app, make_figure


class DashboardTests(unittest.TestCase):
    def test_amplitude_and_expiry_use_captures_not_refreshes(self):
        state = LiveWindow(window=2, pairs=2)
        state.add({"packet_id": 1, "csi_raw": [3, 4, 0, 12]})
        state.add({"packet_id": 2, "csi_raw": [0, 10, 0, 2]})
        self.assertEqual(state.snapshot()["mean"], [7.5, 7])
        self.assertEqual(state.snapshot()["p10"], [5.5, 3])
        self.assertEqual(state.snapshot()["p90"], [9.5, 11])
        state.snapshot()  # Browser polling must not advance the window.
        state.add({"packet_id": 3, "csi_raw": [0, 6, 0, 4]})
        result = state.snapshot()
        self.assertEqual(result["mean"], [8, 3])
        self.assertEqual(result["p10"], [6.4, 2.2])
        self.assertEqual(result["p90"], [9.6, 3.8])
        self.assertEqual(result["size"], 2)
        self.assertEqual(result["total"], 3)

    def test_missing_pairs_are_not_zero_and_expire(self):
        state = LiveWindow(window=2, pairs=2)
        state.add({"packet_id": 1, "csi_raw": [0, 5, 0, 12]})
        state.add({"packet_id": 2, "csi_raw": [0, 7]})
        result = state.snapshot()
        self.assertEqual(result["mean"], [6, 12])
        self.assertEqual(result["p10"], [5.2, 12])
        self.assertEqual(result["p90"], [6.8, 12])
        state.add({"packet_id": 3, "csi_raw": [0, 9]})
        self.assertEqual(state.snapshot()["mean"], [8, None])
        state.reset()
        self.assertEqual(state.snapshot()["size"], 0)
        self.assertEqual(state.snapshot()["p90"], [None, None])
        state.add({"packet_id": 4, "csi_raw": [0, 2]})
        self.assertEqual(state.snapshot()["mean"], [2, None])
        self.assertEqual(state.snapshot()["p10"], [2, None])
        self.assertEqual(state.snapshot()["p90"], [2, None])

    def test_plot_keeps_all_indices_and_latest_capture(self):
        state = LiveWindow(pairs=192)
        state.add({"packet_id": 1, "csi_raw": [0, 2] * 192})
        state.add({"packet_id": 2, "csi_raw": [0, 8] * 192})
        figure = make_figure(state, state.snapshot())
        self.assertEqual(list(figure.data[0].x), list(range(192)))
        self.assertEqual(list(figure.data[1].x), list(range(192)))
        self.assertEqual(list(figure.data[2].y), [8] * 192)
        self.assertEqual(state.window, 200)
        self.assertEqual(list(figure.data[0].y), [5] * 192)
        self.assertEqual(list(figure.data[1].y), [5] * 192)
        for half_range in figure.data[1].error_y.array:
            self.assertAlmostEqual(half_range, 2.4)

    def test_outliers_do_not_expand_percentile_whiskers_to_mean(self):
        state = LiveWindow(pairs=5)
        for index in range(20):
            state.add({"packet_id": index, "csi_raw": [0, 100 if index == 19 else 0] * 5})
        result = state.snapshot()
        self.assertEqual(result["mean"], [5] * 5)
        self.assertEqual(result["p10"], [0] * 5)
        self.assertEqual(result["p90"], [0] * 5)
        figure = make_figure(state, result)
        self.assertEqual(list(figure.data[1].y), [0] * 5)
        self.assertEqual(list(figure.data[1].error_y.array), [0] * 5)

