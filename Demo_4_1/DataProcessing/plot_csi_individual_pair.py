"""Save real/imaginary density heatmaps for up to 192 CSI pairs."""

import argparse
import json
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.colors import LogNorm
import numpy as np


def load_pairs(path):
    samples = []
    # Support both UTF-8 and Windows PowerShell's UTF-16 redirected output.
    with path.open("rb") as handle:
        encoding = "utf-16" if handle.read(2) in (b"\xff\xfe", b"\xfe\xff") else "utf-8-sig"
    with path.open(encoding=encoding) as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            location = f"{path}:{line_number}"
            try:
                record = json.loads(line)
            except ValueError as exc:
                raise ValueError(f"{location}: expected one JSON response per line") from exc
            csi = record.get("csi") if isinstance(record, dict) else None
            if (not isinstance(csi, list) or not 2 <= len(csi) <= 384 or len(csi) % 2 or
                    not all(type(value) is int and -128 <= value <= 127 for value in csi)):
                raise ValueError(f"{location}: expected 1..192 signed imag/real pairs (2..384 values)")
            # Old LLTF-only recordings omitted four invalid leading bytes.
            # New firmware retains the full buffer, including invalid bytes.
            samples.append([float("nan")] * 4 + csi if len(csi) == 124 else csi)
    if not samples:
        raise ValueError(f"{path}: no CSI recordings found")
    # Preserve pair offsets. Shorter frames have no data for trailing pairs.
    pair_count = max(len(sample) for sample in samples) // 2
    values = np.full((len(samples), pair_count * 2), np.nan)
    for row, sample in enumerate(samples):
        values[row, :len(sample)] = sample
    return values.reshape(-1, pair_count, 2)


def plot_heatmaps(path, pairs, output_dir, bins, show):
    bound = max(1, float(np.nanmax(np.abs(pairs)))) * 1.05
    extent = (-bound, bound, -bound, bound)
    pair_count = pairs.shape[1]
    columns = min(8, pair_count)
    rows = (pair_count + columns - 1) // columns
    fig, axes = plt.subplots(rows, columns, figsize=(3 * columns, 3 * rows),
                             sharex=True, sharey=True, squeeze=False, layout="constrained")
    plots = []
    for pair_index, ax in enumerate(axes.flat):
        if pair_index >= pair_count:
            ax.set_visible(False)
            continue
        imag = pairs[:, pair_index, 0]
        real = pairs[:, pair_index, 1]
        valid = np.isfinite(real) & np.isfinite(imag)
        if valid.any():
            plot = ax.hexbin(real[valid], imag[valid], gridsize=bins, extent=extent, mincnt=1,
                             cmap="viridis", norm=LogNorm(vmin=1))
            plots.append(plot)
        else:
            ax.text(0.5, 0.5, "Unavailable\nNo recorded values",
                    ha="center", va="center", transform=ax.transAxes, fontsize=9)
        ax.set_title(f"Pair {pair_index}", fontsize=10)
        ax.set_xlim(-bound, bound)
        ax.set_ylim(-bound, bound)
        ax.set_aspect("equal", adjustable="box")
        ax.axhline(0, color="gray", linewidth=0.4, alpha=0.4)
        ax.axvline(0, color="gray", linewidth=0.4, alpha=0.4)
        ax.tick_params(labelsize=7)

    # Use one density scale and identical axes for every pair.
    max_bin = max(2, max(float(plot.get_array().max()) for plot in plots))
    norm = LogNorm(vmin=1, vmax=max_bin)
    for plot in plots:
        plot.set_norm(norm)
    fig.colorbar(plots[0], ax=list(axes.flat)[:pair_count], shrink=0.6,
                 label="Measurement count per hexagon (log scale)")
    fig.supxlabel("Real")
    fig.supylabel("Imaginary")
    fig.suptitle(f"{path.stem} — all {pair_count} CSI pairs — {len(pairs)} measurements", fontsize=18)
    output_path = output_dir / f"{path.stem}_all_{pair_count}_heatmaps.png"
    fig.savefig(output_path, dpi=160)
    print(f"Saved {output_path}")
    if show:
        plt.show()
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="+", type=Path, help="JSON-lines captures (.txt or .jsonl)")
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parent / "plots")
    parser.add_argument("--bins", type=int, default=45, help="Hexbin resolution per pair (default: 45)")
    parser.add_argument("--show", action="store_true", help="Also open each saved heatmap grid")
    args = parser.parse_args()
    if args.bins < 2:
        parser.error("--bins must be at least 2")
    try:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        for path in args.files:
            plot_heatmaps(path, load_pairs(path), args.output_dir, args.bins, args.show)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
