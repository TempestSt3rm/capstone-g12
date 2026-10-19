"""Compare CSI spread with real on x and imaginary on y."""
import argparse
import csv
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.colors import LogNorm
import numpy as np

# Edit these settings, then run this file without arguments.
# Relative paths are resolved beside this script.
CSV_FILES = ["../Data/x0y0.csv", "../Data/x0y1.csv",
             "../Data/x1y0.csv", "../Data/x1y1.csv"]
MODE = "both"  # "individual", "combined", or "both"
CSI_PAIR_INDEX = None  # None pools all pairs; e.g. 10 compares pair 10 only.
MAX_SCATTER_POINTS_PER_FILE = 12000  # Combined view only; densities use all values.
SHOW_PLOTS = True
OUTPUT_DIRECTORY = "plots"


def resolve_path(filename):
    path = Path(filename)
    return path if path.is_absolute() else Path(__file__).resolve().parent / path


def load_pairs(path, pair_index=None):
    with path.open(newline="", encoding="utf-8-sig") as handle:
        header = next(csv.reader(handle), [])
    if not header or len(header) % 2:
        raise ValueError(f"{path.name}: expected alternating img/real columns")
    expected = [name for i in range(len(header) // 2)
                for name in (f"img_{i}", f"real_{i}")]
    if header != expected:
        raise ValueError(f"{path.name}: unexpected CSI column order")
    values = np.loadtxt(path, delimiter=",", skiprows=1, ndmin=2)
    if not values.size or values.shape[1] != len(header) or not np.isfinite(values).all():
        raise ValueError(f"{path.name}: empty, incomplete, or nonfinite CSI data")
    pairs = values.reshape(values.shape[0], -1, 2)
    if pair_index is not None:
        if not 0 <= pair_index < pairs.shape[1]:
            raise ValueError(f"{path.name}: pair index must be 0..{pairs.shape[1] - 1}")
        pairs = pairs[:, pair_index:pair_index + 1, :]
    # CSV order is imaginary/real; return x=real, y=imaginary.
    return pairs[:, :, 1].ravel(), pairs[:, :, 0].ravel(), values.shape[0]


def style_axes(ax, extent):
    ax.set_xlabel("Real")
    ax.set_ylabel("Imaginary")
    ax.set_xlim(extent[:2])
    ax.set_ylim(extent[2:])
    ax.set_aspect("equal", adjustable="box")
    ax.axhline(0, color="gray", linewidth=0.6, alpha=0.4)
    ax.axvline(0, color="gray", linewidth=0.6, alpha=0.4)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("individual", "combined", "both"), default=MODE)
    parser.add_argument("--pair", type=int, default=CSI_PAIR_INDEX)
    args = parser.parse_args(argv)
    if not CSV_FILES:
        raise ValueError("CSV_FILES must include at least one recording")
    if MAX_SCATTER_POINTS_PER_FILE < 1:
        raise ValueError("MAX_SCATTER_POINTS_PER_FILE must be positive")
    datasets = [(resolve_path(filename).stem, *load_pairs(resolve_path(filename), args.pair))
                for filename in CSV_FILES]
    # Identical limits and equal axis scaling allow fair visual comparisons.
    bound = max(1, max(max(np.abs(x).max(), np.abs(y).max())
                       for _, x, y, _ in datasets)) * 1.05
    extent = (-bound, bound, -bound, bound)
    selection = "All CSI pairs pooled" if args.pair is None else f"CSI pair {args.pair}"
    suffix = "all_pairs" if args.pair is None else f"pair_{args.pair}"
    output = resolve_path(OUTPUT_DIRECTORY)
    output.mkdir(parents=True, exist_ok=True)
    colors = plt.get_cmap("tab10")
    figures = []

    print(f"{selection}; standard deviations below use every selected value.")
    for name, real, img, count in datasets:
        print(f"{name}: {count} measurements, {len(real):,} pairs; "
              f"real mean={real.mean():.2f}, std={real.std():.2f}; "
              f"img mean={img.mean():.2f}, std={img.std():.2f}; "
              f"RMS magnitude={np.sqrt(np.mean(real**2 + img**2)):.2f}")

    if args.mode in ("individual", "both"):
        columns = min(2, len(datasets))
        rows = (len(datasets) + columns - 1) // columns
        fig, axes = plt.subplots(rows, columns, figsize=(6 * columns, 5 * rows),
                                 squeeze=False, layout="constrained")
        hexplots = []
        for ax, (name, real, img, count) in zip(axes.flat, datasets):
            plot = ax.hexbin(real, img, gridsize=45, extent=extent, mincnt=1,
                             cmap="viridis", norm=LogNorm(vmin=1))
            hexplots.append(plot)
            ax.set_title(f"{name} — {count} measurements")
            style_axes(ax, extent)
        # Same density scale for each panel, even if recording lengths differ.
        max_bin = max(2, max(float(plot.get_array().max()) for plot in hexplots))
        for plot in hexplots:
            plot.set_norm(LogNorm(vmin=1, vmax=max_bin))
        for ax in list(axes.flat)[len(datasets):]:
            ax.set_visible(False)
        fig.colorbar(hexplots[0], ax=list(axes.flat)[:len(datasets)],
                     label="Pair count per hexagon (log scale)", shrink=0.8)
        fig.suptitle(f"CSI spread per location — {selection}")
        path = output / f"individual_{suffix}.png"
        fig.savefig(path, dpi=160)
        print(f"Saved {path}")
        figures.append(fig)

    if args.mode in ("combined", "both"):
        fig, ax = plt.subplots(figsize=(9, 8), layout="constrained")
        rng = np.random.default_rng(42)
        for i, (name, real, img, _) in enumerate(datasets):
            indices = rng.choice(len(real), min(len(real), MAX_SCATTER_POINTS_PER_FILE),
                                 replace=False)
            color = colors(i % 10)
            ax.scatter(real[indices], img[indices], s=5, alpha=0.2, color=color,
                       edgecolors="none", label=name, rasterized=True)
        style_axes(ax, extent)
        ax.set_title(f"All locations — {selection}")
        legend = ax.legend(title="Recording", loc="upper right")
        for handle in legend.legend_handles:
            handle.set_alpha(1)
        path = output / f"combined_{suffix}.png"
        fig.savefig(path, dpi=160)
        print(f"Saved {path}")
        figures.append(fig)

    if args.pair is None:
        print("Pooling hides pair identity. Set CSI_PAIR_INDEX to compare the same pair "
              "across recordings. Overlap here does not rule out localization.")
    print("Visual separation in these recordings is exploratory; it does not establish "
          "accuracy on independent visits.")
    if SHOW_PLOTS:
        plt.show()
    for fig in figures:
        plt.close(fig)


if __name__ == "__main__":
    main()
