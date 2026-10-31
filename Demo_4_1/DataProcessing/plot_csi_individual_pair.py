"""Save an 8x8 grid of real/imaginary density heatmaps for all 64 CSI pairs."""

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
            if (not isinstance(csi, list) or len(csi) != 128 or
                    not all(type(value) is int and -128 <= value <= 127 for value in csi)):
                raise ValueError(f"{location}: expected 128 signed CSI values (64 imag/real pairs)")
            samples.append(csi)
    if not samples:
        raise ValueError(f"{path}: no CSI recordings found")
    # Shape: measurements x 64 pairs x [imaginary, real].
    return np.asarray(samples, dtype=float).reshape(-1, 64, 2)


def plot_heatmaps(path, pairs, output_dir, bins, show):
    bound = max(1, float(np.abs(pairs).max())) * 1.05
    extent = (-bound, bound, -bound, bound)
    fig, axes = plt.subplots(8, 8, figsize=(24, 24), sharex=True, sharey=True,
                             layout="constrained")
    plots = []
    for pair_index, ax in enumerate(axes.flat):
        imag = pairs[:, pair_index, 0]
        real = pairs[:, pair_index, 1]
        plot = ax.hexbin(real, imag, gridsize=bins, extent=extent, mincnt=1,
                         cmap="viridis", norm=LogNorm(vmin=1))
        plots.append(plot)
        ax.set_title(f"Pair {pair_index}", fontsize=10)
        ax.set_xlim(-bound, bound)
        ax.set_ylim(-bound, bound)
        ax.set_aspect("equal", adjustable="box")
        ax.axhline(0, color="gray", linewidth=0.4, alpha=0.4)
        ax.axvline(0, color="gray", linewidth=0.4, alpha=0.4)
        ax.tick_params(labelsize=7)

    # Use one density scale and identical axes for all 64 panels.
    max_bin = max(2, max(float(plot.get_array().max()) for plot in plots))
    norm = LogNorm(vmin=1, vmax=max_bin)
    for plot in plots:
        plot.set_norm(norm)
    fig.colorbar(plots[0], ax=list(axes.flat), shrink=0.6,
                 label="Measurement count per hexagon (log scale)")
    fig.supxlabel("Real")
    fig.supylabel("Imaginary")
    fig.suptitle(f"{path.stem} — all 64 CSI pairs — {len(pairs)} measurements", fontsize=18)
    output_path = output_dir / f"{path.stem}_all_64_heatmaps.png"
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
