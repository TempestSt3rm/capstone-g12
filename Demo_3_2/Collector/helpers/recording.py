"""Save sequential CSI measurements as raw packets and feature CSV."""
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
        self.partials=[Path(str(path)+".partial") for path in self.paths]
        self.stack=ExitStack()

    def __enter__(self):
        self.directory.mkdir(parents=True,exist_ok=True)
        try:
            if any(path.exists() for path in self.paths+self.partials):
                raise FileExistsError("Recording or partial already exists; choose a new label")
            self.raw=self.stack.enter_context(self.partials[0].open("xb"))
            output=self.stack.enter_context(self.partials[1].open("x",newline="",encoding="utf-8"))
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
        if exc_type is None:
            for partial, final in zip(self.partials,self.paths):
                partial.rename(final)
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
            dropped=0
            # Each COLLECT requests exactly one measurement. Do not issue the next
            # command until the sample AND its matching DONE have been validated.
            for _ in range(count):
                samples=[]
                for frame in self.client.batch_frames(1,self.mask,self.timeout_ms):
                    if frame.is_sample:
                        samples.append(validator.validate(frame))
                if len(samples)!=1 or self.client.summary is None:
                    raise RuntimeError("Incomplete CSI measurement")
                recording.append(samples[0])
                received+=1
                dropped+=self.client.summary["dropped"]
                if received%100==0 or received==count:
                    progress(f"{label}: {received}/{count}")
        elapsed=time.monotonic()-started
        progress(f"Saved {received} samples in {elapsed:.2f}s; queue skips: {dropped}")
        progress(f"Features: {recording.csv_path}; raw CSI: {recording.paths[0]}")
        return recording.csv_path
