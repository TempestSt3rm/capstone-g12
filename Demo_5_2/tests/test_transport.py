"""UDP integration checks for ordered two-receiver capture and retry safety."""

import json
import socket
import threading
import unittest
from unittest.mock import patch

from Demo_5_2.Server.ping import capture, decode_response, stream_captures


def response(packet_id, receiver_id, csi=None):
    return json.dumps({"packet_id": packet_id, "receiver_id": receiver_id,
                       "csi": [3, 4] if csi is None else csi}).encode()


class TransportTests(unittest.TestCase):
    def test_sequence_retries_duplicates_and_packet_id_wrap(self):
        requests, errors = [], []
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as tx, \
                socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as rx1, \
                socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as rx2:
            for sock in (tx, rx1, rx2):
                sock.bind(("127.0.0.1", 0))
                sock.settimeout(3)

            def receive(prefix):
                data, laptop = tx.recvfrom(1024)
                requests.append((data, laptop))
                self.assertTrue(data.startswith(prefix), data)
                return data, laptop

            def radio():
                try:
                    for cycle in range(2):
                        data, laptop = receive(b"PING ")
                        packet_id = int(data.decode().split()[1])
                        # Malformed and premature RX2 data cannot advance the cycle.
                        rx1.sendto(b"invalid", laptop)
                        rx2.sendto(response(packet_id, 2), laptop)
                        rx1.sendto(response(packet_id, 1, [1]), laptop)
                        if cycle == 0:
                            retry, same_laptop = receive(b"PING ")
                            self.assertEqual((retry, same_laptop), (data, laptop))
                        rx1.sendto(response(packet_id, 1), laptop)
                        ack, _ = receive(b"RECEIVED ")
                        self.assertEqual(ack, f"RECEIVED {packet_id} 1".encode())
                        # Simulate a lost ACK by resending RX1's cached response.
                        rx1.sendto(response(packet_id, 1), laptop)
                        duplicate_ack, _ = receive(b"RECEIVED ")
                        self.assertEqual(duplicate_ack, ack)
                        rx2.sendto(response(packet_id, 2, [0, 12]), laptop)
                        ack, _ = receive(b"RECEIVED ")
                        self.assertEqual(ack, f"RECEIVED {packet_id} 2".encode())
                except Exception as exc:
                    errors.append(exc)

            worker = threading.Thread(target=radio, daemon=True)
            worker.start()
            try:
                with patch("Demo_5_2.Server.ping.secrets.randbits", return_value=0xFFFFFFFF):
                    records = list(stream_captures("127.0.0.1", tx.getsockname()[1], count=2, timeout=2))
            finally:
                worker.join(timeout=4)
            self.assertFalse(worker.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual([(r["packet_id"], r["receiver_id"]) for r in records],
                             [(0xFFFFFFFF, 1), (0xFFFFFFFF, 2), (0, 1), (0, 2)])
            self.assertEqual(len({source for _, source in requests}), 1)

    def test_partial_capture_yields_rx1_then_names_missing_rx2(self):
        errors = []
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as tx, \
                socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as laptop:
            tx.bind(("127.0.0.1", 0))
            laptop.bind(("127.0.0.1", 0))
            tx.settimeout(2)

            def radio():
                try:
                    _, source = tx.recvfrom(1024)
                    tx.sendto(response(123, 1), source)
                except Exception as exc:
                    errors.append(exc)

            worker = threading.Thread(target=radio, daemon=True)
            worker.start()
            stream = capture(laptop, tx.getsockname(), 123, 0.1)
            self.assertEqual(next(stream)["receiver_id"], 1)
            with self.assertRaisesRegex(TimeoutError, "Missing RX2 for packet_id=123"):
                next(stream)
            worker.join(timeout=2)
            self.assertEqual(errors, [])

    def test_response_validation(self):
        for payload in (b"[]", response(99, 1), response(1, True), response(1, 3),
                        response(1, 1, []), response(1, 1, [128, 0]), response(1, 1, [True, 0])):
            self.assertIsNone(decode_response(payload, 1))
        self.assertEqual(decode_response(response(1, 2), 1)["receiver_id"], 2)


if __name__ == "__main__":
    unittest.main()
