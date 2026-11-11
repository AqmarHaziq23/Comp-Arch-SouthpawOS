#!/usr/bin/env python3
# Modified by: Yue Ming (Team P1D) – trains and tests the wrist boards' punch classifier (original file).
"""Train one decision tree per hand on the recordings, and test it on files it hasn't seen.

    python training/tools/train_classifier.py                 # both hands, depth 3
    python training/tools/train_classifier.py --hand L --depth 3
    python training/tools/train_classifier.py --sweep         # also compare depths 1-6

Punches are cut and described by segment_punches.py, so the features are exactly the
integers the board computes. Labels come from the file names:

    jab -> J, cross -> C, hook -> H, uppercut -> U, idle / offhand -> N (not a punch)

One model per hand (orthodox stance): the left board chooses J/H/U/N, the right C/H/U/N.

Testing is leave-one-file-out: each file is predicted by a tree trained on all the
OTHER files, so punches from the same 20 s recording never sit on both sides. That is
closer to how the board meets new punches than a random split of punches would be.
"""
import argparse
import collections
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import segment_punches as sp  # noqa: E402

LABEL_CODE = {"jab": "J", "cross": "C", "hook": "H", "uppercut": "U", "idle": "N", "offhand": "N"}
CLASSES = {"L": ["J", "H", "U", "N"], "R": ["C", "H", "U", "N"]}
CLASS_NAME = {"J": "jab", "C": "cross", "H": "hook", "U": "uppercut", "N": "not a punch"}

# Every value the board has in PunchFeatures (features.cpp), as integers.
FEATURES = ["peak_counts", "dom_axis", "dom_sign", "burst_ms",
            "pre_x", "pre_y", "pre_z", "post_x", "post_y", "post_z", "tilt_change",
            "max_x", "max_y", "max_z", "min_x", "min_y", "min_z",
            "order_x", "order_y", "order_z", "win_peak_counts"]
AXIS_INDEX = {"x": 0, "y": 1, "z": 2}           # dom_axis as the board stores it
# Only values that describe the punch itself. pre_* / post_* are gravity just before and after,
# i.e. how the arm was held: they work well on one person but may not carry over to other guards.
WINDOW_FEATURES = ["burst_ms", "max_x", "max_y", "max_z", "min_x", "min_y", "min_z",
                   "order_x", "order_y", "order_z", "win_peak_counts"]
FEATURE_SETS = {"all": FEATURES, "window": WINDOW_FEATURES}


def load_punches(c):
    """Every punch in training/data/raw/ as (features, code, file, person, hand)."""
    rows, skipped = [], collections.Counter()
    for path in sorted(sp.RAW_DIR.glob("*.csv")):
        meta = sp.parse_name(path)
        code = LABEL_CODE.get(meta["label"])
        if code is None:
            skipped[meta["label"]] += 1
            continue
        _, punches, _ = sp.process(path, c)
        for f in punches or []:
            vec = [AXIS_INDEX[f[k]] if k == "dom_axis" else int(f[k]) for k in FEATURES]
            rows.append((vec, code, path.name, meta["person"], meta["hand"]))
    for label, n in skipped.items():
        print(f"(skipped {n} file(s) labelled '{label}': not a known label)")
    return rows


def make_tree(depth):
    from sklearn.tree import DecisionTreeClassifier
    # 'balanced' weights each class by 1/its count, so the few not-a-punch examples still count.
    return DecisionTreeClassifier(max_depth=depth, min_samples_leaf=2, class_weight="balanced", random_state=0)


def leave_one_file_out(X, y, files, depth):
    """Predictions for every punch, each from a tree that never saw that punch's file."""
    pred = [None] * len(y)
    for held in sorted(set(files)):
        train = [i for i, f in enumerate(files) if f != held]
        test = [i for i, f in enumerate(files) if f == held]
        tree = make_tree(depth).fit([X[i] for i in train], [y[i] for i in train])
        for i, p in zip(test, tree.predict([X[i] for i in test])):
            pred[i] = p
    return pred


def report(hand, y, pred, people, classes):
    n = len(y)
    right = sum(a == b for a, b in zip(y, pred))
    print(f"\n  tested on unseen files: {right}/{n} correct ({100 * right / n:.0f}%)")
    print("\n  per type        tested  correct   recall  precision")
    for cl in classes:
        tp = sum(a == cl and b == cl for a, b in zip(y, pred))
        actual = sum(a == cl for a in y)
        called = sum(b == cl for b in pred)
        rec = f"{100 * tp / actual:.0f}%" if actual else "-"
        prec = f"{100 * tp / called:.0f}%" if called else "-"
        print(f"  {CLASS_NAME[cl]:<14} {actual:>7}  {tp:>7}  {rec:>7}  {prec:>9}")
    print("\n  confusion (rows = what was thrown, columns = what the tree said)")
    print("  " + " " * 14 + "".join(f"{cl:>6}" for cl in classes))
    for a in classes:
        print(f"  {CLASS_NAME[a]:<14}" + "".join(f"{sum(t == a and p == b for t, p in zip(y, pred)):>6}" for b in classes))
    if len(set(people)) > 1:
        print("\n  per person")
        for person in sorted(set(people)):
            idx = [i for i, p in enumerate(people) if p == person]
            ok = sum(y[i] == pred[i] for i in idx)
            print(f"  {person:<14} {ok}/{len(idx)} ({100 * ok / len(idx):.0f}%)")


def main():
    ap = argparse.ArgumentParser(description="Train and test the per-hand punch classifier.")
    ap.add_argument("--hand", choices=["L", "R"], help="only this hand (default: both)")
    ap.add_argument("--depth", type=int, default=3, help="maximum tree depth (default 3)")
    ap.add_argument("--sweep", action="store_true", help="also test depths 1-6")
    ap.add_argument("--features", choices=sorted(FEATURE_SETS), default="all",
                    help="all = every board feature (default); window = only the punch's own shape")
    ap.add_argument("--show-tree", action="store_true", help="print the tree trained on all files")
    args = ap.parse_args()

    c = sp.load_config(sp.CONFIG_H, [])
    rows = load_punches(c)
    for hand in [args.hand] if args.hand else ["L", "R"]:
        mine = [r for r in rows if r[4] == hand]
        names = FEATURE_SETS[args.features]
        cols = [FEATURES.index(n) for n in names]
        X = [[r[0][i] for i in cols] for r in mine]
        y = [r[1] for r in mine]
        files = [r[2] for r in mine]
        people = [r[3] for r in mine]
        classes = CLASSES[hand]
        wrong = sorted({cl for cl in y if cl not in classes})
        if wrong:
            sys.exit(f"{hand} hand has punches labelled {wrong}, which that hand can't throw (orthodox). Check the file names.")
        counts = collections.Counter(y)
        print(f"\n=== {hand} hand: {len(y)} punches from {len(set(files))} files, {len(set(people))} person(s): "
              + ", ".join(f"{CLASS_NAME[cl]} {counts[cl]}" for cl in classes))
        if args.sweep:
            print("  depth  correct on unseen files")
            for d in range(1, 7):
                pred = leave_one_file_out(X, y, files, d)
                ok = sum(a == b for a, b in zip(y, pred))
                print(f"  {d:>5}  {ok}/{len(y)} ({100 * ok / len(y):.0f}%)")
        print(f"\n  depth {args.depth}, features: {args.features}")
        report(hand, y, leave_one_file_out(X, y, files, args.depth), people, classes)

        tree = make_tree(args.depth).fit(X, y)
        used = sorted(((imp, name) for name, imp in zip(names, tree.feature_importances_) if imp > 0), reverse=True)
        print("\n  features the tree uses (share of the decisions): "
              + ", ".join(f"{name} {100 * imp:.0f}%" for imp, name in used))
        print(f"  tree trained on all {len(set(files))} files: {tree.tree_.node_count} nodes, {tree.get_n_leaves()} leaves")
        if args.show_tree:
            from sklearn.tree import export_text
            print(export_text(tree, feature_names=names, decimals=1))


if __name__ == "__main__":
    main()
