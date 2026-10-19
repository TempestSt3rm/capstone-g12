"""Diagnostic TCP listener: one PING, then wait for complete CSI/ERROR."""
import argparse
import json
from pathlib import Path
import select
import socket
import time

# Edit these settings, then run this file without command-line arguments.
# Relative output filenames are saved beside this script, regardless of where
# you launch Python. Each response is appended as one JSON object per line.
OUTPUT_FILENAME = "mystery.json"
SAMPLE_COUNT = 1000
LISTEN_HOST = "0.0.0.0"
LISTEN_PORT = 5000


class JsonLines:
    def __init__(self, connection):
        self.connection = connection
        self.buffer = bytearray()

    def receive(self, timeout):
        deadline = time.monotonic() + timeout
        while b"\n" not in self.buffer:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([self.connection], [], [], remaining)[0]:
                return None
            chunk = self.connection.recv(4096)
            if not chunk:
                raise ConnectionError("ESP32 disconnected before a complete response")
            self.buffer.extend(chunk)
            if len(self.buffer) > 65536:
                raise ValueError("Response exceeds diagnostic buffer limit")
        line, _, rest = self.buffer.partition(b"\n")
        self.buffer = bytearray(rest)
        message = json.loads(line)
        if not isinstance(message, dict):
            raise ValueError("Expected a JSON object")
        return message


def capture(connection, reader, request_id, timeout=5):
    connection.sendall(f"PING {request_id}\n".encode("ascii"))
    deadline = time.monotonic() + timeout
    armed = False
    next_probe = 0
    while time.monotonic() < deadline:
        now = time.monotonic()
        if armed and now >= next_probe:
            connection.sendall(f"PROBE {request_id} {'x' * 256}\n".encode("ascii"))
            next_probe = now + 0.1
        response = reader.receive(min(0.1, max(0, deadline - time.monotonic())))
        if response is None:
            continue
        if response.get("request_id") != request_id:
            raise ValueError("Response request ID does not match outstanding PING")
        kind = response.get("type")
        if kind == "ARMED":
            armed = True
        elif kind == "ERROR":
            return response
        elif kind == "CSI":
            data = response.get("csi")
            if not isinstance(data, list) or len(data) != response.get("len"):
                raise ValueError("CSI array length does not match metadata")
            if not all(type(value) is int and -128 <= value <= 127 for value in data):
                raise ValueError("CSI contains values outside signed-byte range")
            return response
        else:
            raise ValueError(f"Unexpected response type: {kind}")
    raise TimeoutError("No terminal response; close connection before another request")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=LISTEN_HOST)
    parser.add_argument("--port", type=int, default=LISTEN_PORT)
    parser.add_argument("--count", type=int, default=SAMPLE_COUNT)
    parser.add_argument("--output", default=OUTPUT_FILENAME,
                        help="Complete responses as JSON Lines (relative to this script)")
    args = parser.parse_args()
    if args.count < 1:
        parser.error("--count must be positive")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((args.host, args.port))
        listener.listen(1)
        print(f"Waiting for ESP32 on {args.host}:{args.port}", flush=True)
        connection, address = listener.accept()
        with connection:
            connection.settimeout(5)
            connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            reader = JsonLines(connection)
            hello = reader.receive(5)
            if not hello or hello.get("type") != "HELLO" or hello.get("schema_version") != 1:
                raise ValueError("Missing or incompatible HELLO")
            print(f"Connected: {address}, {json.dumps(hello)}", flush=True)
            connection.sendall(b"READY\n")
            ready = reader.receive(5)
            if not ready or ready.get("type") != "READY":
                raise ValueError("Missing READY acknowledgment")
            output_path = Path(args.output) if args.output else None
            if output_path and not output_path.is_absolute():
                output_path = Path(__file__).resolve().parent / output_path
            output = open(output_path, "a", encoding="utf-8") if output_path else None
            if output:
                print(f"Appending responses to {output_path}", flush=True)
            try:
                for request_id in range(1, args.count + 1):
                    response = capture(connection, reader, request_id)
                    line = json.dumps(response, separators=(",", ":"))
                    print(line, flush=True)
                    if output:
                        output.write(line + "\n")
                        output.flush()
            finally:
                if output:
                    output.close()


if __name__ == "__main__":
    main()
