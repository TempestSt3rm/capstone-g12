"""One PING to ESP32-U TX collects RX1 Pico then RX2 WROOM under one packet ID."""

import argparse
import json
import math
import secrets
import socket
import sys
import time

RECEIVER_IDS = (1, 2)


def decode_response(data, packet_id):
    try:
        response = json.loads(data)
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(response, dict) or set(response) != {"packet_id", "receiver_id", "csi"}:
        return None
    if type(response["packet_id"]) is not int or response["packet_id"] != packet_id:
        return None
    if type(response["receiver_id"]) is not int or response["receiver_id"] not in RECEIVER_IDS:
        return None
    csi = response["csi"]
    if (not isinstance(csi, list) or not csi or len(csi) > 1024 or len(csi) % 2 or
            not all(type(value) is int and -128 <= value <= 127 for value in csi)):
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


def stream_captures(tx_ip, port=5000, count=0, timeout=10.0):
    """Count is complete two-receiver cycles; zero streams continuously."""
    transmitter = (socket.gethostbyname(tx_ip), port)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("0.0.0.0", 0))
        packet_id = secrets.randbits(32)
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
