"""Tune the cosine match thresholds against labelled face images.

W2 (v2.0). The bands in config.py were adopted from the literature; this
script measures them against our own data so the values in the report can be
justified rather than asserted.

Usage
-----
    python scripts/tune_threshold.py --dataset path/to/faces
    python scripts/tune_threshold.py --separation          # no dataset needed

--dataset expects one folder per student, each holding two or more photographs
of that person:

    faces/
      2122414642/  img1.jpg  img2.jpg  img3.jpg
      2131174642/  img1.jpg  img2.jpg

Every within-folder pair is a positive (same person), every across-folder pair
a negative. The script reports the threshold that maximises accuracy, plus the
false-accept and false-reject rates at the thresholds currently configured.

--separation needs no images. It reports the distribution of distances between
the enrolled templates already in the database, which bounds how low the match
threshold can go before confident matches start being rejected.
"""

import argparse
import itertools
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import COSINE_MATCH_THRESHOLD, COSINE_REVIEW_THRESHOLD  # noqa: E402
from database.db_manager import get_all_students  # noqa: E402
from utils.detection import detect_faces, extract_face  # noqa: E402
from utils.recognition import (  # noqa: E402
    compare_faces,
    find_optimal_threshold,
    get_face_embedding,
)

IMAGE_EXTS = {".jpg", ".jpeg", ".png"}


def embed_dataset(root):
    """Return {label: [embedding, ...]} for one folder per person."""
    people = {}
    for entry in sorted(os.listdir(root)):
        folder = os.path.join(root, entry)
        if not os.path.isdir(folder):
            continue
        vectors = []
        for name in sorted(os.listdir(folder)):
            if os.path.splitext(name)[1].lower() not in IMAGE_EXTS:
                continue
            path = os.path.join(folder, name)
            image = cv2.imread(path)
            if image is None:
                print(f"  skipped (unreadable): {path}")
                continue
            boxes = detect_faces(image)
            if len(boxes) != 1:
                print(f"  skipped ({len(boxes)} faces, need exactly 1): {path}")
                continue
            vector = get_face_embedding(extract_face(image, boxes[0]))
            if vector is None:
                print(f"  skipped (no embedding): {path}")
                continue
            vectors.append(vector)
        if vectors:
            people[entry] = vectors
            print(f"  {entry}: {len(vectors)} usable image(s)")
    return people


def build_pairs(people):
    """(embedding_a, embedding_b, same_person) over all labelled pairs."""
    pairs = []
    for vectors in people.values():
        pairs += [(a, b, True) for a, b in itertools.combinations(vectors, 2)]
    for one, two in itertools.combinations(people.values(), 2):
        pairs += [(a, b, False) for a in one for b in two]
    return pairs


def rates_at(pairs, threshold):
    """False-accept and false-reject rates at a given threshold."""
    pos = [compare_faces(a, b) for a, b, same in pairs if same]
    neg = [compare_faces(a, b) for a, b, same in pairs if not same]
    far = sum(d <= threshold for d in neg) / len(neg) if neg else float('nan')
    frr = sum(d > threshold for d in pos) / len(pos) if pos else float('nan')
    return far, frr


def report_dataset(root):
    print(f"Reading {root}")
    people = embed_dataset(root)
    if len(people) < 2:
        print("\nNeed at least two people with usable images. Nothing to tune.")
        return 1

    pairs = build_pairs(people)
    positives = sum(1 for _, _, same in pairs if same)
    print(f"\n{len(people)} people, {len(pairs)} pairs "
          f"({positives} same-person, {len(pairs) - positives} different-person)")
    if positives == 0:
        print("No same-person pairs: give at least two photographs of someone.")
        return 1

    result = find_optimal_threshold(pairs)
    print(f"\nBest accuracy {result['accuracy']}% at threshold "
          f"{result['optimal_threshold']}")

    print("\nAt the thresholds currently in config.py:")
    for label, value in (("match ", COSINE_MATCH_THRESHOLD),
                         ("review", COSINE_REVIEW_THRESHOLD)):
        far, frr = rates_at(pairs, value)
        print(f"  {label} {value:.2f}  false accept {far:6.1%}   "
              f"false reject {frr:6.1%}")
    return 0


def report_separation():
    """Distance between enrolled templates. Needs no images."""
    students = get_all_students()
    if len(students) < 2:
        print("Fewer than two students enrolled; nothing to compare.")
        return 1

    distances = [
        compare_faces(a['embedding'], b['embedding'])
        for a, b in itertools.combinations(students, 2)
    ]
    arr = np.array(distances)
    print(f"{len(students)} enrolled templates, "
          f"{len(distances)} between-student pairs\n")
    print(f"  minimum   {arr.min():.4f}   <- closest two different students")
    print(f"  mean      {arr.mean():.4f}")
    print(f"  maximum   {arr.max():.4f}")
    print(f"\nReview threshold is {COSINE_REVIEW_THRESHOLD:.2f}.")
    below = int((arr <= COSINE_REVIEW_THRESHOLD).sum())
    if below:
        print(f"  WARNING: {below} different-student pair(s) fall within it, "
              f"so those identities can be confused.")
    else:
        print("  No two different students fall within it.")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dataset", help="folder with one subfolder per person")
    parser.add_argument("--separation", action="store_true",
                        help="report distances between enrolled templates")
    args = parser.parse_args()

    if args.separation:
        return report_separation()
    if args.dataset:
        if not os.path.isdir(args.dataset):
            print(f"Not a folder: {args.dataset}")
            return 1
        return report_dataset(args.dataset)
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
