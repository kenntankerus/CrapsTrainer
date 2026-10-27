"""
train_dice_model.py
-------------------
Teaches the tracker what YOUR dice look like, from the pictures it saves.

Every camera throw saves a small picture of each die to the dice_crops folder,
and dice_throws.csv says what the dice really were (after any fix you made with F2).
This script pairs them up, tests how well a model built from them reads dice it
hasn't seen, and saves the model as dice_model.npz. dice_vision.py then uses it
whenever the normal pip-layout check isn't sure.

Run:    python train_dice_model.py
Then restart dice_tracker_v2.py so it picks up the new model.

Run it again whenever you've logged a lot more throws; it always rebuilds from
everything saved so far. Keep this file in the same folder as dice_vision.py.
"""

import csv
import os
import random
import sys
from collections import Counter, defaultdict

import cv2
import numpy as np

import dice_vision as dv

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(HERE, "dice_throws.csv")
CROP_DIR = os.path.join(HERE, "dice_crops")
MIN_PER_FACE = 15          # need at least this many saved pictures of each face 1-6


def labelled_pairs(row):
    """Which value goes with the left picture and which with the right.

    Normally the left die is die1. If you fixed a throw and typed the dice in the
    other order, we line them up with what the camera saw where we can."""
    die1, die2 = int(row["die1"]), int(row["die2"])
    camera = row.get("camera_read", "")
    if camera and camera != "unclear":
        left, right = (int(v) for v in camera.split("-"))
        same_order = (left == die1) + (right == die2)
        swapped = (left == die2) + (right == die1)
        if swapped > same_order:
            return die2, die1
    return die1, die2


def load_examples():
    """Return a list of (throw_id, face picture, value)."""
    if not os.path.exists(DATA_FILE):
        sys.exit(f"No {os.path.basename(DATA_FILE)} next to this script yet. Log some camera throws first.")
    examples = []
    with open(DATA_FILE, newline="") as f:
        for row in csv.DictReader(f):
            throw_id = row.get("throw_id") or ""
            if not throw_id:
                continue
            values = labelled_pairs(row)
            for side, value in zip(("left", "right"), values):
                path = os.path.join(CROP_DIR, f"{throw_id}_{side}.png")
                face = cv2.imread(path)
                if face is not None:
                    examples.append((throw_id, face, value))
    return examples


def build(examples):
    """Features and labels, with each picture in all 8 orientations."""
    features, labels = [], []
    for _, face, value in examples:
        for version in dv.turned_and_mirrored(face):
            features.append(dv.face_features(version))
            labels.append(value)
    return np.array(features, dtype=np.float32), np.array(labels, dtype=np.int64)


def main():
    examples = load_examples()
    counts = Counter(value for _, _, value in examples)
    print(f"Found {len(examples)} saved dice pictures.")
    print("  per face: " + ", ".join(f"{face}: {counts.get(face, 0)}" for face in range(1, 7)))

    short = [face for face in range(1, 7) if counts.get(face, 0) < MIN_PER_FACE]
    if short:
        print(f"\nNot enough yet: each face needs at least {MIN_PER_FACE} pictures "
              f"(short on: {', '.join(map(str, short))}).")
        print("Keep throwing with the camera on, fix any wrong reads with F2, and run this again.")
        return

    # ---- test: hold back 1 in 5 throws, build from the rest, and see how it does on those
    by_throw = defaultdict(list)
    for example in examples:
        by_throw[example[0]].append(example)
    throw_ids = sorted(by_throw)
    random.Random(1).shuffle(throw_ids)
    held_back = set(throw_ids[: max(1, len(throw_ids) // 5)])
    train = [e for e in examples if e[0] not in held_back]
    test = [e for e in examples if e[0] in held_back]

    dv._model = build(train)
    right = confident = confident_right = 0
    mistakes = Counter()
    for _, face, value in test:
        guess, agreement = dv.model_guess(face)
        right += guess == value
        if agreement >= dv.MODEL_AGREEMENT:
            confident += 1
            confident_right += guess == value
        if guess != value:
            mistakes[(value, guess)] += 1
    print(f"\nTest on {len(test)} pictures it hadn't seen:")
    print(f"  right on {right} of {len(test)} ({right / len(test):.0%})")
    if confident:
        print(f"  when it's sure enough to be used: right on {confident_right} of {confident} "
              f"({confident_right / confident:.0%}), sure on {confident / len(test):.0%} of pictures")
    if mistakes:
        common = ", ".join(f"{real} read as {guess} ({n}x)" for (real, guess), n in mistakes.most_common(4))
        print(f"  most common mix-ups: {common}")

    # ---- final model from everything
    features, labels = build(examples)
    np.savez_compressed(dv.MODEL_FILE, features=features, labels=labels, faces=len(examples))
    print(f"\nSaved {os.path.basename(dv.MODEL_FILE)} ({len(examples)} pictures). "
          "Restart the tracker to use it.")


if __name__ == "__main__":
    main()
