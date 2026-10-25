"""Save one streamed batch as raw CSI packets and the model's feature CSV."""
import csv
import re
import time
from contextlib import ExitStack
from pathlib import Path
from .schema import SampleValidator,columns_for_mask


class RecordingWriter:
    def __init__(self,directory,label,mask):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*",label):
            raise ValueError("Use a simple position label")
        self.directory=Path(directory)
        self.label=label
        self.mask=mask
        self.paths=[self.directory/(label+suffix) for suffix in (".bin",".csv")]
        self.csv_path=self.paths[1]
        self.stack=ExitStack()

    def __enter__(self):
        self.directory.mkdir(parents=True,exist_ok=True)
        try:
            self.raw=self.stack.enter_context(self.paths[0].open("wb"))
            output=self.stack.enter_context(self.paths[1].open("w",newline="",encoding="utf-8"))
            self.features=csv.writer(output)
            self.features.writerow(columns_for_mask(self.mask))
        except Exception:
            self.stack.close()
            raise
        return self

    def append(self,measurement):
        self.raw.write(measurement.frame.encode())
        self.features.writerow(measurement.features)

    def __exit__(self,exc_type,exc_value,traceback):
        self.stack.close()
        return False


class CollectionSession:
    def __init__(self,client,feature_mask=3,timeout_ms=120000):
        columns_for_mask(feature_mask)
        self.client=client
        self.mask=feature_mask
        self.timeout_ms=timeout_ms

    def run(self,label,count,directory,progress=print):
        if type(count) is not int or not 1<=count<=100000:
            raise ValueError("Sample count must be 1..100000")
        validator=SampleValidator(self.mask)
        with RecordingWriter(directory,label,self.mask) as recording:
            self.client.start_session()
            started=time.monotonic()
            received=0
            for frame in self.client.batch_frames(count,self.mask,self.timeout_ms):
                if frame.is_sample:
                    recording.append(validator.validate(frame))
                    received+=1
                    if received%100==0 or received==count:
                        progress(f"{label}: {received}/{count}")
            if received!=count or self.client.summary is None:
                raise RuntimeError("Incomplete CSI batch")
        elapsed=time.monotonic()-started
        progress(f"Saved {received} samples in {elapsed:.2f}s; queue skips: {self.client.summary['dropped']}")
        progress(f"Features: {recording.csv_path}; raw CSI: {recording.paths[0]}")
        return recording.csv_path
