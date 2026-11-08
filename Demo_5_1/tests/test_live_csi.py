"""Host checks for live UDP capture and ESP32 I/Q byte ordering."""

import contextlib
import io
import json
import socket
import threading
import unittest
from unittest.mock import patch

from Demo_5_1.DataProcessing.live_csi import print_capture
from Demo_5_1.Server.ping import capture, stream_captures


class LiveCsiTests(unittest.TestCase):
    def test_live_stream_retries_filters_and_wraps_ids(self):
        requests = []
        errors = []
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as tx, \
                socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as rx:
            tx.bind(("127.0.0.1", 0))
            rx.bind(("127.0.0.1", 0))
            tx.settimeout(2)

            def radio():
                try:
                    # First request receives junk only, forcing the same-ID retry.
                    data, laptop = tx.recvfrom(1024)
                    requests.append((data, laptop))
                    packet_id = int(data.decode().split()[1])
                    invalid = [b"not JSON", b"[]"]
                    for record in (
                        {"packet_id": 7, "csi": [1, 2]},
                        {"packet_id": packet_id, "csi": [1]},
                        {"packet_id": packet_id, "csi": [128, 2]},
                        {"packet_id": packet_id, "csi": [True, 2]},
                    ):
                        invalid.append(json.dumps(record).encode())
                    for payload in invalid:
                        rx.sendto(payload, laptop)
                    for _ in range(2):
                        data, laptop = tx.recvfrom(1024)
                        requests.append((data, laptop))
                        packet_id = int(data.decode().split()[1])
                        rx.sendto(json.dumps({"packet_id": packet_id,
                                              "csi": [-3, 12, 4, 7]}).encode(), laptop)
                except Exception as exc:
                    errors.append(exc)

            worker = threading.Thread(target=radio, daemon=True)
            worker.start()
            try:
                with patch("Demo_5_1.Server.ping.secrets.randbits", return_value=0xFFFFFFFF):
                    responses = list(stream_captures("127.0.0.1", tx.getsockname()[1], 2, 1))
            finally:
                worker.join(timeout=3)
            self.assertFalse(worker.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual([r["packet_id"] for r in responses], [0xFFFFFFFF, 0])
            self.assertEqual([r[0] for r in requests],
                             [b"PING 4294967295", b"PING 4294967295", b"PING 0"])
            self.assertEqual(len({r[1] for r in requests}), 1)

    def test_prints_every_pair_in_iq_order(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            print_capture({"packet_id": 123, "csi": [-3, 12, 4, 7, 0, -2]})
        self.assertEqual(output.getvalue(),
                         "packet_id=123 pairs=3 (I,Q) 0:(12, -3) 1:(7, 4) 2:(-2, 0)\n")

    def test_missing_receiver_times_out(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as tx, \
                socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as laptop:
            tx.bind(("127.0.0.1", 0))
            laptop.bind(("127.0.0.1", 0))
            with self.assertRaisesRegex(TimeoutError, "packet_id=123"):
                capture(laptop, tx.getsockname(), 123, 0.03)


if __name__ == "__main__":
    unittest.main()
