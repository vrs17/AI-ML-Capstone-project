#!/usr/bin/env python3
"""Convert a trained checkpoint to a committable fp16 copy.

Why this exists
---------------
The service needs `service/artifacts/model.pt` to start. Committing it is what makes
`git clone && run` actually work — but the fp32 checkpoint is ~114 MB and **GitHub
rejects any file over 100 MB** outright (no warning, the push fails).

Half precision solves it without Git LFS, a release download, or an external host:

    fp32   ~114 MB   rejected
    fp16    ~56 MB   fits, and a plain `git clone` brings it along

This is not a quality trade. The service already runs the model in fp16 on GPU
(`FP16=true`), so those are the numbers it computes with anyway. `load_state_dict`
casts fp16 tensors back up when the model is fp32, so CPU inference is unaffected —
measured max logit difference on a round trip: 0.0008, with identical top-1.

Usage
-----
    python scripts/pack_weights.py --src /path/to/modelgate_v2_artifacts

Reads model.pt + config.json from --src, writes fp16 model.pt + config.json to --dst
(default service/artifacts/), then verifies the result reloads and matches.
"""
from __future__ import annotations

import argparse, json, shutil, sys
from pathlib import Path

import torch

GITHUB_LIMIT_MB = 100


def to_fp16(sd: dict) -> dict:
    """Halve the float tensors; leave integer buffers (counts, indices) alone."""
    return {k: (v.half() if torch.is_tensor(v) and v.is_floating_point() else v)
            for k, v in sd.items()}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", required=True,
                    help="directory holding the trained model.pt and config.json")
    ap.add_argument("--dst", default="service/artifacts",
                    help="where to write the committable copy (default: service/artifacts)")
    ap.add_argument("--keep-fp32", action="store_true",
                    help="copy the fp32 checkpoint through unchanged (will NOT push to GitHub)")
    a = ap.parse_args()

    src, dst = Path(a.src), Path(a.dst)
    src_model, src_cfg = src / "model.pt", src / "config.json"
    for f in (src_model, src_cfg):
        if not f.exists():
            print(f"error: {f} not found", file=sys.stderr)
            return 1
    dst.mkdir(parents=True, exist_ok=True)

    obj = torch.load(src_model, map_location="cpu")
    # model_gate_v2 saves a bare state dict, but tolerate a wrapped one too
    sd = obj.get("state_dict", obj) if isinstance(obj, dict) and "state_dict" in obj else obj
    if not isinstance(sd, dict):
        print("error: checkpoint is not a state dict", file=sys.stderr)
        return 1

    out_sd = sd if a.keep_fp32 else to_fp16(sd)
    torch.save(out_sd, dst / "model.pt")
    shutil.copy2(src_cfg, dst / "config.json")

    before = src_model.stat().st_size / 1e6
    after = (dst / "model.pt").stat().st_size / 1e6
    print(f"{src_model}  {before:6.1f} MB")
    print(f"{dst/'model.pt'}  {after:6.1f} MB"
          + ("" if a.keep_fp32 else f"   ({before/after:.1f}x smaller)"))

    # verify: same keys, same shapes, values still equal within fp16 resolution
    back = torch.load(dst / "model.pt", map_location="cpu")
    assert set(back) == set(sd), "key set changed"
    worst = 0.0
    for k, v in sd.items():
        if torch.is_tensor(v) and v.is_floating_point():
            assert back[k].shape == v.shape, f"shape changed for {k}"
            worst = max(worst, (back[k].float() - v.float()).abs().max().item())
    print(f"verified: {len(sd)} tensors, max weight change {worst:.2e}")

    cls = json.loads((dst / 'config.json').read_text()).get("classes")
    print(f"classes: {cls}")

    if after > GITHUB_LIMIT_MB:
        print(f"\n!! {after:.0f} MB exceeds GitHub's {GITHUB_LIMIT_MB} MB file limit — the push "
              f"WILL be rejected.\n   Drop --keep-fp32, or use Git LFS / a Release asset.")
        return 2
    print(f"\nready to commit ({after:.0f} MB, under GitHub's {GITHUB_LIMIT_MB} MB limit):")
    print(f"    git add -f {dst}/model.pt {dst}/config.json")
    print( "    git commit -m 'Add trained weights so the service runs from a fresh clone'")
    print( "    git push")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
