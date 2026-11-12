#!/usr/bin/env python3
# Modified by: Yue Ming (Team P1D) – offline copy of the wrist board's punch detector and features (original file).
"""Cut recordings into punches with the SAME detector and features as the wrist board.

    python training/tools/segment_punches.py                       # every CSV in training/data/raw/
    python training/tools/segment_punches.py training/data/raw/ym_L_jab_*.csv
    python training/tools/segment_punches.py --set DETECT_THRESHOLD_COUNTS=128   # try a value without re-flashing

For each file it prints how many punches it found, saves <file>_punches.png (one panel
per punch, next to the CSV) and writes all punches to training/data/features.csv.

The constants are read from firmware/wrist/southpaw_wrist/config.h, and the steps below
mirror detector.cpp and features.cpp line for line (integer maths, C-style rounding),
so a number here is the number the board computes. If you change one, change the other.

Input columns: board_ms,x_raw,y_raw,z_raw[,x_g,y_g,z_g] (as written by log_accel.py).
File names: <person>_<hand>_<label>_<time>.csv gives the person, hand and label columns.
"""
import argparse
import csv
import glob
import math
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
CONFIG_H = ROOT / "firmware" / "wrist" / "southpaw_wrist" / "config.h"
RAW_DIR = ROOT / "training" / "data" / "raw"
FEATURES_CSV = ROOT / "training" / "data" / "features.csv"

NEEDED = ["SAMPLE_RATE_HZ", "COUNTS_PER_G", "DETECT_THRESHOLD_COUNTS", "PEAK_SEARCH_MS", "COOLDOWN_MS",
          "WINDOW_PRE_MS", "WINDOW_POST_MS", "BURST_THRESHOLD_COUNTS", "GRAVITY_AVG_MS", "MIN_BURST_MS"]
NAME_RE = re.compile(r"^(?P<person>[^_]+)_(?P<hand>[LR])_(?P<label>[^_]+)_(?P<stamp>.+)$")
AXES = "xyz"
FEATURE_COLUMNS = ["file", "person", "hand", "label", "punch", "row", "peak_ms", "peak_counts", "peak_g",
                   "dom_axis", "dom_sign", "burst_ms", "pre_x", "pre_y", "pre_z", "post_x", "post_y", "post_z",
                   "tilt_change", "max_x", "max_y", "max_z", "min_x", "min_y", "min_z",
                   "order_x", "order_y", "order_z", "win_peak_counts"]


# ---------------------------------------------------------------- constants from config.h

def load_config(path, overrides):
    text = path.read_text(encoding="utf-8")
    found = {m.group(1): int(m.group(2))
             for m in re.finditer(r"constexpr\s+\w+\s+(\w+)\s*=\s*(\d+)\s*;", text)}
    missing = [n for n in NEEDED if n not in found]
    if missing:
        sys.exit(f"{path} is missing {', '.join(missing)} (keep them as `constexpr <type> NAME = <integer>;`)")
    c = {n: found[n] for n in NEEDED}
    for item in overrides:
        name, _, value = item.partition("=")
        if name not in c or not value.lstrip("-").isdigit():
            sys.exit(f"--set needs NAME=integer with NAME one of {', '.join(NEEDED)}; got {item!r}")
        c[name] = int(value)

    # Derived exactly as in config.h (integer division).
    period = 1000 // c["SAMPLE_RATE_HZ"]
    c["SAMPLE_PERIOD_MS"] = period
    c["PEAK_SEARCH_SAMPLES"] = c["PEAK_SEARCH_MS"] // period
    c["COOLDOWN_SAMPLES"] = c["COOLDOWN_MS"] // period
    c["WINDOW_PRE_SAMPLES"] = c["WINDOW_PRE_MS"] // period
    c["WINDOW_POST_SAMPLES"] = c["WINDOW_POST_MS"] // period
    c["WINDOW_LEN"] = c["WINDOW_PRE_SAMPLES"] + 1 + c["WINDOW_POST_SAMPLES"]
    c["GRAVITY_AVG_SAMPLES"] = c["GRAVITY_AVG_MS"] // period
    c["DETECT_THRESHOLD_SQ"] = c["DETECT_THRESHOLD_COUNTS"] ** 2
    c["BURST_THRESHOLD_SQ"] = c["BURST_THRESHOLD_COUNTS"] ** 2

    # The same checks as config.h's static_asserts.
    checks = [
        (1000 % c["SAMPLE_RATE_HZ"] == 0, "sample period must be a whole number of ms"),
        (c["PEAK_SEARCH_SAMPLES"] >= 1, "peak search needs at least one sample"),
        (c["WINDOW_POST_MS"] > c["PEAK_SEARCH_MS"], "the peak must be found before the window ends"),
        (c["COOLDOWN_MS"] > c["WINDOW_POST_MS"], "COOLDOWN_MS must be longer than WINDOW_POST_MS"),
        (c["BURST_THRESHOLD_COUNTS"] <= c["DETECT_THRESHOLD_COUNTS"], "the peak must be inside the burst"),
        (c["GRAVITY_AVG_SAMPLES"] >= 1 and 2 * c["GRAVITY_AVG_SAMPLES"] <= c["WINDOW_LEN"],
         "gravity averages must fit in the window"),
    ]
    for ok, why in checks:
        if not ok:
            sys.exit(f"Invalid settings: {why}")
    return c


# ---------------------------------------------------------------- mirrors of the firmware

def c_div(a, b):
    """Integer division rounding towards zero, like C (Python's // rounds down)."""
    q = abs(a) // abs(b)
    return q if (a >= 0) == (b >= 0) else -q


def mag2(s):
    return s[1] * s[1] + s[2] * s[2] + s[3] * s[3]


def detect(samples, c):
    """detector.cpp: returns (peak indices of complete windows, dropped at start, pending at end)."""
    state, prev_above = "IDLE", False
    trigger = peak = peak_mag2 = cooldown_until = 0
    peaks, dropped = [], 0
    for i, s in enumerate(samples):
        m2 = mag2(s)
        above = m2 >= c["DETECT_THRESHOLD_SQ"]
        if state == "IDLE":
            if above and not prev_above and i >= cooldown_until:
                trigger, peak, peak_mag2, state = i, i, m2, "SEARCH"
        elif state == "SEARCH":
            if m2 > peak_mag2:                      # strictly greater: first of equal peaks wins
                peak, peak_mag2 = i, m2
        if state == "SEARCH" and i - trigger + 1 >= c["PEAK_SEARCH_SAMPLES"]:
            state, cooldown_until = "POST", peak + c["COOLDOWN_SAMPLES"]
        if state == "POST" and i == peak + c["WINDOW_POST_SAMPLES"]:
            state = "IDLE"
            if peak >= c["WINDOW_PRE_SAMPLES"]:
                peaks.append(peak)
            else:
                dropped += 1
        prev_above = above
    pending = 1 if state != "IDLE" else 0             # the board would finish it after the file ends
    return peaks, dropped, pending


def features(w, c):
    """features.cpp: w is WINDOW_LEN samples (ms, x, y, z) with the peak at WINDOW_PRE_SAMPLES."""
    pre, n_avg = c["WINDOW_PRE_SAMPLES"], c["GRAVITY_AVG_SAMPLES"]
    p = w[pre]
    peak_counts = math.isqrt(mag2(p))
    v = p[1:]
    best = 0
    for a in (1, 2):                                 # ties go to the lower axis
        if abs(v[a]) > abs(v[best]):
            best = a
    run = 1
    j = pre - 1
    while j >= 0 and mag2(w[j]) >= c["BURST_THRESHOLD_SQ"]:
        run, j = run + 1, j - 1
    j = pre + 1
    while j < len(w) and mag2(w[j]) >= c["BURST_THRESHOLD_SQ"]:
        run, j = run + 1, j + 1

    def avg(start):
        return [c_div(sum(s[k] for s in w[start:start + n_avg]), n_avg) for k in (1, 2, 3)]

    before, after = avg(0), avg(len(w) - n_avg)
    d = [a - b for a, b in zip(after, before)]

    # Whole window: per-axis max and min (the first one wins a tie), max-to-min time, largest total.
    whole = {}
    for k, a in enumerate(AXES, 1):
        vals = [s[k] for s in w]
        hi, lo = max(vals), min(vals)
        whole["max_" + a], whole["min_" + a] = hi, lo
        whole["order_" + a] = (vals.index(lo) - vals.index(hi)) * c["SAMPLE_PERIOD_MS"]
    whole["win_peak_counts"] = math.isqrt(max(mag2(s) for s in w))
    return {
        "peak_ms": p[0], "peak_counts": peak_counts,
        "peak_g": (peak_counts + c["COUNTS_PER_G"] // 2) // c["COUNTS_PER_G"],
        "dom_axis": AXES[best], "dom_sign": -1 if v[best] < 0 else 1,
        "burst_ms": run * c["SAMPLE_PERIOD_MS"],
        "pre_x": before[0], "pre_y": before[1], "pre_z": before[2],
        "post_x": after[0], "post_y": after[1], "post_z": after[2],
        "tilt_change": math.isqrt(d[0] * d[0] + d[1] * d[1] + d[2] * d[2]),
        **whole,
    }


# ---------------------------------------------------------------- files

RAW_COLUMNS = ("board_ms", "x_raw", "y_raw", "z_raw")


def load_csv(path):
    """The recording's samples, or None if the file isn't a recording (e.g. a features.csv)."""
    rows = []
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or any(col not in reader.fieldnames for col in RAW_COLUMNS):
            return None
        for r in reader:
            rows.append((int(r["board_ms"]), int(r["x_raw"]), int(r["y_raw"]), int(r["z_raw"])))
    return rows


def parse_name(path):
    m = NAME_RE.match(pathlib.Path(path).stem)
    if not m:
        return {"person": "?", "hand": "?", "label": "unknown"}
    return {"person": m["person"], "hand": m["hand"], "label": m["label"]}


def process(path, c):
    samples = load_csv(path)
    if samples is None:
        return None, None, None
    gaps = sum(1 for a, b in zip(samples, samples[1:]) if b[0] - a[0] != c["SAMPLE_PERIOD_MS"])
    peaks, dropped, pending = detect(samples, c)
    pre, post = c["WINDOW_PRE_SAMPLES"], c["WINDOW_POST_SAMPLES"]
    punches, knocks = [], 0
    for pk in peaks:
        f = features(samples[pk - pre: pk + post + 1], c)
        if f["burst_ms"] < c["MIN_BURST_MS"]:        # a knock, not a punch (as in southpaw_wrist.ino)
            knocks += 1
            continue
        f["row"] = pk
        punches.append(f)
    return samples, punches, {"knocks": knocks, "dropped": dropped, "pending": pending, "gaps": gaps}


# ---------------------------------------------------------------- plot

COLORS = {"x": "#2a78d6", "y": "#eb6834", "z": "#1baf7a"}   # categorical slots 1-3 of the default palette
INK, INK_2, GRID, SURFACE, NEUTRAL = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb", "#8f8d87"


def plot(path, samples, punches, c, out):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    n, cols = len(punches), 5
    thr_g = c["DETECT_THRESHOLD_COUNTS"] / c["COUNTS_PER_G"]
    if n == 0:                                        # just say so, without empty axes
        fig = plt.figure(figsize=(7, 1.2), facecolor=SURFACE)
        fig.text(0.02, 0.62, pathlib.Path(path).name, fontsize=10, color=INK)
        fig.text(0.02, 0.25, f"no punches found (trigger {thr_g:g} g, {len(samples)} samples)",
                 fontsize=9, color=INK_2)
        fig.savefig(out, dpi=110, facecolor=SURFACE)
        plt.close(fig)
        return
    rows = math.ceil(n / cols)
    fig, axes = plt.subplots(rows, cols, figsize=(2.6 * cols, 2.1 * rows + 0.9), sharex=True, sharey=True,
                             squeeze=False, facecolor=SURFACE)
    pre, post, cpg = c["WINDOW_PRE_SAMPLES"], c["WINDOW_POST_SAMPLES"], c["COUNTS_PER_G"]
    t = [(k - pre) * c["SAMPLE_PERIOD_MS"] for k in range(pre + post + 1)]
    for k, ax in enumerate(axes.flat):
        ax.set_facecolor(SURFACE)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        for side in ("left", "bottom"):
            ax.spines[side].set_color(GRID)
        ax.tick_params(colors=INK_2, labelsize=7, length=2)
        ax.grid(color=GRID, linewidth=0.6)
        if k >= n:
            ax.set_visible(False)
            continue
        f = punches[k]
        w = samples[f["row"] - pre: f["row"] + post + 1]
        ax.plot(t, [math.sqrt(mag2(s)) / cpg for s in w], color=NEUTRAL, linewidth=1.0, label="total")
        for i, a in enumerate(AXES):
            ax.plot(t, [s[i + 1] / cpg for s in w], color=COLORS[a], linewidth=1.2, label=a)
        ax.axhline(thr_g, color=NEUTRAL, linewidth=0.8, linestyle="--")
        ax.axvline(0, color=INK_2, linewidth=0.6, linestyle=":")
        ax.set_title(f"#{k + 1}  {f['peak_g']} g  {f['dom_sign'] > 0 and '+' or '-'}{f['dom_axis']}  "
                     f"burst {f['burst_ms']} ms  tilt {f['tilt_change']}", fontsize=7.5, color=INK, loc="left")
    for k, ax in enumerate(axes.flat):                # x labels on the lowest visible panel of each column
        if k < n and k + cols >= n:
            ax.set_xlabel("ms from peak", fontsize=7.5, color=INK_2)
            ax.xaxis.set_tick_params(labelbottom=True)
    for ax in axes[:, 0]:
        ax.set_ylabel("g", fontsize=7.5, color=INK_2)
    handles, labels = axes.flat[0].get_legend_handles_labels()
    if handles:
        fig.legend(handles, labels, loc="upper right", ncol=4, frameon=False, fontsize=8, labelcolor=INK_2)
    fig.suptitle(f"{pathlib.Path(path).name}: {n} punch{'es' if n != 1 else ''}  "
                 f"(dashed = {thr_g:g} g trigger)", x=0.01, ha="left", fontsize=10, color=INK)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    fig.savefig(out, dpi=110, facecolor=SURFACE)
    plt.close(fig)


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description="Detect punches in recordings exactly as the wrist board does.")
    ap.add_argument("files", nargs="*", help="CSV files or globs (default: every CSV in training/data/raw/)")
    ap.add_argument("--set", action="append", default=[], metavar="NAME=VALUE",
                    help="override a config.h constant for this run, e.g. --set COOLDOWN_MS=300")
    ap.add_argument("--out", default=str(FEATURES_CSV), help="features file (default training/data/features.csv)")
    ap.add_argument("--no-plots", action="store_true", help="skip the per-file punch plots")
    ap.add_argument("--config", default=str(CONFIG_H), help=argparse.SUPPRESS)
    args = ap.parse_args()

    c = load_config(pathlib.Path(args.config), args.set)
    files = []
    for pattern in args.files or [str(RAW_DIR / "*.csv")]:
        files += sorted(glob.glob(pattern)) or ([pattern] if pathlib.Path(pattern).exists() else [])
    out = pathlib.Path(args.out)
    files = [f for f in dict.fromkeys(files)
             if f.lower().endswith(".csv") and pathlib.Path(f).resolve() != out.resolve()]
    if not files:
        sys.exit("No CSV files found. Record some with log_accel.py, or pass file names.")

    print(f"threshold {c['DETECT_THRESHOLD_COUNTS']} counts ({c['DETECT_THRESHOLD_COUNTS'] / c['COUNTS_PER_G']:g} g), "
          f"cooldown {c['COOLDOWN_MS']} ms, window -{c['WINDOW_PRE_MS']}/+{c['WINDOW_POST_MS']} ms, "
          f"min burst {c['MIN_BURST_MS']} ms" + (f"   (overrides: {', '.join(args.set)})" if args.set else ""))
    print(f"{'file':<44} {'label':<9} {'punches':>7} {'knocks':>6}  notes")
    all_rows, skipped = [], 0
    for path in files:
        meta = parse_name(path)
        samples, punches, info = process(path, c)
        if samples is None:
            print(f"{pathlib.Path(path).name:<44} skipped: no {','.join(RAW_COLUMNS)} columns (not a recording)")
            skipped += 1
            continue
        notes = []
        if meta["label"] == "unknown":
            notes.append("name isn't person_hand_label_time")
        if info["dropped"]:
            notes.append(f"{info['dropped']} too close to the start")
        if info["pending"]:
            notes.append("1 cut off at the end")
        if info["gaps"]:
            notes.append(f"{info['gaps']} timing gaps (not {c['SAMPLE_PERIOD_MS']} ms)")
        print(f"{pathlib.Path(path).name:<44} {meta['label']:<9} {len(punches):>7} {info['knocks']:>6}  "
              f"{'; '.join(notes)}")
        for k, f in enumerate(punches, 1):
            all_rows.append({"file": pathlib.Path(path).name, **meta, "punch": k, **f})
        if not args.no_plots:
            plot(path, samples, punches, c, pathlib.Path(path).with_name(pathlib.Path(path).stem + "_punches.png"))

    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FEATURE_COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(all_rows)
    print(f"\n{len(all_rows)} punches from {len(files) - skipped} recording(s) -> {out}")
    if not args.no_plots:
        print("plots: <file>_punches.png next to each CSV")


if __name__ == "__main__":
    main()
