"""Sequential TCP collection, stream integrity, recording safety, and ML checks."""
import contextlib
import importlib.util
import io
import socket
import struct
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("collector", ROOT / "Collector/collect.py")
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)
from Collector.helpers.protocol import Frame, FrameDecoder, Message, ReceiverClient, MARKER, HEADER, SUMMARY
from Collector.helpers.transport import TcpTransport
from Collector.helpers.schema import SampleValidator, columns_for_mask
from DataProcessing.helpers.classifier import PositionClassifier, TrainingConfig, load_csv

class FakeLink:
    def __init__(self, value=10, failure=None):
        self.pending = bytearray()
        self.commands = []
        self.value, self.failure = value, failure
        self.index = 0

    def write(self, data):
        # No next request is allowed until the previous response is consumed.
        if self.pending:
            raise AssertionError("Request sent before previous response was consumed")
        command = data.decode().strip()
        self.commands.append(command)
        if command == "READY":
            self.pending.extend(b"READY\n")
        else:
            _, count, mask, timeout = command.split()
            assert int(count) == 1
            self.index += 1
            first = 100 + self.index * 2
            if self.failure == "ack":
                self.pending.extend(b"BATCH 99 2 3\n")
                return len(data)
            self.pending.extend(f"BATCH {first} 1 {mask}\n".encode())
            if self.failure == "error":
                self.pending.extend(Frame(Message.ERROR, first, struct.pack("<H", 5)).encode())
                return len(data)
            packet = first + (1 if self.failure == "sequence" else 0)
            self.pending.extend(Frame(int(mask), packet, bytes([self.value & 255]) * (int(mask).bit_count()*128)).encode())
            if self.failure != "truncated":
                completed = 0 if self.failure == "done_count" else 1
                self.pending.extend(Frame(Message.DONE, first+1, SUMMARY.pack(completed, 0)).encode())
        return len(data)

    def read(self, size):
        if not self.pending:
            raise ConnectionError("Simulated EOF")
        size = min(size, 17, len(self.pending))
        result = bytes(self.pending[:size])
        del self.pending[:size]
        return result

class WorkflowTests(unittest.TestCase):
    def test_fragmented_and_coalesced_binary_frames(self):
        frames = [Frame(3, 5, bytes(256)), Frame(Message.DONE, 6, SUMMARY.pack(1, 0))]
        wire = b"".join(frame.encode() for frame in frames)
        for size in (1, 3, 5, 17, 4096):
            decoder, output = FrameDecoder(), []
            for offset in range(0, len(wire), size):
                decoder.feed(wire[offset:offset+size])
                while (frame := decoder.pop()) is not None:
                    output.append(frame)
            self.assertEqual(output, frames)

    def test_invalid_header_and_truncated_payload(self):
        decoder = FrameDecoder()
        decoder.feed(MARKER + HEADER.pack(8, 1))
        with self.assertRaises(ValueError):
            decoder.pop()
        decoder = FrameDecoder()
        decoder.feed(Frame(3, 1, bytes(256)).encode()[:-1])
        self.assertIsNone(decoder.pop())

    def test_signed_features_and_mask(self):
        frame = Frame(3, 1, bytes([156])*256)
        self.assertEqual(SampleValidator(3).validate(frame).features[:6], [0,0,0,0,-100,-100])
        with self.assertRaises(ValueError):
            SampleValidator(7).validate(frame)
        self.assertEqual(len(columns_for_mask(3)), 256)

    def test_strictly_one_command_per_sample(self):
        link = FakeLink()
        with tempfile.TemporaryDirectory(dir=ROOT / "tests") as folder, contextlib.redirect_stdout(io.StringIO()):
            path = collector.collect(link, "A", 5, folder)
            values, _ = load_csv(path)
            self.assertEqual(values.shape, (5,256))
            self.assertEqual(link.commands, ["READY"] + ["COLLECT 1 3 5000"]*5)
            decoder = FrameDecoder()
            decoder.feed(path.with_suffix(".bin").read_bytes())
            ids = []
            while (frame := decoder.pop()) is not None:
                ids.append(frame.packet_id)
            self.assertEqual(ids, [102,104,106,108,110])
            before = path.read_bytes()
            with self.assertRaises(FileExistsError):
                collector.collect(FakeLink(), "A", 1, folder)
            self.assertEqual(path.read_bytes(), before)

    def test_failure_never_publishes_completed_csv(self):
        for failure in ("ack", "error", "sequence", "truncated", "done_count"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory(dir=ROOT / "tests") as folder, contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises((RuntimeError,ValueError,ConnectionError)):
                    collector.collect(FakeLink(failure=failure), "A", 3, folder)
                self.assertFalse((Path(folder)/"A.csv").exists())
                self.assertTrue((Path(folder)/"A.csv.partial").exists())
                with self.assertRaises(FileExistsError):
                    collector.collect(FakeLink(), "A", 1, folder)

    def test_short_write_deadline_and_multisample_rejection(self):
        class Short(FakeLink):
            def write(self, data):
                return len(data)-1
        with self.assertRaises(OSError):
            ReceiverClient(Short()).start_session()
        with self.assertRaises(TimeoutError):
            ReceiverClient(FakeLink()).read_frame(0)
        with self.assertRaises(ValueError):
            list(ReceiverClient(FakeLink()).batch_frames(2,3))

    def test_real_tcp_handshake_fragmentation_and_eof(self):
        listener = socket.socket()
        listener.bind(("127.0.0.1",0))
        listener.listen(1)
        errors = []
        def server():
            try:
                with listener.accept()[0] as connection:
                    connection.settimeout(3)
                    def command():
                        data = bytearray()
                        while not data.endswith(b"\n"):
                            chunk = connection.recv(1)
                            if not chunk:
                                raise ConnectionError("Client disconnected")
                            data.extend(chunk)
                        return bytes(data)
                    self.assertEqual(command(), b"READY\n")
                    connection.sendall(b"REA")
                    connection.sendall(b"DY\n")
                    self.assertEqual(command(), b"COLLECT 1 3 5000\n")
                    response = b"BATCH 10 1 3\n" + Frame(3,10,bytes(256)).encode() + Frame(Message.DONE,11,SUMMARY.pack(1,0)).encode()
                    for offset in range(0,len(response),7):
                        connection.sendall(response[offset:offset+7])
            except BaseException as error:
                errors.append(error)
        thread = threading.Thread(target=server,daemon=True)
        thread.start()
        try:
            with socket.create_connection(listener.getsockname(),timeout=2) as connection:
                link = TcpTransport(connection)
                client = ReceiverClient(link)
                client.start_session()
                self.assertEqual(sum(f.is_sample for f in client.batch_frames(1,3,5000)),1)
                thread.join(3)
                with self.assertRaises(ConnectionError):
                    link.read(1)
            self.assertFalse(thread.is_alive())
            self.assertFalse(errors, errors)
        finally:
            listener.close()

    def test_synthetic_collection_training_prediction(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "tests") as folder, contextlib.redirect_stdout(io.StringIO()):
            files = [collector.collect(FakeLink(value=value),label,120,folder)
                     for label,value in (("A",-30),("B",30))]
            mystery = collector.collect(FakeLink(value=30),"mystery",10,folder)
            classifier = PositionClassifier(TrainingConfig(ROOT,output_directory=str(Path(folder)/"results")))
            classifier.train(files)
            self.assertEqual(classifier.predict(mystery)["most_similar_position"],"B")

if __name__ == "__main__":
    unittest.main()
