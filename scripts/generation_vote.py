"""generation_vote.py: visual old-vs-new generation vote per listing for a generic/newer class pair
(tracker vs tracker_2, malibu vs malibu2). Read-only on data/raw.

Protocol: YOLO11s finds the largest vehicle box; images whose box covers <12% of the frame are
ignored (interiors, details); the box is cropped and embedded with the project fine-tuned
ConvNeXt-Tiny backbone (service/artifacts/model.pt, ArcFace-trained on car models, 768-d).
A class-balanced L2 logistic regression is trained on year-verified OLD listings of the generic
class (from reports/generation_audit.csv, produced by scripts/year_audit.py) versus the newer
class; evaluated with listing-grouped 5-fold CV and on the held-out year-verified NEW listings
found inside the generic class; then every listing gets a mean p_new and a verdict.

Usage: python scripts/generation_vote.py tracker|malibu [cache_dir]
Writes reports/generation_vote_<pair>.csv."""
import csv, sys, re, random, json
from collections import defaultdict
from pathlib import Path
import numpy as np, torch, timm
from PIL import Image
sys.stdout.reconfigure(encoding="utf-8")
import tempfile
pair = sys.argv[1]; CACHE = Path(sys.argv[2] if len(sys.argv) > 2 else tempfile.gettempdir())
GEN, NEW = {"tracker": ("chevrolet_tracker", "chevrolet_tracker_2"), "malibu": ("chevrolet_malibu", "chevrolet_malibu2")}[pair]
random.seed(0); torch.manual_seed(0)
cfg = json.load(open("service/artifacts/config.json"))
bb = timm.create_model(cfg["model_id"], pretrained=False, num_classes=0)
sd = torch.load("service/artifacts/model.pt", map_location="cpu")
bb.load_state_dict({k[len("backbone."):]: v for k, v in sd.items() if k.startswith("backbone.")}, strict=True)
bb = bb.cuda().eval(); S = cfg["img_size"]
mean = torch.tensor(cfg["mean"]).view(1, 3, 1, 1).cuda(); std = torch.tensor(cfg["std"]).view(1, 3, 1, 1).cuda()
def lid_of(n): return re.match(r"(\d+)_", n).group(1)

@torch.no_grad()
def embed_crops(cls):
    cache = CACHE / f"cnx_emb_{cls}.npz"
    files = sorted(p for p in Path("data/raw", cls).iterdir() if p.suffix.lower() in (".webp", ".jpg", ".jpeg", ".png"))
    if cache.exists():
        z = np.load(cache, allow_pickle=True)
        if list(z["names"]) == [p.name for p in files]: return z["X"], list(z["names"]), list(z["ext"])
    from ultralytics import YOLO
    det = YOLO("service/yolo11s.pt"); VEH = {2, 5, 7}
    X, names, ext = [], [], []
    for i in range(0, len(files), 32):
        batch = files[i:i+32]; res = det.predict([str(p) for p in batch], conf=0.35, verbose=False, device=0)
        tens = []
        for p, r in zip(batch, res):
            best = None
            for b in r.boxes:
                if int(b.cls) in VEH:
                    x1, y1, x2, y2 = b.xyxy[0].tolist(); a = (x2-x1)*(y2-y1) / (r.orig_shape[0]*r.orig_shape[1])
                    if best is None or a > best[0]: best = (a, x1, y1, x2, y2)
            names.append(p.name)
            if best and best[0] >= 0.12:
                a, x1, y1, x2, y2 = best; im = Image.open(p).convert("RGB"); W, H = im.size
                mx, my = 0.1*(x2-x1), 0.1*(y2-y1)
                im = im.crop((max(0, x1-mx), max(0, y1-my), min(W, x2+mx), min(H, y2+my))).resize((S, S), Image.BICUBIC)
                tens.append(torch.from_numpy(np.asarray(im).copy()).permute(2, 0, 1).float() / 255); ext.append(True)
            else: ext.append(False)
        if tens:
            t = (torch.stack(tens).cuda() - mean) / std
            f = torch.nn.functional.normalize(bb(t), dim=1)
            X += [v.cpu().numpy() for v in f]
    X = np.stack(X); np.savez(cache, X=X, names=np.array(names), ext=np.array(ext)); return X, names, ext

audit = {(r["class"], r["listing_id"]): r["verdict"] for r in csv.DictReader(open("reports/generation_audit.csv", encoding="utf-8"))}
def verdict(cls, lid): return audit.get((cls, lid), "unknown")
data = {}
for cls in (GEN, NEW):
    X, names, ext = embed_crops(cls); en = [n for n, e in zip(names, ext) if e]; data[cls] = (X, en)
    print(f"{cls}: {len(names)} images, {len(en)} exterior")
Xg, ng = data[GEN]; Xn, nn_ = data[NEW]
old_idx = [i for i, n in enumerate(ng) if verdict(GEN, lid_of(n)).startswith("old")]
test_new_idx = [i for i, n in enumerate(ng) if verdict(GEN, lid_of(n)).startswith("new")]
new_idx = [i for i, n in enumerate(nn_) if not verdict(NEW, lid_of(n)).startswith("old")]
X = np.concatenate([Xg[old_idx], Xn[new_idx]]); y = np.array([0]*len(old_idx) + [1]*len(new_idx))
groups = [lid_of(ng[i]) for i in old_idx] + [lid_of(nn_[i]) for i in new_idx]
mu, sd_ = X.mean(0), X.std(0) + 1e-6
def fit(Xtr, ytr, l2=1e-2):
    Xt = torch.tensor((Xtr - mu) / sd_, dtype=torch.float32); yt = torch.tensor(ytr, dtype=torch.float32)
    w = torch.zeros(Xt.shape[1], requires_grad=True); b = torch.zeros(1, requires_grad=True)
    opt = torch.optim.LBFGS([w, b], max_iter=200); pos = yt.mean(); wts = torch.where(yt == 1, 0.5/pos, 0.5/(1-pos))
    def closure():
        opt.zero_grad(); z = Xt @ w + b
        loss = (torch.nn.functional.binary_cross_entropy_with_logits(z, yt, reduction="none") * wts).mean() + l2 * (w*w).sum()
        loss.backward(); return loss
    opt.step(closure); return w.detach().numpy(), b.item()
def predict(w, b, Xs): return 1 / (1 + np.exp(-(((Xs - mu) / sd_) @ w + b)))
ulist = sorted(set(groups)); random.shuffle(ulist); folds = [set(ulist[k::5]) for k in range(5)]
p_cv = np.zeros(len(y))
for f in folds:
    te = np.array([g in f for g in groups]); w, b = fit(X[~te], y[~te]); p_cv[te] = predict(w, b, X[te])
lp = defaultdict(list); ly = {}
for g, p, yy in zip(groups, p_cv, y): lp[g].append(p); ly[g] = yy
old_l = [g for g in lp if ly[g] == 0]; new_l = [g for g in lp if ly[g] == 1]
print(f"CV: {len(y)} exterior images / {len(lp)} listings: image acc {((p_cv>0.5)==y).mean():.3f}; listing-vote acc "
      f"old {np.mean([np.mean(lp[g])<=0.5 for g in old_l]):.3f} ({len(old_l)}), new {np.mean([np.mean(lp[g])>0.5 for g in new_l]):.3f} ({len(new_l)})")
w, b = fit(X, y); pt = predict(w, b, Xg[test_new_idx]); tl = defaultdict(list)
for i, p in zip(test_new_idx, pt): tl[lid_of(ng[i])].append(p)
print(f"held-out year-verified NEW listings inside {GEN}: {sum(np.mean(v)>0.5 for v in tl.values())}/{len(tl)} voted new (image-level {np.mean(pt>0.5):.3f})")
rows = []
for cls, Xc, names in ((GEN, Xg, ng), (NEW, Xn, nn_)):
    p = predict(w, b, Xc); per = defaultdict(list)
    for n, pp in zip(names, p): per[lid_of(n)].append(pp)
    allf = [q.name for q in Path("data/raw", cls).iterdir() if q.suffix.lower() in (".webp", ".jpg", ".jpeg", ".png")]
    nimg = defaultdict(int)
    for n in allf: nimg[lid_of(n)] += 1
    for lid in sorted(nimg):
        ps = per.get(lid, []); m = float(np.mean(ps)) if ps else float("nan")
        vis = "no exterior shot" if not ps else "new" if m > 0.65 else "old" if m < 0.35 else "uncertain"
        rows.append({"class": cls, "listing_id": lid, "images": nimg[lid], "exterior_images": len(ps),
                     "year_verdict": verdict(cls, lid), "p_new": "" if not ps else round(m, 3), "visual": vis})
out = Path("reports") / f"generation_vote_{pair}.csv"
with out.open("w", newline="", encoding="utf-8") as f:
    wr = csv.DictWriter(f, fieldnames=list(rows[0].keys())); wr.writeheader(); wr.writerows(rows)
for cls in (GEN, NEW):
    for bucket in ("unknown", "ambiguous", "old", "new"):
        sub = [r for r in rows if r["class"] == cls and r["year_verdict"].startswith(bucket)]
        if not sub: continue
        c = defaultdict(int); im = defaultdict(int)
        for r in sub: c[r["visual"]] += 1; im[r["visual"]] += r["images"]
        print(f"  {cls:22} year={bucket:9} {len(sub):3} listings -> " + ", ".join(f"{k}:{c[k]} ({im[k]} img)" for k in ("old", "new", "uncertain", "no exterior shot") if c[k]))
print("->", out)
