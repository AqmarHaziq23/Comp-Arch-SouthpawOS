#!/usr/bin/env python3
# Modified by: Yue Ming (Team P1D) – plots log_accel.py CSV recordings (x, y, z in g against time).
"""Plot one or more CSV files from log_accel.py (x, y, z in g against time).

Usage:
    python plot_accel.py ../data/raw/ym_L_jab_20261108-143012.csv
    python plot_accel.py ../data/raw/ym_L_*.csv         # one panel per file

Saves a PNG next to each CSV and opens a window.
Team P1D - ICT1011 TinyCircuit project.
"""
import csv
import glob
import pathlib
import sys

try:
    import matplotlib.pyplot as plt
except ImportError:
    sys.exit("matplotlib is missing. Install it with:  pip install matplotlib")


def load(path):
    t, x, y, z = [], [], [], []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            t.append(int(row["board_ms"]))
            x.append(float(row["x_g"]))
            y.append(float(row["y_g"]))
            z.append(float(row["z_g"]))
    t0 = t[0] if t else 0
    return [(v - t0) / 1000.0 for v in t], x, y, z


def main():
    files = []
    for arg in sys.argv[1:]:
        files += glob.glob(arg) or [arg]
    missing = [f for f in files if not pathlib.Path(f).exists()]
    if not files or missing:
        sys.exit(f"No CSV found{': ' + ', '.join(missing) if missing else ''}. "
                 "Example: python plot_accel.py ../data/raw/ym_L_jab_*.csv")

    fig, axes = plt.subplots(len(files), 1, figsize=(12, 3.2 * len(files)), squeeze=False, sharey=True)
    for ax, path in zip(axes[:, 0], files):
        t, x, y, z = load(path)
        ax.plot(t, x, label="x", linewidth=1)
        ax.plot(t, y, label="y", linewidth=1)
        ax.plot(t, z, label="z", linewidth=1)
        ax.axhline(16, color="grey", linestyle=":", linewidth=0.8)
        ax.axhline(-16, color="grey", linestyle=":", linewidth=0.8)
        ax.set_title(pathlib.Path(path).name, fontsize=10)
        ax.set_ylabel("acceleration (g)")
        ax.grid(alpha=0.3)
        ax.legend(loc="upper right")
    axes[-1, 0].set_xlabel("time (s)")
    fig.tight_layout()
    out = pathlib.Path(files[0]).with_suffix(".png") if len(files) == 1 else pathlib.Path(files[0]).parent / "comparison.png"
    fig.savefig(out, dpi=120)
    print(f"Saved plot to {out}  (dotted lines = the +/-16 g sensor limit)")
    plt.show()


if __name__ == "__main__":
    main()
