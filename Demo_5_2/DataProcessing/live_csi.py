"""Capture live CSI and print (I, Q) pairs until Ctrl+C."""

import argparse
import math
import sys

from ..Server.ping import stream_captures


def iq_pairs(csi):
    """Convert ESP32's [imag, real, ...] bytes to [(I=real, Q=imag), ...]."""
    return [(csi[index + 1], csi[index]) for index in range(0, len(csi), 2)]


def print_capture(response):
    pairs = iq_pairs(response["csi"])
    values = " ".join(f"{index}:({i}, {q})" for index, (i, q) in enumerate(pairs))
    print(f"RX{response['receiver_id']} packet_id={response['packet_id']} pairs={len(pairs)} (I,Q) {values}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tx_ip", help="Transmitter IP printed by its serial monitor")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--count", type=int, default=0, help="Two-receiver cycles; 0 runs continuously (default)")
    parser.add_argument("--timeout", type=float, default=10.0, help="Seconds to wait per cycle (default: 10)")
    args = parser.parse_args()
    if args.count < 0 or not 1 <= args.port <= 65535:
        parser.error("--count must be nonnegative; --port must be 1..65535")
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--timeout must be finite and positive")
    try:
        for response in stream_captures(args.tx_ip, args.port, args.count, args.timeout):
            print_capture(response)
    except (OSError, TimeoutError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
