"""Pick one CSI pair and compare its real/imaginary values at all four points."""
import plot_csi

# Edit this zero-based index: 10 selects img_10 and real_10 (the 11th pair).
# The current 192-pair recordings accept indices 0 through 191.
PAIR_INDEX = 10
MODE = "both"  # "combined", "individual" (four panels), or "both"
SHOW_PLOTS = True
CSV_FILES = ["../Data/x0y0.csv", "../Data/x0y1.csv",
             "../Data/x1y0.csv", "../Data/x1y1.csv"]
OUTPUT_DIRECTORY = "plots"


def main():
    if type(PAIR_INDEX) is not int or PAIR_INDEX < 0:
        raise ValueError("PAIR_INDEX must be a nonnegative integer")
    plot_csi.CSV_FILES = CSV_FILES
    plot_csi.SHOW_PLOTS = SHOW_PLOTS
    plot_csi.OUTPUT_DIRECTORY = OUTPUT_DIRECTORY
    plot_csi.main(["--pair", str(PAIR_INDEX), "--mode", MODE])


if __name__ == "__main__":
    main()
