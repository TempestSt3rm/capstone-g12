"""Compile and exercise the firmware's actual queue on the host."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]

@unittest.skipUnless(shutil.which("g++"), "g++ required for native firmware queue tests")
class FirmwareQueueTests(unittest.TestCase):
    def test_stalled_tcp_probes_control_priority_and_partial_frames(self):
        with tempfile.TemporaryDirectory(dir=ROOT/"tests") as directory:
            binary=Path(directory)/"tcp_queue.exe"
            subprocess.run([
                shutil.which("g++"), "-std=c++11", "-Wall", "-Wextra", "-Werror",
                "-I", str(ROOT/"Radio/include"),
                str(ROOT/"tests/native/test_tcp_queue.cpp"),
                "-o", str(binary),
            ],check=True,capture_output=True,text=True,timeout=60)
            subprocess.run([str(binary)],check=True,capture_output=True,text=True,timeout=10)
