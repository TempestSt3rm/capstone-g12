"""Train/evaluate the position classifier, or predict a mystery CSV."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from DataProcessing.helpers.classifier import PositionClassifier, TrainingConfig

BASE = Path(__file__).resolve().parent
# Edit these settings, then run this file without command-line arguments.
# Relative file paths are resolved from this script's directory.
MODE = "predict"  # "train" or "predict"
CSV_FILES = ["../Data/x0y0.csv", "../Data/x0y1.csv", "../Data/x1y0.csv", "../Data/x1y1.csv"]
MYSTERY_CSV = "../Data/mystery.csv"
CONFIG = TrainingConfig(base_directory=BASE)


def main():
    classifier = PositionClassifier(CONFIG)
    if MODE == "train":
        classifier.train(CSV_FILES)
    elif MODE == "predict":
        classifier.predict(MYSTERY_CSV)
    else:
        raise ValueError('MODE must be "train" or "predict"')


if __name__ == "__main__":
    main()
