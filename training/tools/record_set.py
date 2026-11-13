#!/usr/bin/env python3
# Modified by: Yue Ming (Team P1D) – records a person's whole training set for one wrist in one go (original file).
"""Record a full training set for one wrist, file after file, with beeps instead of a countdown.

    python training/tools/record_set.py --hand R --person ym
    python training/tools/record_set.py --hand L --person ys --port COM5
    python training/tools/record_set.py --hand R --person ym --only cross,hook

1. Checks the board is mounted the standard way (palm side, screen out, USB port towards the
   elbow, edges along the forearm) from gravity in two still poses.
2. Walks through the set: for each file it says what to do, waits for Enter, beeps three times
   (get ready), then a long beep (go). Two beeps = stop. The person punching never needs to see
   the laptop.
3. After each file it cuts the punches exactly like the board does and says how many it found,
   so a bad take can be redone straight away (r).

Files land in training/data/raw/ with the usual names (<person>_<hand>_<label>_<time>.csv).
A redone file is moved to training/data/raw/discarded/, which training ignores.
"""
import argparse
import pathlib
import statistics
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import log_accel  # noqa: E402
import segment_punches as sp  # noqa: E402

PUNCHES = {"L": ["jab", "hook", "uppercut"], "R": ["cross", "hook", "uppercut"]}
NOT_PUNCH = ["offhand", "pullback", "pullback", "strap", "idle"]
FILES_PER_PUNCH = 3

WHAT_TO_DO = {
    "jab": "About 10 full-speed JABS. Back to guard for 1-2 s between each.",
    "cross": "About 10 full-speed CROSSES. Back to guard for 1-2 s between each.",
    "hook": "About 10 full-speed HOOKS with this arm. Back to guard for 1-2 s between each.",
    "uppercut": "About 10 full-speed UPPERCUTS with this arm. Back to guard for 1-2 s between each.",
    "offhand": "Keep THIS arm in guard. Punch with the OTHER hand only: about 10 full-speed punches, any type.",
    "pullback": "THIS arm, no real punches: pull the arm back, feint (a small twitch forward that stops short), "
                "reset the guard. About 10 moves.",
    "strap": "Fiddle like you're putting the board on: adjust the strap, shake the hand out, roll the wrist, "
             "drop and raise the guard, tap the board.",
    "idle": "Guard up, bounce on your feet, move around, breathe. No punches.",
}

# Gravity on a correctly mounted board (palm side, screen out, USB port towards the elbow).
POSES = [
    ("Elbow on the table, forearm pointing straight UP, fist closed, palm facing you.", "x", +1),
    ("Forearm flat on the table, PALM UP, fist closed.", "z", -1),
]
MAX_TWIST_G = 0.25        # sideways gravity allowed in the forearm-up pose (about 15 degrees)


def beep(freq, ms):
    try:
        import winsound
        winsound.Beep(freq, ms)
    except (ImportError, RuntimeError):
        print("\a", end="", flush=True)
        time.sleep(ms / 1000)


def read_gravity(ser, seconds=3.0):
    """Average x, y, z in g over a few seconds of the board's stream."""
    ser.reset_input_buffer()
    xs, ys, zs = [], [], []
    end = time.time() + seconds
    while time.time() < end:
        parts = ser.readline().decode("ascii", errors="ignore").strip().split(",")
        if len(parts) != 4:
            continue
        try:
            _, x, y, z = (int(v) for v in parts)
        except ValueError:
            continue
        xs.append(x); ys.append(y); zs.append(z)
    if len(xs) < 50:
        sys.exit("Hardly any samples from the board. Is southpaw_wrist (or the P0 logger) on it, "
                 "and the Serial Monitor closed?")
    return [statistics.mean(v) / log_accel.COUNTS_PER_G for v in (xs, ys, zs)]


def check_mount(ser):
    """True if the board sits the standard way; explains what's wrong if not."""
    ok = True
    for i, (pose, axis, sign) in enumerate(POSES, 1):
        input(f"\nMount check {i}/{len(POSES)}: {pose}\n  Hold still and press Enter... ")
        g = dict(zip("xyz", read_gravity(ser)))
        print(f"  gravity x={g['x']:+.2f} y={g['y']:+.2f} z={g['z']:+.2f} g")
        if g[axis] * sign < 0.8:
            ok = False
            if axis == "x" and g["x"] < -0.8:
                print("  -> The board is the wrong way round: the USB port must point towards the ELBOW.")
            elif axis == "z" and g["z"] > 0.8:
                print("  -> The screen faces the wrong way: the board must be on the PALM side, screen facing out.")
            else:
                print(f"  -> Expected about {sign:+d} g on {axis}. Check the board's position and that the arm "
                      "is held as described.")
        if axis == "x" and abs(g["y"]) > MAX_TWIST_G:
            ok = False
            print(f"  -> The board is twisted about {abs(g['y']) * 57:.0f} degrees on the arm. Turn it so its "
                  "edges run along the forearm.")
    print("\nMount OK." if ok else "\nFix the mounting and run the check again (or c to carry on anyway).")
    return ok


def plan(hand, only):
    items = [label for label in PUNCHES[hand] for _ in range(FILES_PER_PUNCH)] + NOT_PUNCH
    return [label for label in items if not only or label in only]


def summarise(path, label, c):
    """How many punches the board would have detected in the file."""
    _, punches, info = sp.process(path, c)
    peaks = " ".join(str(p["peak_g"]) for p in punches)
    found = len(punches)
    if label in WHAT_TO_DO and label not in NOT_PUNCH:
        note = "  <- fewer than expected, redo? (r)" if found < 6 else ""
        print(f"  {found} punches detected (peak g: {peaks or '-'}){note}")
    else:
        print(f"  {found} triggers (these teach the classifier what NOT to count) peak g: {peaks or '-'}")
    if info and info["gaps"]:
        print(f"  warning: {info['gaps']} timing gaps in the file")


def main():
    ap = argparse.ArgumentParser(description="Record a whole training set for one wrist.")
    ap.add_argument("--hand", required=True, choices=["L", "R"], help="which wrist the board is on")
    ap.add_argument("--person", required=True, help="short name of the person punching, e.g. ym")
    ap.add_argument("--port", help="serial port (auto-detected if left out)")
    ap.add_argument("--seconds", type=float, default=20.0, help="length of each file (default 20)")
    ap.add_argument("--only", help="only these labels, comma separated (e.g. cross,hook)")
    ap.add_argument("--skip-check", action="store_true", help="skip the mount check")
    args = ap.parse_args()

    only = set(args.only.split(",")) if args.only else None
    unknown = (only or set()) - set(WHAT_TO_DO)
    if unknown:
        ap.error(f"unknown label(s): {', '.join(sorted(unknown))}")
    items = plan(args.hand, only)
    c = sp.load_config(sp.CONFIG_H, [])
    port = args.port or log_accel.find_port()
    discarded = sp.RAW_DIR / "discarded"
    print(f"{len(items)} files of {args.seconds:g} s for {args.person}, {args.hand} wrist, on {port}.")
    print("Beeps: 3 short = get ready, 1 long = GO, 2 short = stop.")

    with log_accel.serial.Serial(port, log_accel.BAUD, timeout=1) as ser:
        time.sleep(0.5)
        if not args.skip_check:
            while not check_mount(ser):
                if input("  Enter = check again, c = carry on anyway: ").strip().lower() == "c":
                    break

        i, last = 0, None
        while i < len(items):
            label = items[i]
            print(f"\n[{i + 1}/{len(items)}] {args.hand} wrist, {label.upper()}")
            print(f"  {WHAT_TO_DO[label]}")
            cmd = input("  Enter = record, s = skip, r = redo the previous file, q = quit: ").strip().lower()
            if cmd == "q":
                break
            if cmd == "s":
                i += 1
                continue
            if cmd == "r" and last:
                discarded.mkdir(exist_ok=True)
                last[0].rename(discarded / last[0].name)
                print(f"  moved {last[0].name} to discarded/, recording it again")
                i, label = last[1], items[last[1]]
                print(f"  {WHAT_TO_DO[label]}")

            stamp = time.strftime("%Y%m%d-%H%M%S")
            path = sp.RAW_DIR / f"{args.person}_{args.hand}_{label}_{stamp}.csv"
            for _ in range(3):
                beep(880, 150)
                time.sleep(0.35)
            ser.reset_input_buffer()          # drop what queued up while waiting for Enter
            beep(1320, 500)
            n, bad, rate = log_accel.record(ser, path, args.seconds)
            beep(660, 150)
            time.sleep(0.1)
            beep(660, 150)
            if rate is None:
                print(f"  only {n} samples received - check the cable and that the Serial Monitor is closed")
                path.unlink(missing_ok=True)
                continue
            print(f"  saved {path.name} ({rate:.0f} samples/s, {bad} bad lines)")
            summarise(path, label, c)
            last = (path, i)
            i += 1
    print("\nDone. Next: python training/tools/train_classifier.py")


if __name__ == "__main__":
    main()
