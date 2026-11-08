"""Compile and run the same sequence controller used by transmitter firmware."""

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class FirmwareTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("g++"), "g++ is needed for native firmware checks")
    def test_transmitter_only_advances_in_receiver_order(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as folder:
            binary = Path(folder) / "test_sequence.exe"
            subprocess.run(["g++", "-std=c++11", "-Wall", "-Wextra", "-Werror",
                            "-I", str(root / "Radio/include"), str(root / "tests/native/test_sequence.cpp"),
                            "-o", str(binary)], check=True, capture_output=True, text=True)
            subprocess.run([str(binary)], check=True, capture_output=True, text=True)
