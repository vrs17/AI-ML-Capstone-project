#!/usr/bin/env python3
"""audit_composition.py — measure how much of a scraped set is usable exterior car shots.

WHY: a listing gallery is photographed for a human buyer, not for a classifier. Alongside
the exterior shots that carry model identity, sellers post odometers, dashboards, engine
bays, seats and trunk linings. Those are legitimate photos of the car, but they carry almost
no model-identifying signal — an odometer close-up of a Cobalt looks like an odometer
close-up of a Gentra — so they are label noise in a model classifier.

This script MEASURES that split. It reads images, runs the detector, and reports numbers.
It moves, deletes and rewrites nothing, so it is safe to run against the live scrape and
its output can inform whether a filtering stage is worth adding (and what per-model target
would survive it).

Method: YOLO detects COCO vehicle classes (car/truck/bus). An image counts as USABLE when
its largest vehicle box covers at least --min-area of the frame (default 12%) — that
threshold keeps whole-car shots and rejects both non-car photos and distant background
traffic. Per-class and overall percentages are written to a CSV alongside a printed summary.

KNOWN BLIND SPOT (validated against hand-labelled images, 2026-08-30): "usable" here means
"a vehicle surface fills the frame", which is NOT the same as "a whole car is visible".
Measured on nine hand-labelled gentra photos:

    dashboard / seats / instrument cluster ->  0% car area  -> correctly rejected
    exterior side & rear shots             -> 23-54%        -> correctly kept
    engine bay (open hood)                 -> 64-98%        -> WRONGLY kept
    door card (shot from outside)          -> 90%           -> WRONGLY kept

Raising --min-area does not fix this: an engine bay scores *higher* than a genuine side
shot. So treat this script's number as an UPPER BOUND on whole-car images; the true figure
is lower by the engine-bay/detail-shot share (~15-20% by eye). Separating those needs shape
or aspect reasoning, or a small purpose-trained exterior/detail classifier — not a
detector-area threshold.

Usage:
    python scripts/audit_composition.py                       # sample 150/class
    python scripts/audit_composition.py --sample 0            # every image (slow)
    python scripts/audit_composition.py --min-area 0.15
"""
import argparse
import csv
import random
import sys
from pathlib import Path

EXTS = {".jpg", ".jpeg", ".png", ".webp"}
VEHICLE_NAMES = {"car", "truck", "bus"}


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", type=Path, default=Path("data/raw"))
    ap.add_argument("--weights", type=Path, default=Path("service/yolo11s.pt"))
    ap.add_argument("--sample", type=int, default=150,
                    help="images sampled per class (0 = all)")
    ap.add_argument("--min-area", type=float, default=0.12,
                    help="largest vehicle box must cover this fraction of the frame")
    ap.add_argument("--conf", type=float, default=0.35, help="detector confidence threshold")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--report", type=Path, default=Path("reports/composition_audit.csv"))
    args = ap.parse_args()

    try:
        from ultralytics import YOLO
    except ImportError:
        sys.exit("ultralytics is required:  pip install ultralytics")
    if not args.weights.exists():
        sys.exit(f"weights not found: {args.weights}")
    if not args.src.is_dir():
        sys.exit(f"source folder not found: {args.src}")

    model = YOLO(str(args.weights))
    names = model.names
    rng = random.Random(args.seed)

    classes = sorted(p for p in args.src.iterdir()
                     if p.is_dir() and not p.name.startswith(("_", ".")))
    rows, tot_seen, tot_usable = [], 0, 0
    print(f"detector: {args.weights.name} · conf {args.conf} · min-area {args.min_area:.0%}\n")

    for d in classes:
        files = sorted(p for p in d.iterdir() if p.suffix.lower() in EXTS)
        if not files:
            continue
        sample = (files if args.sample <= 0 or len(files) <= args.sample
                  else rng.sample(files, args.sample))
        usable = no_vehicle = too_small = unreadable = 0

        for p in sample:
            try:
                res = model.predict(str(p), conf=args.conf, verbose=False)[0]
            except Exception:
                unreadable += 1
                continue
            h, w = res.orig_shape
            frame = float(h * w) or 1.0
            best = 0.0
            for box in res.boxes:
                if names.get(int(box.cls), "") in VEHICLE_NAMES:
                    x1, y1, x2, y2 = (float(v) for v in box.xyxy[0])
                    best = max(best, abs((x2 - x1) * (y2 - y1)) / frame)
            if best <= 0:
                no_vehicle += 1
            elif best < args.min_area:
                too_small += 1
            else:
                usable += 1

        n = len(sample)
        pct = usable / n if n else 0.0
        tot_seen += n
        tot_usable += usable
        rows.append({"class": d.name, "total_files": len(files), "sampled": n,
                     "usable_exterior": usable, "usable_pct": round(100 * pct, 1),
                     "no_vehicle": no_vehicle, "vehicle_too_small": too_small,
                     "unreadable": unreadable,
                     "projected_usable_in_class": round(len(files) * pct)})
        print(f"  {d.name:28s} {usable:4d}/{n:<4d} usable ({pct:5.1%})"
              f"   no-vehicle {no_vehicle:3d} · too-small {too_small:3d}"
              f"   -> ~{round(len(files) * pct)} of {len(files)}")

    if not rows:
        sys.exit("no images found to audit")

    args.report.parent.mkdir(parents=True, exist_ok=True)
    with args.report.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    overall = tot_usable / tot_seen if tot_seen else 0
    print(f"\noverall vehicle-visible: {tot_usable}/{tot_seen} sampled = {overall:.1%}")
    print("NOTE: this is an UPPER BOUND on whole-car images — engine bays and door cards")
    print("      score high here (see the blind-spot section in this script's docstring).")
    print(f"implication: a 2000 target yields at most ~{round(2000 * overall)} usable images; "
          f"to land 2000, collect ~{round(2000 / overall) if overall else 0} or more")
    print(f"report -> {args.report}   (measurement only — no files were changed)")


if __name__ == "__main__":
    main()
