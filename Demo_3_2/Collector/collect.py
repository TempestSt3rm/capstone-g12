"""ENTRY POINT: sequential CSI collection over the laptop hotspot."""
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from Collector.helpers.protocol import ReceiverClient
from Collector.helpers.recording import CollectionSession
from Collector.helpers.transport import TcpTransport

HOST = "192.168.137.2"  # Must match transmitterIp in radio_config.h.
PORT = 3334
LABEL = "test6"
SAMPLE_COUNT = 500
FEATURE_MASK = 0b011  # LLTF + HT-LTF, matching Demo_3_1 features.
SAMPLE_TIMEOUT_MS = 5000
DATA = Path(__file__).resolve().parents[1] / "Data"

def collect(link, label, count, directory=DATA):
    return CollectionSession(ReceiverClient(link), FEATURE_MASK, SAMPLE_TIMEOUT_MS).run(label, count, directory)

def main():
    print(f"Collecting {SAMPLE_COUNT} samples for {LABEL} through {HOST}:{PORT}")
    with socket.create_connection((HOST, PORT), timeout=10) as connection:
        collect(TcpTransport(connection), LABEL, SAMPLE_COUNT)

if __name__ == "__main__":
    main()
