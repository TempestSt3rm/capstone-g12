"""Train/evaluate a raw-CSI position classifier, or predict a mystery CSV."""
import csv
import json
from collections import Counter
from pathlib import Path
from dataclasses import dataclass

import joblib
import numpy as np
from sklearn.dummy import DummyClassifier
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVC

from Collector.helpers.schema import columns_for_mask

def load_csv(path, expected_columns=None):
    """One row = one measurement. Preserve every raw imaginary/real column."""
    path = Path(path)
    with path.open(newline="", encoding="utf-8-sig") as handle:
        columns = next(csv.reader(handle), [])
    if not columns or len(columns) % 2:
        raise ValueError(f"{path.name}: expected imaginary/real column pairs")
    if not any(columns == columns_for_mask(mask) for mask in range(1, 8)):
        raise ValueError(f"{path.name}: unexpected CSI column order")
    if expected_columns is not None and columns != expected_columns:
        raise ValueError(f"{path.name}: columns do not match the training data/model")
    values = np.loadtxt(path, delimiter=",", skiprows=1, ndmin=2)
    if not values.size or values.shape[1] != len(columns) or not np.isfinite(values).all():
        raise ValueError(f"{path.name}: empty, incomplete, or nonfinite data")
    return values, columns


def save_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


@dataclass(frozen=True)
class TrainingConfig:
    """Paths resolve against base_directory; test rows never enter model fitting."""
    base_directory: Path
    test_fraction: float = 0.20
    gap_rows: int = 50
    svm_c: float = 1.0
    svm_gamma: str = "scale"
    output_directory: str = "ml_results"
    model_filename: str = "csi_svm.joblib"


class PositionClassifier:
    """Train/evaluate or load/predict without module-level mutable settings."""
    def __init__(self, config: TrainingConfig):
        self.config = config

    def resolve_path(self, filename):
        path = Path(filename)
        return path if path.is_absolute() else Path(self.config.base_directory) / path

    def prepare_data(self, csv_files):
        """Split each recording in original row order, before fitting anything."""
        if not 0 < self.config.test_fraction < 1 or type(self.config.gap_rows) is not int or self.config.gap_rows < 0:
            raise ValueError("test_fraction must be between 0 and 1; gap_rows a nonnegative integer")
        if len(csv_files) < 2:
            raise ValueError("Provide at least two labeled CSV files")
        train_x, train_y, test_x, test_y = [], [], [], []
        columns = None
        details, test_sources = [], []
        labels = set()
        paths = set()
        for filename in csv_files:
            path = self.resolve_path(filename).resolve()
            label = path.stem  # Filename supplies the known position, not a feature.
            if path in paths or label in labels:
                raise ValueError("Each CSV must have a unique path and position filename")
            paths.add(path)
            labels.add(label)
            values, columns = load_csv(path, columns)
            count = len(values)
            test_count = max(1, int(np.ceil(count * self.config.test_fraction)))
            test_start = count - test_count
            train_end = test_start - self.config.gap_rows
            if train_end < 2:
                raise ValueError(f"{label}: too few rows for this test fraction and gap")
            train_x.append(values[:train_end])
            test_x.append(values[test_start:])
            train_y.extend([label] * train_end)
            test_y.extend([label] * test_count)
            # CSV row numbers include the header, making predictions traceable.
            test_sources.extend((str(path), row + 2) for row in range(test_start, count))
            details.append({"label": label, "source_csv": str(path), "total_rows": count,
                            "training_rows": train_end, "excluded_gap_rows": self.config.gap_rows,
                            "test_rows": test_count, "first_test_csv_row": test_start + 2})
        return (np.vstack(train_x), np.asarray(train_y), np.vstack(test_x),
                np.asarray(test_y), columns, details, test_sources)

    def train(self, csv_files):
        x_train, y_train, x_test, y_test, columns, recordings, sources = self.prepare_data(csv_files)
        for recording in recordings:
            print(f"{recording['label']}: {recording['training_rows']} train / "
                  f"{recording['excluded_gap_rows']} gap / {recording['test_rows']} test")
        print(f"Using all {len(columns)} raw features per row; no amplitude conversion or PCA.")

        # fit() sees only training rows. The scaler learns training means/stds and
        # applies those same values to test/mystery rows, avoiding preprocessing leakage.
        model = Pipeline([
            ("scaler", StandardScaler()),
            ("classifier", SVC(kernel="rbf", C=self.config.svm_c, gamma=self.config.svm_gamma)),
        ])
        model.fit(x_train, y_train)
        predictions = model.predict(x_test)
        classes = model.named_steps["classifier"].classes_.tolist()

        # Compare against always choosing the most frequent training position.
        baseline = DummyClassifier(strategy="most_frequent")
        baseline.fit(x_train, y_train)
        baseline_accuracy = accuracy_score(y_test, baseline.predict(x_test))
        accuracy = accuracy_score(y_test, predictions)
        matrix = confusion_matrix(y_test, predictions, labels=classes)
        report_text = classification_report(y_test, predictions, labels=classes,
                                            digits=3, zero_division=0)
        report = {
            "evaluation": "later block within each existing recording; not independent visits",
            "feature_representation": "all raw imaginary/real columns, training-only standard scaling",
            "feature_count": len(columns), "training_rows": len(y_train), "test_rows": len(y_test),
            "test_fraction": self.config.test_fraction, "gap_rows": self.config.gap_rows,
            "model_parameters": {"kernel": "rbf", "C": self.config.svm_c, "gamma": self.config.svm_gamma},
            "accuracy": float(accuracy), "baseline_accuracy": float(baseline_accuracy),
            "classes": classes, "recordings": recordings,
            "confusion_matrix_rows_true_columns_predicted": matrix.tolist(),
            "classification_report": classification_report(y_test, predictions, labels=classes,
                                                            output_dict=True, zero_division=0),
        }
        output = self.resolve_path(self.config.output_directory)
        output.mkdir(parents=True, exist_ok=True)
        save_json(output / "evaluation.json", report)
        with (output / "test_predictions.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["source_csv", "csv_row", "true_position", "predicted_position"])
            writer.writerows((path, row, truth, prediction)
                             for (path, row), truth, prediction in zip(sources, y_test, predictions))
        with (output / "confusion_matrix.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["true_position / predicted_position", *classes])
            writer.writerows((label, *row) for label, row in zip(classes, matrix))

        # Save the evaluated pipeline as-is. Do not refit it on the held-out rows.
        joblib.dump({"schema_version": 1, "pipeline": model, "columns": columns,
                     "classes": classes, "evaluation": report}, output / self.config.model_filename)
        print(f"\nHeld-out accuracy: {accuracy:.1%}; baseline: {baseline_accuracy:.1%}\n")
        print(report_text)
        print(f"Saved model, evaluation, confusion matrix, and test predictions in {output}")
        print("This is a within-recording check. New visits are needed to test localization.")
        return report

    def predict(self, mystery_csv):
        output = self.resolve_path(self.config.output_directory)
        artifact = joblib.load(output / self.config.model_filename)
        if artifact.get("schema_version") != 1:
            raise ValueError("Unsupported model artifact version")
        path = self.resolve_path(mystery_csv)
        values, _ = load_csv(path, artifact["columns"])
        predictions = artifact["pipeline"].predict(values)
        counts = Counter(predictions.tolist())
        best_count = max(counts.values())
        tied_positions = sorted(label for label, count in counts.items() if count == best_count)
        summary = {
            "source_csv": str(path), "measurement_count": len(predictions),
            "most_similar_position": tied_positions[0] if len(tied_positions) == 1 else None,
            "top_positions": tied_positions,
            "vote_counts": {label: counts.get(label, 0) for label in artifact["classes"]},
            "top_vote_fraction": best_count / len(predictions),
            "interpretation": "row prediction agreement, not a calibrated probability or physical distance",
        }
        save_json(output / "mystery_summary.json", summary)
        with (output / "mystery_predictions.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["csv_row", "predicted_position"])
            writer.writerows((row + 2, label) for row, label in enumerate(predictions))
        print(json.dumps(summary, indent=2))
        print("The classifier chooses among known positions; it has no unknown-position rejection yet.")
        return summary


