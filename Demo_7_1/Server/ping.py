"""One PING to ESP32-U TX collects RX1 Pico then RX2 Pico under one packet ID."""

import argparse
import json
import math
import secrets
import socket
import sys
import time

RECEIVER_IDS = (1, 2)
CSI_FLAGS = ("lltf_en", "htltf_en", "stbc_htltf2_en", "ltf_merge_en", "channel_filter_en", "manu_scale")


def valid_csi_config(config):
    return (isinstance(config, dict) and set(config) == {*CSI_FLAGS, "shift"}
            and all(type(config[key]) is bool for key in CSI_FLAGS)
            and type(config["shift"]) is int and 0 <= config["shift"] <= 15)


def receiver_configs(sock, transmitter, request_id, timeout):
    """Fetch both registered receivers' settings before starting CSI collection."""
    deadline, next_send = time.monotonic() + timeout, 0.0
    reports = {}
    while len(reports) < len(RECEIVER_IDS):
        now = time.monotonic()
        if now >= deadline:
            missing = ", ".join(f"RX{rid}" for rid in RECEIVER_IDS if rid not in reports)
            raise TimeoutError(f"Missing CSI settings from {missing}; collection not started")
        if now >= next_send:
            sock.sendto(f"GET_CONFIG {request_id}".encode("ascii"), transmitter)
            next_send = now + .25
        sock.settimeout(max(.001, min(next_send, deadline) - time.monotonic()))
        try:
            data, source = sock.recvfrom(65535)
        except socket.timeout:
            continue
        if source != transmitter:
            continue
        try:
            report = json.loads(data)
        except (ValueError, UnicodeDecodeError):
            continue
        if (not isinstance(report, dict) or report.get("type") != "receiver_config"
                or type(report.get("schema_version")) is not int or report["schema_version"] != 1
                or type(report.get("request_id")) is not int or report["request_id"] != request_id
                or type(report.get("receiver_id")) is not int or report["receiver_id"] not in RECEIVER_IDS
                or not valid_csi_config(report.get("csi_config"))):
            continue
        reports[report["receiver_id"]] = report["csi_config"]
    return reports


def decode_response(data, packet_id):
    try:
        response = json.loads(data)
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(response, dict) or set(response) != {"schema_version", "packet_id", "receiver_id", "metadata", "csi", "csi_config"}:
        return None
    if not valid_csi_config(response["csi_config"]):
        return None
    if type(response["schema_version"]) is not int or response["schema_version"] != 1:
        return None
    if type(response["packet_id"]) is not int or response["packet_id"] != packet_id:
        return None
    if type(response["receiver_id"]) is not int or response["receiver_id"] not in RECEIVER_IDS:
        return None
    csi = response["csi"]
    if (not isinstance(csi, list) or not csi or len(csi) > 1024 or len(csi) % 2 or
            not all(type(value) is int and -128 <= value <= 127 for value in csi)):
        return None
    metadata = response["metadata"]
    if not isinstance(metadata, dict):
        return None
    if type(metadata.get("first_word_invalid")) is not bool:
        return None
    if type(metadata.get("csi_len")) is not int or metadata["csi_len"] != len(csi):
        return None
    for key in ("rx_timestamp_us", "rssi", "noise_floor", "rate", "sig_mode", "mcs", "cwb",
                "channel", "secondary_channel", "stbc", "sgi", "aggregation", "ampdu_cnt",
                "fec_coding", "ant", "sig_len", "rx_state"):
        if type(metadata.get(key)) is not int:
            return None
    if not isinstance(metadata.get("source_mac"), str):
        return None
    return response


def capture(sock, transmitter, packet_id, timeout):
    """Yield each receiver once; ACK duplicate responses to recover lost ACKs."""
    request = f"PING {packet_id}".encode("ascii")
    deadline = time.monotonic() + timeout
    next_send = 0.0
    received = set()
    while len(received) < len(RECEIVER_IDS):
        now = time.monotonic()
        if now >= deadline:
            missing = ", ".join(f"RX{rid}" for rid in RECEIVER_IDS if rid not in received)
            raise TimeoutError(f"Missing {missing} for packet_id={packet_id}")
        if now >= next_send:
            sock.sendto(request, transmitter)
            next_send = now + 0.25
        sock.settimeout(max(0.001, min(next_send, deadline) - time.monotonic()))
        try:
            data, _ = sock.recvfrom(65535)
        except socket.timeout:
            continue
        response = decode_response(data, packet_id)
        if response is None:
            continue
        receiver_id = response["receiver_id"]
        if any(rid not in received for rid in RECEIVER_IDS if rid < receiver_id):
            continue
        # TX advances only after the laptop has validated that stage's data.
        sock.sendto(f"RECEIVED {packet_id} {receiver_id}".encode("ascii"), transmitter)
        if receiver_id not in received:
            received.add(receiver_id)
            yield response


def stream_captures(tx_ip, port=5000, count=0, timeout=10.0, on_config=None):
    """Count is complete two-receiver cycles; zero streams continuously."""
    transmitter = (socket.gethostbyname(tx_ip), port)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("0.0.0.0", 0))
        packet_id = secrets.randbits(32)
        configs = receiver_configs(sock, transmitter, packet_id, timeout)
        for receiver_id in RECEIVER_IDS:
            if on_config is not None:
                on_config(receiver_id, configs[receiver_id])
            else:
                print(f"RX{receiver_id} CSI settings: {json.dumps(configs[receiver_id], sort_keys=True)}", file=sys.stderr, flush=True)
        completed = 0
        while count == 0 or completed < count:
            yield from capture(sock, transmitter, packet_id, timeout)
            completed += 1
            packet_id = (packet_id + 1) & 0xFFFFFFFF


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tx_ip")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--count", type=int, default=0, help="Two-receiver cycles; 0 runs continuously")
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args()
    if args.count < 0 or not 1 <= args.port <= 65535:
        parser.error("count must be nonnegative; port must be 1..65535")
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("timeout must be finite and positive")
    try:
        for response in stream_captures(args.tx_ip, args.port, args.count, args.timeout):
            print(json.dumps(response, separators=(",", ":")), flush=True)
    except (OSError, TimeoutError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
