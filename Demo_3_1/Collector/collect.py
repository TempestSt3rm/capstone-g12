"""ENTRY POINT: request one binary CSI batch through the fixed transmitter."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from Collector.helpers.protocol import ReceiverClient
from Collector.helpers.recording import CollectionSession

# Edit these settings, then run this file without command-line arguments.
PORT = "COM7"               # Stationary transmitter, not the moving receiver.
LABEL = "mystery"              # Known position, or "mystery".
SAMPLE_COUNT = 500
BAUD_RATE = 115200          # Must match SerialBaud in Radio/include/radio_config.h.
FEATURE_MASK = 0b011         # bit0 LLTF, bit1 HT-LTF, bit2 STBC-HT-LTF.
BATCH_TIMEOUT_MS = 120000     # Failure deadline, not a delay between samples.
DATA = Path(__file__).resolve().parents[1] / "Data"


def collect(link, label, count, directory=DATA):
    return CollectionSession(ReceiverClient(link, baud_rate=BAUD_RATE), FEATURE_MASK, BATCH_TIMEOUT_MS).run(label, count, directory)


def main():
    import serial
    print(f"Collecting {SAMPLE_COUNT} samples for {LABEL} through transmitter {PORT}")
    # Set control lines before opening; avoid deliberately resetting the board.
    # READY retries handle boot/connect delays without a fixed startup sleep.
    with serial.Serial(port=None, baudrate=BAUD_RATE, timeout=0.1, write_timeout=2) as link:
        link.dtr = False
        link.rts = False
        link.port = PORT
        link.open()
        collect(link, LABEL, SAMPLE_COUNT)


if __name__ == "__main__":
    main()
