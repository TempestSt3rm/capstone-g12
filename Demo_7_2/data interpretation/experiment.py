"""Compare seven-distance classifiers using whole-recording validation.

Run: python "Demo_7_2/data interpretation/experiment.py"
Outputs: results.json, predictions.jsonl.gz, comparison.png in this folder/results.
"""

import argparse
from collections import Counter
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import time

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import sklearn
from sklearn.base import clone
from sklearn.dummy import DummyClassifier
from sklearn.ensemble import RandomForestClassifier
from sklearn.feature_selection import VarianceThreshold
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, balanced_accuracy_score, confusion_matrix, mean_absolute_error
from sklearn.neighbors import KNeighborsClassifier
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVC


HERE = Path(__file__).resolve().parent
DEMO = HERE.parent
DISTANCES = np.arange(0.5, 4.0, 0.5)


def load_data(cleaned, raw_dir):
    with cleaned.open(encoding="utf-8") as stream:
        rows = [json.loads(line) for line in stream]
    if not rows:
        raise ValueError("No cleaned samples")
    if len({row["layout_id"] for row in rows}) != 1:
        raise ValueError("Use one CSI layout per experiment; layouts cannot be combined blindly")
    # Reconstruct pre-mask CSI so VarianceThreshold learns constants from the
    # training recordings only, rather than using the exploratory global mask.
    sources = {}
    hashes = {}
    for filename in sorted({row["source"]["file"] for row in rows}):
        path = raw_dir / filename
        if path.resolve().parent != raw_dir.resolve():
            raise ValueError("Source file must be directly inside raw_dir")
        hashes[filename] = hashlib.sha256(path.read_bytes()).hexdigest()
        with path.open(encoding="utf-8-sig") as stream:
            sources[filename] = {line: json.loads(text) for line, text in enumerate(stream, 1)}
    packets = [sources[row["source"]["file"]][row["source"]["line"]] for row in rows]
    for row, packet in zip(rows, packets):
        if (row["target"]["distance_m"] != packet["distance_m"] or
                row["group"]["recording_id"] != packet["recording_id"]):
            raise ValueError("Cleaned/raw provenance mismatch; rerun prepare")
    iq = np.asarray([packet["csi_raw"] for packet in packets], dtype=np.float64)
    if iq.ndim != 2 or iq.shape[1] % 2 or not np.isfinite(iq).all():
        raise ValueError("CSI requires finite, equal-length real/imag byte pairs")
    if any(packet["first_word_invalid"] for packet in packets):
        iq = iq[:, 4:]  # Protocol validity mask, not a fitted statistical filter.
    amplitudes = np.hypot(iq[:, 0::2], iq[:, 1::2])
    row_mean = amplitudes.mean(axis=1, keepdims=True)
    normalized = np.divide(amplitudes, row_mean, out=np.zeros_like(amplitudes), where=row_mean != 0)
    signal = np.asarray([[packet[key] for key in ("rssi", "agc_gain", "fft_gain")] for packet in packets], dtype=float)
    labels_m = np.asarray([row["target"]["distance_m"] for row in rows], dtype=float)
    if not np.isin(labels_m, DISTANCES).all():
        raise ValueError("Unexpected distance label")
    labels = np.rint(labels_m * 2).astype(int) - 1
    features = {"signal_only": signal, "amplitude_only": amplitudes,
                "normalized_amplitude": normalized,
                "amplitude_and_signal": np.column_stack((amplitudes, signal)),
                "raw_iq_and_signal": np.column_stack((iq, signal))}
    return rows, features, labels, hashes


def recording_folds(rows, labels):
    groups = {}
    for row, label in zip(rows, labels):
        group = row["group"]["recording_id"]
        if group in groups and groups[group]["label"] != int(label):
            raise ValueError("Recording contains more than one distance")
        groups[group] = dict(label=int(label), file=row["source"]["file"],
                             session_id=row["group"].get("session_id"),
                             orientation=row["group"].get("orientation", ""))
    by_label = {label: sorted((g for g in groups if groups[g]["label"] == label),
                              key=lambda g: (groups[g]["file"], g)) for label in range(7)}
    folds = min(len(value) for value in by_label.values())
    if folds < 2:
        raise ValueError("Need at least two recordings at every distance for grouped validation")
    assignment = {}
    for recordings in by_label.values():
        for index, group in enumerate(recordings):
            assignment[group] = index % folds
    sample_folds = np.asarray([assignment[row["group"]["recording_id"]] for row in rows])
    return sample_folds, groups, assignment, folds


def models():
    return {
        "majority_baseline": DummyClassifier(strategy="prior"),
        "logistic_regression": LogisticRegression(C=1.0, max_iter=3000, class_weight="balanced", random_state=42),
        "knn": KNeighborsClassifier(n_neighbors=15, weights="distance", n_jobs=4),
        "random_forest": RandomForestClassifier(n_estimators=150, min_samples_leaf=3,
                                                 class_weight="balanced", random_state=42, n_jobs=4),
        "rbf_svm": SVC(C=1.0, gamma="scale", class_weight="balanced"),
    }


def evaluate(feature_name, name, model, X, y, sample_folds, groups, rows, folds):
    predicted = np.full(len(y), -1, dtype=int)
    fold_results = []
    started = time.perf_counter()
    for fold in range(folds):
        training, held_out = sample_folds != fold, sample_folds == fold
        pipeline = make_pipeline(VarianceThreshold(), StandardScaler(), clone(model))
        pipeline.fit(X[training], y[training])
        predicted[held_out] = pipeline.predict(X[held_out])
        fold_results.append(dict(fold=fold, training_packets=int(training.sum()),
                                 held_out_packets=int(held_out.sum()),
                                 accuracy=float(accuracy_score(y[held_out], predicted[held_out])),
                                 balanced_accuracy=float(balanced_accuracy_score(y[held_out], predicted[held_out])),
                                 retained_features=int(pipeline[0].get_support().sum())))
    votes = []
    for group, metadata in sorted(groups.items(), key=lambda item: item[1]["file"]):
        indices = [index for index, row in enumerate(rows) if row["group"]["recording_id"] == group]
        tally = np.bincount(predicted[indices], minlength=7)
        winners = np.flatnonzero(tally == tally.max())
        winner = int(winners[0])  # Explicit deterministic tie-break: nearest label index to zero.
        votes.append(dict(recording_id=group, file=metadata["file"],
                          distance_m=float(DISTANCES[metadata["label"]]),
                          predicted_m=float(DISTANCES[winner]), vote_tied=len(winners) > 1,
                          correct=winner == metadata["label"]))
    metrics = dict(feature_set=feature_name, model=name, input_features=int(X.shape[1]),
                   packet_accuracy=float(accuracy_score(y, predicted)),
                   balanced_packet_accuracy=float(balanced_accuracy_score(y, predicted)),
                   packet_mae_m=float(mean_absolute_error(DISTANCES[y], DISTANCES[predicted])),
                   recording_vote_accuracy=float(np.mean([row["correct"] for row in votes])),
                   confusion_matrix=confusion_matrix(y, predicted, labels=np.arange(7)).tolist(),
                   per_fold=fold_results, recording_votes=votes,
                   duration_s=time.perf_counter() - started)
    return metrics, predicted


def draw_summary(results, output):
    feature_names = list(dict.fromkeys(row["feature_set"] for row in results))
    model_names = list(dict.fromkeys(row["model"] for row in results))
    scores = np.asarray([[next(row["balanced_packet_accuracy"] for row in results
                               if row["feature_set"] == feature and row["model"] == model)
                          for model in model_names] for feature in feature_names])
    best = max(results, key=lambda row: (row["recording_vote_accuracy"], row["balanced_packet_accuracy"]))
    fig, axes = plt.subplots(1, 2, figsize=(15, 6), layout="constrained")
    axes[0].imshow(scores, vmin=0, vmax=1, cmap="Blues")
    axes[0].set_xticks(range(len(model_names)), [x.replace("_", " ") for x in model_names], rotation=35, ha="right")
    axes[0].set_yticks(range(len(feature_names)), [x.replace("_", " ") for x in feature_names])
    axes[0].set_title("Balanced packet accuracy · whole-recording holdouts")
    for (r, c), value in np.ndenumerate(scores):
        axes[0].text(c, r, f"{value:.1%}", ha="center", va="center", color="white" if value > .6 else "black")
    matrix = np.asarray(best["confusion_matrix"])
    axes[1].imshow(matrix / matrix.sum(axis=1, keepdims=True), vmin=0, vmax=1, cmap="Blues")
    axes[1].set_xticks(range(7), DISTANCES)
    axes[1].set_yticks(range(7), DISTANCES)
    axes[1].set_xlabel("Predicted distance (m)")
    axes[1].set_ylabel("Recorded distance (m)")
    axes[1].set_title(f"{best['model']} / {best['feature_set']}\nExploratory best · packet counts")
    for (r, c), value in np.ndenumerate(matrix):
        axes[1].text(c, r, str(value), ha="center", va="center",
                     color="white" if value / matrix[r].sum() > .6 else "black", fontsize=9)
    fig.suptitle("Seven-distance model experiments · one acquisition session", fontsize=15)
    fig.savefig(output, dpi=160)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cleaned", type=Path, default=DEMO / "data/cleaned/samples.jsonl")
    parser.add_argument("--raw-dir", type=Path, default=DEMO / "data/raw")
    parser.add_argument("--output", type=Path, default=HERE / "results")
    args = parser.parse_args()
    rows, features, y, hashes = load_data(args.cleaned, args.raw_dir)
    sample_folds, groups, assignments, folds = recording_folds(rows, y)
    args.output.mkdir(parents=True, exist_ok=True)
    results = []
    with gzip.open(args.output / "predictions.jsonl.gz", "wt", encoding="utf-8") as stream:
        for feature_name, X in features.items():
            for name, model in models().items():
                metrics, predicted = evaluate(feature_name, name, model, X, y, sample_folds, groups, rows, folds)
                results.append(metrics)
                print(f"{feature_name:25} {name:20} balanced={metrics['balanced_packet_accuracy']:.1%} "
                      f"MAE={metrics['packet_mae_m']:.3f}m sets={metrics['recording_vote_accuracy']:.1%}", flush=True)
                for index, (row, guess) in enumerate(zip(rows, predicted)):
                    stream.write(json.dumps(dict(feature_set=feature_name, model=name, fold=int(sample_folds[index]),
                                                 source=row["source"], recording_id=row["group"]["recording_id"],
                                                 distance_m=float(DISTANCES[y[index]]), predicted_m=float(DISTANCES[guess]))) + "\n")
    results.sort(key=lambda row: (row["recording_vote_accuracy"], row["balanced_packet_accuracy"]), reverse=True)
    report = dict(created_at_utc=datetime.now(timezone.utc).isoformat(), packets=len(rows), recordings=len(groups),
                  folds=folds, labels_m=DISTANCES.tolist(),
                  versions=dict(numpy=np.__version__, sklearn=sklearn.__version__, matplotlib=matplotlib.__version__),
                  validation="Each distance's recordings are ordered by filename; trial index modulo fold count is held out. No recording crosses a fold.",
                  preprocessing="Full pre-mask CSI reconstructed from raw sources. Constant removal and scaling fit on training folds only. Amplitude normalization uses each packet's own mean.",
                  excluded_inputs=["distance label", "timestamps", "packet/request IDs", "recording/session IDs", "orientation", "source filename", "MCS"],
                  limitations=["One acquisition session; no evidence of cross-session generalization.",
                               "Orientation labels are blank; orientation generalization is unmeasured.",
                               "Scores compare exploratory choices on the same folds; best score is not an independent final estimate.",
                               "Recording majority votes describe whole sets, not instantaneous predictions."],
                  raw_sha256=hashes, cleaned_sha256=hashlib.sha256(args.cleaned.read_bytes()).hexdigest(),
                  fold_assignments={key: dict(groups[key], fold=fold) for key, fold in assignments.items()},
                  results=results)
    (args.output / "results.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    draw_summary(results, args.output / "comparison.png")
    print(f"Saved results to {args.output}", flush=True)


if __name__ == "__main__":
    main()
