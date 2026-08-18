#!/usr/bin/env python3
"""Measure the model's operating envelope: accuracy vs. how big the car arrives.

Why this script exists
----------------------
The live console reports that cars reach the classifier at ~163px when it wants 438px,
and the obvious question is "so can we just work at 163px?". That is an empirical
question about *this* model and *this* dataset, so it should be answered with a
measurement rather than an opinion.

The method: take the sealed test set and, for each candidate size, shrink every image so
its short side is exactly that many pixels — simulating a sensor that never resolved more
than that — then push it through the *unchanged* production pipeline, which upscales it
back to 384. Nothing else changes. The drop you see is purely the information the camera
failed to capture.

It reports, at each size:
  accuracy / macro-F1   raw classification quality
  precision @ thr       the number the product actually promises, on the five known models
  coverage              how often it is willing to answer at all

Read it as an operating envelope: the smallest size at which precision still clears your
target is the distance your cameras have to be placed within.

Usage
-----
    python scripts/resolution_sweep.py --artifacts service/artifacts --data data/dataset/test

In Colab, point --artifacts at the unzipped modelgate_v2_artifacts and --data at
dataset/test. Runs on CPU; a GPU just makes it quicker.
"""
from __future__ import annotations

import argparse, json, sys
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from PIL import Image
from torch.utils.data import DataLoader
from torchvision import datasets, transforms

DEFAULT_SIZES = [96, 128, 160, 192, 224, 288, 352, 438, 0]      # 0 = untouched original


# ── model defs, verbatim from model_gate_v2 so the checkpoint loads strict ──
class SubCenterArcFace(nn.Module):
    def __init__(self, in_features, num_classes, K=3, s=30.0, m=0.30):
        super().__init__()
        self.num_classes, self.K, self.s, self.m = num_classes, K, s, m
        self.W = nn.Parameter(torch.empty(num_classes * K, in_features))
        nn.init.xavier_uniform_(self.W)

    def forward(self, feat, labels=None):
        f = F.normalize(feat, dim=1)
        w = F.normalize(self.W, dim=1)
        cos = (f @ w.t()).view(-1, self.num_classes, self.K).amax(dim=2)
        cos = cos.float().clamp(-1 + 1e-6, 1 - 1e-6)
        if labels is None:
            return self.s * cos
        theta = torch.acos(cos)
        margin = torch.zeros_like(cos).scatter_(1, labels.view(-1, 1), self.m)
        return self.s * torch.cos(theta + margin)


class ArcModel(nn.Module):
    def __init__(self, model_id, num_classes, K=3, s=30.0, m=0.30, pretrained=False):
        super().__init__()
        import timm
        self.backbone = timm.create_model(model_id, pretrained=pretrained,
                                          num_classes=0, global_pool="avg")
        self.head = SubCenterArcFace(self.backbone.num_features, num_classes, K, s, m)

    def forward(self, x, labels=None):
        return self.head(self.backbone(x), labels)


class SnapMixNet(nn.Module):
    def __init__(self, model_id, num_classes, pretrained=False):
        super().__init__()
        import timm
        self.backbone = timm.create_model(model_id, pretrained=pretrained,
                                          num_classes=0, global_pool="")
        self.fc = nn.Linear(self.backbone.num_features, num_classes)

    def forward(self, x):
        f = self.backbone.forward_features(x)
        return self.fc(F.adaptive_avg_pool2d(f, 1).flatten(1))


class CaptureLimit:
    """Shrink so the short side is `px` — never upscales, so it only ever removes detail.

    This is the honest simulation of a camera further away: an image that was already
    smaller than the limit is left alone rather than being stretched, which would add a
    second, different distortion and confound the measurement.
    """

    def __init__(self, px: int):
        self.px = px
        self.applied = 0
        self.total = 0

    def __call__(self, img: Image.Image) -> Image.Image:
        self.total += 1
        if not self.px:
            return img
        w, h = img.size
        short = min(w, h)
        if short <= self.px:
            return img
        self.applied += 1
        sc = self.px / short
        return img.resize((max(1, round(w * sc)), max(1, round(h * sc))), Image.BILINEAR)


def evaluate(model, ds, dev, batch, workers):
    loader = DataLoader(ds, batch_size=batch, shuffle=False, num_workers=workers)
    logits, ys = [], []
    with torch.inference_mode():
        for x, y in loader:
            logits.append(model(x.to(dev)).float().cpu())
            ys.append(y)
    return torch.cat(logits), torch.cat(ys)


def metrics(logits, y, classes, T, thr, reject):
    p = torch.softmax(logits / max(T, 1e-6), dim=1).numpy()
    pred, conf, y = p.argmax(1), p.max(1), y.numpy()
    acc = float((pred == y).mean())

    f1s = []
    for c in range(len(classes)):
        tp = int(((pred == c) & (y == c)).sum())
        fp = int(((pred == c) & (y != c)).sum())
        fn = int(((pred != c) & (y == c)).sum())
        pr = tp / (tp + fp) if tp + fp else 0.0
        rc = tp / (tp + fn) if tp + fn else 0.0
        f1s.append(2 * pr * rc / (pr + rc) if pr + rc else 0.0)
    macro_f1 = float(np.mean(f1s))

    # The product's promise: of the images it names as one of the five, how many are right.
    oth = classes.index(reject) if reject in classes else -1
    answered = (pred != oth) & (conf >= (thr if thr is not None else 0.0))
    precision = float((pred[answered] == y[answered]).mean()) if answered.any() else float("nan")
    return acc, macro_f1, precision, float(answered.mean())


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--artifacts", default="service/artifacts",
                    help="directory holding model.pt and config.json")
    ap.add_argument("--data", default="data/dataset/test", help="ImageFolder test split")
    ap.add_argument("--sizes", type=int, nargs="+", default=DEFAULT_SIZES,
                    help="short-side pixel budgets to simulate (0 = original)")
    ap.add_argument("--batch", type=int, default=32)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--device", default="auto")
    ap.add_argument("--csv", default=None, help="also write the table here")
    a = ap.parse_args()

    art = Path(a.artifacts)
    cfg = json.loads((art / "config.json").read_text())
    classes, img = cfg["classes"], int(cfg.get("img_size", 384))
    resize = 438 if img == 384 else round(img / 0.875)     # must match model_gate_v2
    mean, std = tuple(cfg.get("mean", (.485, .456, .406))), tuple(cfg.get("std", (.229, .224, .225)))
    T = float(cfg.get("temperature", 1.0))
    thr = cfg.get("abstain_threshold")
    thr = float(thr) if thr is not None else None
    reject = cfg.get("reject_class", "others")

    dev = torch.device(("cuda" if torch.cuda.is_available() else "cpu")
                       if a.device == "auto" else a.device)
    head = cfg.get("head", "linear")
    if head == "arcface":
        model = ArcModel(cfg["model_id"], len(classes), pretrained=False, **cfg.get("arc", {}))
    elif head == "snapmix":
        model = SnapMixNet(cfg["model_id"], len(classes), pretrained=False)
    else:
        import timm
        model = timm.create_model(cfg["model_id"], pretrained=False, num_classes=len(classes))
    model.load_state_dict(torch.load(art / "model.pt", map_location="cpu"))
    model.eval().to(dev)

    print(f"model {cfg['model_id']} · head {head} · {len(classes)} classes · {dev}")
    print(f"pipeline Resize({resize}) -> CenterCrop({img}) · T={T:.3f} · "
          f"threshold={thr if thr is None else f'{thr:.3f}'}\n")
    print(f"{'capture':>9} {'upscale':>8} {'affected':>9} {'accuracy':>9} "
          f"{'macro-F1':>9} {'precision':>10} {'coverage':>9}")
    print("-" * 70)

    rows = []
    for px in a.sizes:
        cap = CaptureLimit(px)
        tf = transforms.Compose([cap, transforms.Resize(resize), transforms.CenterCrop(img),
                                 transforms.ToTensor(), transforms.Normalize(mean, std)])
        ds = datasets.ImageFolder(a.data, tf)
        if ds.classes != classes:
            sys.exit(f"class order mismatch: dataset {ds.classes} vs model {classes}")
        logits, y = evaluate(model, ds, dev, a.batch, a.workers)
        acc, f1, prec, cov = metrics(logits, y, classes, T, thr, reject)
        share = cap.applied / max(cap.total, 1)
        label = "original" if not px else f"{px}px"
        up = "—" if not px else f"{resize/px:.1f}x"
        # precision is undefined when the trust layer answered nothing — say so
        pstr = "—" if cov == 0 or prec != prec else f"{prec:.3f}"
        print(f"{label:>9} {up:>8} {share:>8.0%} {acc:>9.3f} {f1:>9.3f} "
              f"{pstr:>10} {cov:>9.1%}")
        rows.append({"capture_px": px, "upscale": None if not px else round(resize/px, 2),
                     "share_affected": round(share, 4), "accuracy": round(acc, 4),
                     "macro_f1": round(f1, 4),
                     "precision": None if prec != prec else round(prec, 4),
                     "coverage": round(cov, 4)})

    if a.csv:
        import csv
        with open(a.csv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader(); w.writerows(rows)
        print(f"\nwrote {a.csv}")

    # the operating envelope, stated plainly
    graded = [r for r in rows if r["capture_px"]]
    for target in (0.99, 0.95):
        ok = [r for r in graded if r["precision"] is not None and r["precision"] >= target]
        floor = min(r["capture_px"] for r in ok) if ok else None
        print(f"\nsmallest capture holding {target:.0%} precision: "
              + (f"{floor}px" if floor else "none of the sizes tested"))
    print("\nPlace cameras so cars cross that size, or treat anything smaller as "
          "'count it, don't name it'.")


if __name__ == "__main__":
    main()
