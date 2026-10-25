"""Minimal CSI packet decoding, batch integrity, and ML regression checks."""
import contextlib
import importlib.util
import io
import struct
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("collector", ROOT / "Collector/collect.py")
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)
from Collector.helpers.protocol import Frame, FrameDecoder, Message, ReceiverClient, HEADER, REQUEST, SUMMARY
from Collector.helpers.schema import SampleValidator, columns_for_mask
from Collector.helpers.recording import CollectionSession
from DataProcessing.helpers.classifier import PositionClassifier, TrainingConfig, load_csv


def sample(index=0, value=10, mask=3):
    return Frame(mask, 99 + index, bytes([value & 255]) * (mask.bit_count() * 128))


class FakeSerial:
    def __init__(self, value=10, actual_mask=3, failure=None):
        self.pending = bytearray(b"ESP boot diagnostics\r\n")
        self.commands = []
        self.value, self.actual_mask, self.failure = value, actual_mask, failure

    @property
    def in_waiting(self):
        return len(self.pending)

    def write(self, data):
        text = data.decode().strip()
        self.commands.append(text)
        if text == "READY":
            self.pending.extend(b"\nREADY\n")
        else:
            _, count, mask, timeout = text.split()
            count, mask = int(count), int(mask)
            self.pending.extend(f"BATCH 99 {count} {mask}\n".encode())
            frames = []
            for index in range(count):
                if self.failure == "disconnect" and index == 1:
                    frames.append(Frame(Message.ERROR, 100, struct.pack("<H", 5)))
                    break
                frame = sample(index, self.value, self.actual_mask)
                if self.failure == "sequence" and index == 1:
                    frame = sample(2, self.value, self.actual_mask)
                frames.append(frame)
            if self.failure not in ("disconnect", "truncated"):
                completed = count - 1 if self.failure == "done_count" else count
                frames.append(Frame(Message.DONE, 99 + count, SUMMARY.pack(completed, 0)))
            for frame in frames:
                self.pending.extend(frame.encode())
        return len(data)

    def read(self, size):
        if not self.pending:
            raise TimeoutError("Simulated disconnect/end of stream")
        count = min(size, 17, len(self.pending))
        result = bytes(self.pending[:count])
        del self.pending[:count]
        return result


def read_frames(path):
    decoder = FrameDecoder()
    decoder.feed(path.read_bytes())
    result = []
    while (frame := decoder.pop()) is not None:
        result.append(frame)
    return result


class BinaryBatchTests(unittest.TestCase):
    def test_five_byte_header_and_no_extra_sample_metadata(self):
        self.assertEqual(HEADER.size, 5)
        self.assertEqual(REQUEST.size, 9)
        self.assertEqual(SUMMARY.size, 8)
        wire = Frame(3, 0x12345678, bytes(256)).encode()
        self.assertEqual(wire[:5].hex(), "0378563412")
        self.assertEqual(len(wire), 261)

    def test_fragmented_and_coalesced_packets(self):
        expected = [sample(), sample(1, mask=7)]
        wire = b"".join(frame.encode() for frame in expected)
        for size in (1, 3, 5, 17, 128, 4096):
            decoder, output = FrameDecoder(), []
            for offset in range(0, len(wire), size):
                decoder.feed(wire[offset:offset + size])
                while (frame := decoder.pop()) is not None:
                    output.append(frame)
            self.assertEqual(output, expected)

    def test_invalid_mask_and_incomplete_payload(self):
        decoder = FrameDecoder()
        decoder.feed(b"\x08")
        with self.assertRaises(ValueError):
            decoder.pop()
        decoder = FrameDecoder()
        decoder.feed(sample().encode()[:-1])
        self.assertIsNone(decoder.pop())
        with self.assertRaises(ValueError):
            Frame(3, 1, bytes(128)).encode()

    def test_ltf_mask_locates_blocks_in_ascending_bit_order(self):
        frame = Frame(7, 1, bytes([1]) * 128 + bytes([2]) * 128 + bytes([3]) * 128)
        measurement = SampleValidator(3).validate(frame)
        self.assertEqual(measurement.features, [0] * 4 + [1] * 124 + [2] * 128)
        self.assertEqual(measurement.blocks["STBC-HT-LTF"], [3] * 128)
        self.assertEqual(SampleValidator(2).validate(frame).features, [2] * 128)
        self.assertEqual(columns_for_mask(2)[0], "img_64")

    def test_signed_bytes_and_unavailable_stbc(self):
        measurement = SampleValidator(3).validate(sample(value=-100))
        self.assertEqual(measurement.features[:6], [0, 0, 0, 0, -100, -100])
        with self.assertRaises(ValueError):
            SampleValidator(7).validate(sample(mask=3))

    def test_every_mask_can_be_decoded(self):
        for mask in range(1, 8):
            result = SampleValidator(mask).validate(sample(mask=mask))
            self.assertEqual(len(result.features), mask.bit_count() * 128)
            self.assertEqual(len(result.blocks), mask.bit_count())
            self.assertEqual(len(columns_for_mask(mask)), len(result.features))

    def test_truncated_measurement_is_rejected(self):
        frame = sample()
        with self.assertRaises(ValueError):
            SampleValidator(3).validate(Frame(3, frame.packet_id, frame.payload[:-1]))

    def test_one_command_for_1000_samples_and_raw_file_contents(self):
        link = FakeSerial()
        with tempfile.TemporaryDirectory(dir=ROOT / "tests") as folder, contextlib.redirect_stdout(io.StringIO()):
            path = collector.collect(link, "A", 1000, folder)
            values, columns = load_csv(path)
            self.assertEqual(values.shape, (1000, 256))
            self.assertEqual(len(columns), 256)
            self.assertEqual(link.commands, ["READY", "COLLECT 1000 3 120000"])
            frames = read_frames(path.with_suffix(".bin"))
            self.assertEqual(len(frames), 1000)
            self.assertTrue(all(frame.is_sample for frame in frames))
            self.assertEqual(path.with_suffix(".bin").stat().st_size, 261000)
            with self.assertRaises(FileExistsError):
                collector.collect(FakeSerial(), "A", 1, folder)

    def test_failed_batches_keep_partials(self):
        for failure in ("disconnect", "sequence", "truncated", "done_count"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory(dir=ROOT / "tests") as folder, contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises((RuntimeError, ValueError, TimeoutError)):
                    collector.collect(FakeSerial(failure=failure), "A", 3, folder)
                self.assertFalse((Path(folder) / "A.csv").exists())
                self.assertTrue((Path(folder) / "A.csv.partial").exists())

    def test_bad_batch_acknowledgment(self):
        class BadAck(FakeSerial):
            def write(self, data):
                result = super().write(data)
                if data.startswith(b"COLLECT"):
                    self.pending = bytearray(b"BATCH 99 99 3\n")
                return result
        client = ReceiverClient(BadAck())
        client.start_session()
        with self.assertRaises(ValueError):
            list(client.batch_frames(1, 3))

    def test_partial_command_write_and_deadline(self):
        class ShortWriter(FakeSerial):
            def write(self, data):
                return len(data) - 1
        with self.assertRaises(OSError):
            ReceiverClient(ShortWriter()).start_session()
        with self.assertRaises(TimeoutError):
            ReceiverClient(FakeSerial()).read_frame(0)

    def test_selected_features_match_across_actual_masks(self):
        first = SampleValidator(3).validate(sample(mask=3))
        second = SampleValidator(3).validate(sample(mask=7))
        self.assertEqual(first.features, second.features)

    def test_batch_timeout_is_not_a_collection_sleep(self):
        link = FakeSerial()
        client = ReceiverClient(link)
        client.start_session()
        frames = list(client.batch_frames(3, 3, 600000))
        self.assertEqual(sum(frame.is_sample for frame in frames), 3)
        self.assertEqual(client.summary["count"], 3)

    def test_synthetic_batch_through_training_and_mystery_prediction(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "tests") as folder, contextlib.redirect_stdout(io.StringIO()):
            files = [collector.collect(FakeSerial(value=value), label, 120, folder)
                     for label, value in (("A", -30), ("B", 30))]
            mystery = collector.collect(FakeSerial(value=30), "mystery", 10, folder)
            classifier = PositionClassifier(TrainingConfig(ROOT, output_directory=str(Path(folder) / "results")))
            report = classifier.train(files)
            summary = classifier.predict(mystery)
            self.assertEqual(summary["most_similar_position"], "B")
            self.assertEqual(report["training_rows"], 92)
            self.assertEqual(report["test_rows"], 48)
            with self.assertRaises(ValueError):
                load_csv(mystery, columns_for_mask(1))


if __name__ == "__main__":
    unittest.main()
