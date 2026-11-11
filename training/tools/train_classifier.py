#!/usr/bin/env python3
# Modified by: Yue Ming (Team P1D) – trains and tests the wrist boards' punch classifier (original file).
"""Train one decision tree per hand on the recordings, and test it on files it hasn't seen.

    python training/tools/train_classifier.py                 # both hands, depth 3
    python training/tools/train_classifier.py --hand L --depth 3
    python training/tools/train_classifier.py --sweep         # also compare depths 1-6
    python training/tools/train_classifier.py --export        # write the trees into the firmware

Punches are cut and described by segment_punches.py, so the features are exactly the
integers the board computes. Labels come from the file names:

    jab -> J, cross -> C, hook -> H, uppercut -> U, idle / offhand -> N (not a punch)

One model per hand (orthodox stance): the left board chooses J/H/U/N, the right C/H/U/N.

--export trains both trees on ALL files and writes them as C with m2cgen into
firmware/wrist/southpaw_wrist/classifier_model.c / .h. Re-flash the boards afterwards.

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

# Where each feature lives in the board's PunchFeatures struct.
C_FIELD = {"peak_counts": "peakMag", "dom_axis": "domAxis", "dom_sign": "domSign", "burst_ms": "burstMs",
           "pre_x": "preX", "pre_y": "preY", "pre_z": "preZ", "post_x": "postX", "post_y": "postY",
           "post_z": "postZ", "tilt_change": "tiltChange", "max_x": "maxX", "max_y": "maxY", "max_z": "maxZ",
           "min_x": "minX", "min_y": "minY", "min_z": "minZ", "order_x": "orderX", "order_y": "orderY",
           "order_z": "orderZ", "win_peak_counts": "winPeakMag"}
MODEL_C = sp.ROOT / "firmware" / "wrist" / "southpaw_wrist" / "classifier_model.c"
MODEL_H = MODEL_C.with_suffix(".h")


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


def hand_data(rows, hand, feature_set):
    """X, y, files, people for one hand, using the named feature set's columns."""
    cols = [FEATURES.index(n) for n in FEATURE_SETS[feature_set]]
    mine = [r for r in rows if r[4] == hand]
    return ([[r[0][i] for i in cols] for r in mine], [r[1] for r in mine],
            [r[2] for r in mine], [r[3] for r in mine])


def fit_hand(rows, hand, depth, feature_set):
    """The tree the board gets: trained on every file for this hand."""
    X, y, _, _ = hand_data(rows, hand, feature_set)
    return make_tree(depth).fit(X, y)


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
    return right, n


def main():
    ap = argparse.ArgumentParser(description="Train and test the per-hand punch classifier.")
    ap.add_argument("--hand", choices=["L", "R"], help="only this hand (default: both)")
    ap.add_argument("--depth", type=int, default=3, help="maximum tree depth (default 3)")
    ap.add_argument("--sweep", action="store_true", help="also test depths 1-6")
    ap.add_argument("--features", choices=sorted(FEATURE_SETS), default="all",
                    help="all = every board feature (default); window = only the punch's own shape")
    ap.add_argument("--show-tree", action="store_true", help="print the tree trained on all files")
    ap.add_argument("--export", action="store_true",
                    help="write both trees into firmware/wrist/southpaw_wrist/classifier_model.c/.h")
    args = ap.parse_args()
    if args.export and args.hand:
        ap.error("--export writes both hands; leave out --hand")

    c = sp.load_config(sp.CONFIG_H, [])
    rows = load_punches(c)
    names = FEATURE_SETS[args.features]
    trained = {}
    for hand in [args.hand] if args.hand else ["L", "R"]:
        X, y, files, people = hand_data(rows, hand, args.features)
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
        right, n = report(hand, y, leave_one_file_out(X, y, files, args.depth), people, classes)

        tree = fit_hand(rows, hand, args.depth, args.features)
        trained[hand] = (tree, f"{right}/{n} correct on unseen files; {len(set(files))} files, "
                               f"{len(set(people))} person(s); " + ", ".join(f"{cl} {counts[cl]}" for cl in classes))
        used = sorted(((imp, name) for name, imp in zip(names, tree.feature_importances_) if imp > 0), reverse=True)
        print("\n  features the tree uses (share of the decisions): "
              + ", ".join(f"{name} {100 * imp:.0f}%" for imp, name in used))
        print(f"  tree trained on all {len(set(files))} files: {tree.tree_.node_count} nodes, {tree.get_n_leaves()} leaves")
        if args.show_tree:
            from sklearn.tree import export_text
            print(export_text(tree, feature_names=names, decimals=1))

    if args.export:
        export(trained, names, args)


def export(trained, names, args):
    """Both trees as C (m2cgen), plus a header that builds the input array from PunchFeatures."""
    import datetime
    import m2cgen
    for hand, (tree, _) in trained.items():
        if tree.n_classes_ != 4:
            sys.exit(f"{hand} tree has {tree.n_classes_} classes, the firmware expects 4: record every class first.")
    stamp = datetime.date.today().isoformat()
    head = [f"// GENERATED by training/tools/train_classifier.py --export (depth {args.depth}, "
            f"features: {args.features}) on {stamp}. Do not edit: re-run the script instead.",
            "// Trees: scikit-learn DecisionTreeClassifier, converted to C by m2cgen."]
    c_lines = head + ["// Plain C (m2cgen uses C99 compound literals, which C++ doesn't allow).", ""]
    for hand, (tree, summary) in trained.items():
        order = "".join(tree.classes_)
        c_lines += [f"// {hand} hand: {summary}",
                    f"// Output order: {order} (the board picks the first largest, as scikit-learn does).",
                    f'const char CLASSIFIER_CLASSES_{hand}[] = "{order}";',
                    m2cgen.export_to_c(tree, function_name=f"classifier_score_{hand}").strip(), ""]
    MODEL_C.write_text("\n".join(c_lines), encoding="utf-8", newline="\n")

    cont = " \\" + "\n    "                         # C macro line continuation
    inputs = ("," + cont).join(f"(double)(f).{C_FIELD[n]}" for n in names)
    h_lines = head + ["#ifndef SOUTHPAW_CLASSIFIER_MODEL_H", "#define SOUTHPAW_CLASSIFIER_MODEL_H", "",
                      f"#define CLASSIFIER_N_INPUTS {len(names)}",
                      "#define CLASSIFIER_N_CLASSES 4",
                      f"// Inputs in training order: {', '.join(names)}",
                      "#define CLASSIFIER_INPUTS(f) {" + cont + inputs + " }", "",
                      "#ifdef __cplusplus", 'extern "C" {', "#endif"]
    for hand in trained:
        h_lines += [f"extern const char CLASSIFIER_CLASSES_{hand}[];",
                    f"void classifier_score_{hand}(double* input, double* output);"]
    h_lines += ["#ifdef __cplusplus", "}", "#endif", "", "#endif", ""]
    MODEL_H.write_text("\n".join(h_lines), encoding="utf-8", newline="\n")
    print(f"\nwrote {MODEL_C.relative_to(sp.ROOT)} and {MODEL_H.name}. Re-flash both boards.")



if __name__ == "__main__":
    main()
