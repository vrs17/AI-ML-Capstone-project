#!/usr/bin/env python3
"""contact_sheets.py — one labelled grid image per class, for eyeballing a scraped set.

The Data Gate asks a question no metric answers: *are these actually pictures of this car?*
Opening hundreds of files per class is impractical, so this samples N images per model with
a fixed seed (so the sample is reproducible and can be cited in a report) and lays them out
in a grid with the filename under each thumbnail. One sheet per class reviews in seconds,
and the filename keeps every thumbnail traceable back to its listing.

Usage:
    python scripts/contact_sheets.py                       # data/raw -> reports/contact_sheets
    python scripts/contact_sheets.py --n 20 --seed 42
    python scripts/contact_sheets.py --src data/dedup --dst reports/sheets_dedup
"""
import argparse
import random
import sys
from pathlib import Path

try:
    from PIL import Image, ImageDraw
except ImportError:
    sys.exit("Pillow is required:  pip install pillow")

EXTS = {".jpg", ".jpeg", ".png", ".webp"}
COLS, THUMB_W, THUMB_H, PAD, CAPTION_H = 5, 320, 240, 8, 18
BG, FG_CAPTION, FG_ERROR = (24, 24, 24), (200, 200, 200), (255, 80, 80)


def build_sheet(model_dir: Path, out_path: Path, n: int, rng: random.Random) -> int:
    files = sorted(p for p in model_dir.iterdir() if p.suffix.lower() in EXTS)
    if not files:
        return 0
    sample = sorted(files if len(files) <= n else rng.sample(files, n), key=lambda p: p.name)

    rows = (len(sample) + COLS - 1) // COLS
    cell_h = THUMB_H + CAPTION_H
    canvas = Image.new("RGB",
                       (COLS * (THUMB_W + PAD) + PAD, rows * (cell_h + PAD) + PAD), BG)
    draw = ImageDraw.Draw(canvas)

    for i, p in enumerate(sample):
        r, c = divmod(i, COLS)
        x, y = PAD + c * (THUMB_W + PAD), PAD + r * (cell_h + PAD)
        try:
            with Image.open(p) as im:
                im = im.convert("RGB")
                im.thumbnail((THUMB_W, THUMB_H))
                canvas.paste(im, (x + (THUMB_W - im.width) // 2,
                                  y + (THUMB_H - im.height) // 2))
        except Exception as e:                      # truncated / unreadable file
            draw.text((x + 4, y + THUMB_H // 2), f"UNREADABLE: {e}"[:48], fill=FG_ERROR)
        draw.text((x + 2, y + THUMB_H + 2), p.name[:44], fill=FG_CAPTION)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path, quality=88)
    return len(sample)


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", type=Path, default=Path("data/raw"))
    ap.add_argument("--dst", type=Path, default=Path("reports/contact_sheets"))
    ap.add_argument("--n", type=int, default=20, help="images sampled per class")
    ap.add_argument("--seed", type=int, default=42, help="fixed so the sample is reproducible")
    args = ap.parse_args()

    if not args.src.is_dir():
        sys.exit(f"source folder not found: {args.src}")
    classes = sorted(p for p in args.src.iterdir()
                     if p.is_dir() and not p.name.startswith(("_", ".")))
    if not classes:
        sys.exit(f"no class subfolders in {args.src}")

    rng = random.Random(args.seed)
    total = 0
    for d in classes:
        got = build_sheet(d, args.dst / f"{d.name}.jpg", args.n, rng)
        total += got
        print(f"  {d.name:32s} {got:3d} sampled"
              + ("" if got else "   (empty — nothing collected yet)"))
    print(f"\n{len(classes)} sheets, {total} thumbnails -> {args.dst}/  (seed {args.seed})")


if __name__ == "__main__":
    main()
