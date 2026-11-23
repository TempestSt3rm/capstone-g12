"""UDP integration checks for ordered two-receiver capture and retry safety."""

import json
import socket
import threading
import unittest
from unittest.mock import patch

from Demo_7_1.Server.ping import capture, decode_response, stream_captures, receiver_configs, valid_csi_config


def settings(**overrides):
    config = dict(lltf_en=True, htltf_en=True, stbc_htltf2_en=True, ltf_merge_en=False,
                  channel_filter_en=False, manu_scale=False, shift=0)
    return dict(config, **overrides)


def config_report(request_id, receiver_id, config=None):
    return json.dumps(dict(type="receiver_config", schema_version=1, request_id=request_id,
                           receiver_id=receiver_id, csi_config=settings() if config is None else config)).encode()


def response(packet_id, receiver_id, csi=None):
    csi = [3, 4] if csi is None else csi
    metadata = {key: 0 for key in ("rx_timestamp_us", "rssi", "noise_floor", "rate", "sig_mode",
                "mcs", "cwb", "channel", "secondary_channel", "stbc", "sgi", "aggregation",
                "ampdu_cnt", "fec_coding", "ant", "sig_len", "rx_state")}
    metadata.update(first_word_invalid=True, csi_len=len(csi), source_mac="00:11:22:33:44:55")
    return json.dumps({"schema_version": 1, "packet_id": packet_id, "receiver_id": receiver_id,
                       "metadata": metadata, "csi": csi, "csi_config": settings()}).encode()


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
                    query, laptop = receive(b"GET_CONFIG ")
                    request_id = int(query.decode().split()[1])
                    tx.sendto(config_report(request_id, 2, settings(manu_scale=True, shift=2)), laptop)
                    tx.sendto(config_report(request_id, 1), laptop)
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
            reported = []
            try:
                with patch("Demo_7_1.Server.ping.secrets.randbits", return_value=0xFFFFFFFF):
                    records = list(stream_captures("127.0.0.1", tx.getsockname()[1], count=2, timeout=2,
                                                  on_config=lambda rid, config: reported.append((rid, config))))
            finally:
                worker.join(timeout=4)
            self.assertFalse(worker.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(reported, [(1, settings()), (2, settings(manu_scale=True, shift=2))])
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

    def test_metadata_and_all_raw_bytes_survive_validation(self):
        payload = json.loads(response(1, 1, [-128, 127] * 512))
        payload["metadata"]["rx_timestamp_us"] = 0xFFFFFFFF
        self.assertEqual(decode_response(json.dumps(payload).encode(), 1), payload)
        for field, value in (("csi_len", 4), ("first_word_invalid", 1), ("rssi", "-50")):
            malformed = json.loads(json.dumps(payload))
            malformed["metadata"][field] = value
            self.assertIsNone(decode_response(json.dumps(malformed).encode(), 1))

    def test_config_discovery_retries_and_rejects_invalid_or_unrelated_reports(self):
        errors = []
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as tx, \
                socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as laptop, \
                socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as stranger:
            tx.bind(("127.0.0.1", 0))
            laptop.bind(("127.0.0.1", 0))
            stranger.bind(("127.0.0.1", 0))
            tx.settimeout(2)

            def radio():
                try:
                    query, source = tx.recvfrom(1024)
                    self.assertEqual(query, b"GET_CONFIG 123")
                    tx.sendto(b"[]", source)
                    tx.sendto(config_report(124, 2), source)
                    tx.sendto(config_report(123, 3), source)
                    tx.sendto(config_report(123, True), source)
                    tx.sendto(config_report(123, 2, settings(shift=16)), source)
                    stranger.sendto(config_report(123, 2), source)
                    tx.sendto(config_report(123, 1), source)
                    tx.sendto(config_report(123, 1), source)
                    retry, same_source = tx.recvfrom(1024)
                    self.assertEqual((retry, same_source), (query, source))
                    tx.sendto(config_report(123, 2, settings(manu_scale=True, shift=3)), source)
                except Exception as exc:
                    errors.append(exc)

            worker = threading.Thread(target=radio, daemon=True)
            worker.start()
            try:
                reports = receiver_configs(laptop, tx.getsockname(), 123, 2)
            finally:
                worker.join(timeout=3)
            self.assertEqual(errors, [])
            self.assertEqual(reports, {1: settings(), 2: settings(manu_scale=True, shift=3)})

    def test_missing_settings_prevent_ping_and_identify_receiver(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as tx:
            tx.bind(("127.0.0.1", 0))
            tx.settimeout(2)
            requests, errors = [], []
            def radio():
                try:
                    query, source = tx.recvfrom(1024)
                    requests.append(query)
                    tx.sendto(config_report(123, 1), source)
                except Exception as exc:
                    errors.append(exc)
            worker = threading.Thread(target=radio, daemon=True)
            worker.start()
            try:
                with patch("Demo_7_1.Server.ping.secrets.randbits", return_value=123):
                    with self.assertRaisesRegex(TimeoutError, "Missing CSI settings from RX2"):
                        next(stream_captures("127.0.0.1", tx.getsockname()[1], timeout=.15))
            finally:
                worker.join(timeout=3)
            self.assertEqual(errors, [])
            self.assertEqual(requests, [b"GET_CONFIG 123"])
            tx.settimeout(.1)
            with self.assertRaises(socket.timeout):
                tx.recvfrom(1024)  # No PING was queued after the startup timeout.

    def test_config_validation_uses_strict_boolean_flags_and_shift_range(self):
        for config in (None, {}, settings(lltf_en=1), settings(shift=True),
                       settings(shift=-1), settings(shift=16), settings(extra=True)):
            self.assertFalse(valid_csi_config(config))
            payload = json.loads(response(1, 1))
            payload["csi_config"] = config
            self.assertIsNone(decode_response(json.dumps(payload).encode(), 1))
        self.assertTrue(valid_csi_config(settings(manu_scale=True, shift=15)))


if __name__ == "__main__":
    unittest.main()
