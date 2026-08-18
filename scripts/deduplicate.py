#!/usr/bin/env python3
"""
Deduplicate scraped car images + quarantine cross-label collisions.

This reproduces the offline "stage 1" of the data pipeline documented in
`data/README.md`, so the step lives in the repo as runnable code rather than
only as prose. It does two things:

1. Exact-duplicate removal — the same photo is often reposted across listings.
   Each image gets a content hash; within a class, identical images collapse to
   a single kept copy.
2. Ambiguous quarantine — a photo that appears under *more than one* model
   folder can't be trusted to any single label, so it is excluded (copied to
   `_ambiguous/` for inspection) instead of contaminating a class.

Hashing:
  * default  — SHA-256 of the *decoded RGB pixels* (robust to metadata-only
    differences; matches on identical pixel content).
  * --perceptual — a 64-bit dHash (difference hash). Images are grouped by
    identical dHash, which also catches re-encoded / resized reposts that keep
    the same structure. It does NOT catch heavy crops or edits (that subtler
    leak is handled later by the split-by-listing step in data_gate.ipynb).

Input layout (one subfolder per model):
    <src>/cobalt/*.jpg
    <src>/nexia3/*.jpg
    ...
Output: a deduplicated copy under <dst>/<class>/, plus <dst>/_ambiguous/ and a
`dedup_report.csv` recording the decision for every file (nothing removed blind).

Usage:
    python scripts/deduplicate.py --src data/raw --dst data/dedup
    python scripts/deduplicate.py --src data/raw --dst data/dedup --perceptual
    python scripts/deduplicate.py --src data/raw --dry-run     # report only, no copies

The image folders are git-ignored (not redistributed); run this locally on the
scraped data.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import shutil
import sys
from collections import defaultdict
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    sys.exit("Pillow is required:  pip install pillow")

EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def content_hash(im: "Image.Image") -> str:
    """SHA-256 of the decoded RGB pixels — exact on pixel content."""
    return hashlib.sha256(im.convert("RGB").tobytes()).hexdigest()


def dhash(im: "Image.Image", size: int = 8) -> str:
    """64-bit difference hash as hex — catches structure-preserving re-encodes."""
    small = im.convert("L").resize((size + 1, size), Image.LANCZOS)
    px = list(small.getdata())
    bits = 0
    for row in range(size):
        base = row * (size + 1)
        for col in range(size):
            bits = (bits << 1) | (1 if px[base + col] > px[base + col + 1] else 0)
    return f"{bits:016x}"


def discover_classes(src: Path) -> list[str]:
    return sorted(p.name for p in src.iterdir() if p.is_dir() and not p.name.startswith("_"))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", type=Path, default=Path("data/raw"), help="root with one subfolder per model")
    ap.add_argument("--dst", type=Path, default=Path("data/dedup"), help="output root for the deduped set")
    ap.add_argument("--perceptual", action="store_true", help="use dHash (catches re-encodes) instead of exact pixel hash")
    ap.add_argument("--dry-run", action="store_true", help="report only; do not copy any files")
    ap.add_argument("--report", type=Path, default=None, help="report CSV path (default: <dst>/dedup_report.csv)")
    args = ap.parse_args()

    if not args.src.is_dir():
        sys.exit(f"source folder not found: {args.src}")
    classes = discover_classes(args.src)
    if not classes:
        sys.exit(f"no class subfolders found in {args.src}")
    hasher = dhash if args.perceptual else content_hash
    print(f"classes: {classes}")
    print(f"hashing: {'dHash (perceptual)' if args.perceptual else 'SHA-256 pixels (exact)'}\n")

    # hash -> list of (class, path); track per-file records for the report
    groups: dict[str, list[tuple[str, Path]]] = defaultdict(list)
    records: list[dict] = []
    errors = 0
    for cls in classes:
        files = sorted(p for p in (args.src / cls).iterdir() if p.suffix.lower() in EXTS)
        for p in files:
            try:
                with Image.open(p) as im:
                    h = hasher(im)
            except Exception as e:  # unreadable / truncated image
                errors += 1
                records.append({"path": str(p), "class": cls, "hash": "", "decision": "error", "note": str(e)[:120]})
                continue
            groups[h].append((cls, p))

    # decide per hash group
    dst = args.dst
    amb_dir = dst / "_ambiguous"
    kept_per_class: dict[str, int] = defaultdict(int)
    dup_removed = 0
    ambiguous = 0

    for h, members in groups.items():
        classes_here = {c for c, _ in members}
        if len(classes_here) > 1:
            # appears under multiple labels -> cannot trust -> quarantine, exclude from all
            rep_cls, rep_path = sorted(members, key=lambda m: (m[0], m[1].name))[0]
            if not args.dry_run:
                amb_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(rep_path, amb_dir / f"{'-'.join(sorted(classes_here))}__{rep_path.name}")
            for c, p in members:
                ambiguous += 1
                records.append({"path": str(p), "class": c, "hash": h,
                                "decision": "ambiguous_excluded", "note": f"also in {sorted(classes_here)}"})
        else:
            # single label -> keep one representative, drop the rest as duplicates
            members_sorted = sorted(members, key=lambda m: m[1].name)
            rep_cls, rep_path = members_sorted[0]
            if not args.dry_run:
                (dst / rep_cls).mkdir(parents=True, exist_ok=True)
                shutil.copy2(rep_path, dst / rep_cls / rep_path.name)
            kept_per_class[rep_cls] += 1
            records.append({"path": str(rep_path), "class": rep_cls, "hash": h,
                            "decision": "kept", "note": "representative"})
            for c, p in members_sorted[1:]:
                dup_removed += 1
                records.append({"path": str(p), "class": c, "hash": h,
                                "decision": "duplicate_removed", "note": f"dup of {rep_path.name}"})

    # write the report (even on dry-run)
    report = args.report or (dst / "dedup_report.csv")
    if not args.dry_run:
        report.parent.mkdir(parents=True, exist_ok=True)
    if not args.dry_run or args.report:
        with open(report, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=["path", "class", "hash", "decision", "note"])
            w.writeheader()
            w.writerows(records)

    # summary
    total_in = sum(len(m) for m in groups.values()) + errors
    total_kept = sum(kept_per_class.values())
    print("── per-class kept ──")
    for c in classes:
        print(f"  {c:10s} {kept_per_class.get(c, 0)}")
    print("\n── totals ──")
    print(f"  scanned            : {total_in}")
    print(f"  unique kept        : {total_kept}")
    print(f"  duplicates removed : {dup_removed}")
    print(f"  ambiguous excluded : {ambiguous}  (in >1 label folder)")
    if errors:
        print(f"  unreadable (skipped): {errors}")
    print(f"\n{'[dry-run] ' if args.dry_run else ''}report: {report}")
    if not args.dry_run:
        print(f"deduped set: {dst}/  (+ {amb_dir}/ for inspection)")


if __name__ == "__main__":
    main()
