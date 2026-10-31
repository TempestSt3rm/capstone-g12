"""Send one numbered UDP probe at a time; print only packet_id and CSI JSON."""

import argparse
import json
import math
import secrets
import socket
import sys
import time


def ping(sock, packet_id, timeout=5.0):
    request = f"PING {packet_id}".encode("ascii")
    deadline = time.monotonic() + timeout
    next_send = 0.0
    while True:
        now = time.monotonic()
        if now >= deadline:
            raise TimeoutError(f"No CSI response for packet_id={packet_id}")
        if now >= next_send:
            sock.send(request)
            next_send = now + 0.1
        sock.settimeout(max(0.001, min(next_send, deadline) - time.monotonic()))
        try:
            data = sock.recv(65535)
        except socket.timeout:
            continue
        try:
            response = json.loads(data)
        except (ValueError, UnicodeDecodeError):
            continue
        if not isinstance(response, dict) or set(response) != {"packet_id", "csi"}:
            continue
        if type(response["packet_id"]) is not int or response["packet_id"] != packet_id:
            continue  # Ignore delayed responses to earlier requests.
        csi = response["csi"]
        if (not isinstance(csi, list) or not csi or len(csi) % 2 or
                not all(type(value) is int and -128 <= value <= 127 for value in csi)):
            continue
        return response


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("esp_ip", help="Current DHCP address printed by the ESP32")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--count", type=int, default=1, help="Number of captures (default: 1)")
    parser.add_argument("--timeout", type=float, default=5.0)
    args = parser.parse_args()
    if args.count < 1 or not 1 <= args.port <= 65535:
        parser.error("--count must be positive and --port must be between 1 and 65535")
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--timeout must be finite and positive")
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect((args.esp_ip, args.port))
            start_id = secrets.randbits(32)
            for offset in range(args.count):
                response = ping(sock, (start_id + offset) & 0xFFFFFFFF, args.timeout)
                print(json.dumps(response, separators=(",", ":")), flush=True)
    except (OSError, TimeoutError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
