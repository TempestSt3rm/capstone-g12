"""Laptop -> TX -> RX -> laptop: one numbered capture at a time."""

import argparse
import json
import math
from pathlib import Path
import secrets
import socket
import sys
import time


def capture(sock, transmitter, packet_id, timeout):
    request = f"PING {packet_id}".encode("ascii")
    deadline = time.monotonic() + timeout
    next_send = 0.0
    while True:
        now = time.monotonic()
        if now >= deadline:
            raise TimeoutError(f"No receiver CSI for packet_id={packet_id}; stopping collection")
        if now >= next_send:
            sock.sendto(request, transmitter)
            next_send = now + 0.25
        sock.settimeout(max(0.001, min(next_send, deadline) - time.monotonic()))
        try:
            data, _ = sock.recvfrom(65535)
        except socket.timeout:
            continue
        try:
            response = json.loads(data)
        except (ValueError, UnicodeDecodeError):
            continue
        if not isinstance(response, dict) or set(response) != {"packet_id", "csi"}:
            continue
        if type(response["packet_id"]) is not int or response["packet_id"] != packet_id:
            continue
        csi = response["csi"]
        if (not isinstance(csi, list) or not csi or len(csi) > 1024 or len(csi) % 2 or
                not all(type(value) is int and -128 <= value <= 127 for value in csi)):
            continue
        return response


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tx_ip", help="Transmitter IP printed by its serial monitor")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--output", type=Path, help="Also save JSON lines to this file (overwrites it)")
    args = parser.parse_args()
    if args.count < 1 or not 1 <= args.port <= 65535:
        parser.error("--count must be positive; --port must be 1..65535")
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--timeout must be finite and positive")
    output = None
    try:
        transmitter = (socket.gethostbyname(args.tx_ip), args.port)
        if args.output:
            output = args.output.open("w", encoding="utf-8")
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            # Leave this UDP socket unconnected: requests go to TX, replies
            # arrive from RX, a different address. Keep the same port for retries.
            sock.bind(("0.0.0.0", 0))
            start_id = secrets.randbits(32)
            for offset in range(args.count):
                response = capture(sock, transmitter, (start_id + offset) & 0xFFFFFFFF, args.timeout)
                line = json.dumps(response, separators=(",", ":"))
                print(line, flush=True)
                if output:
                    output.write(line + "\n")
                    output.flush()
    except (OSError, TimeoutError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    finally:
        if output:
            output.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
