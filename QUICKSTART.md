# Quickstart

Everything except the dataset images is in this repository. Pick the path you need.

**Just want it running?** `python scripts/setup.py` does steps 1 and 4 for you and checks
its own work. [`SETUP.md`](SETUP.md) is the full runbook, written so an AI agent can
execute it start to finish.

> **⚠️ Temporary: the model weights are committed to git.**
> `service/artifacts/model.pt` (~56 MB, fp16) would normally be in `.gitignore` — binary
> weights do not belong in version control, and every future version adds another 56 MB
> to history permanently. It is committed **deliberately and temporarily** so the project
> runs straight from a clone during testing and demos. Before this repo is treated as
> long-lived, move it back out — see *Temporary decisions* in [`SETUP.md`](SETUP.md).

---

## 1. Run the live service (photo + real-time video)

```bash
git clone https://github.com/vrs17/AI-ML-Capstone-project.git
cd AI-ML-Capstone-project/service

python -m venv .venv
# Windows:  .\.venv\Scripts\activate      Linux/macOS:  source .venv/bin/activate

# GPU (CUDA 12.8 — check https://pytorch.org for your CUDA version):
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
# CPU only:
# pip install torch torchvision

pip install -r requirements.txt
uvicorn app:app --port 8000
```

Open **http://localhost:8000** — the operations console. `/photo` is the snapshot
inspector, `/docs` the API reference.

Or with Docker (needs the NVIDIA container toolkit for GPU):

```bash
cd service && docker compose up --build
```

**First run downloads `yolo11s.pt`** (~19 MB) automatically, so you need internet once.
Everything after that is local.

### If it says `artifacts/config.json not found`

The trained weights are missing. They live in `service/artifacts/` — see
[§4](#4-weights) below.

---

## 2. Reproduce the model from scratch (Google Colab, free T4)

Run these in order. Each mounts Google Drive and reads `dataset_final.zip` from a
`CapstoneCars/` folder:

| # | Notebook | Produces |
|---|---|---|
| 1 | `notebooks/data_gate.ipynb` | cleaned crops + leakage-safe train/val/test split |
| 2 | `notebooks/model_gate_v2.ipynb` | the trained 6-class model (`modelgate_v2_artifacts.zip`) |
| 3 | `notebooks/trust_layer.ipynb` | temperature + abstain threshold written into `config.json` |
| 4 | `notebooks/export_onnx.ipynb` | *optional* — ONNX for the in-browser demo |

`notebooks/model_gate.ipynb` is the earlier 5-class version, kept because the v2 design
is a response to what its error analysis exposed.

**The dataset images are deliberately not in this repository.** They were collected from
public [avtoelon.uz](https://avtoelon.uz) listings for academic use and are not
redistributed. The method to rebuild them is fully documented — see
[`data/README.md`](data/README.md), [`docs/data_collection_runbook.md`](docs/data_collection_runbook.md),
[`scripts/avtoelon_scraper.py`](scripts/avtoelon_scraper.py) and
[`scripts/deduplicate.py`](scripts/deduplicate.py).

---

## 3. Measure things

```bash
# accuracy vs. how large the car arrives — the camera-placement envelope
python scripts/resolution_sweep.py --artifacts service/artifacts --data data/dataset/test

# hash dedup + cross-folder quarantine on a raw scrape
python scripts/deduplicate.py --src data/raw --dst data/dedup
```

---

## 4. Weights

`service/artifacts/model.pt` + `config.json` are committed, so a fresh clone runs
without downloading anything else. **This is a temporary convenience** — see the note at
the top of this file and *Temporary decisions* in [`SETUP.md`](SETUP.md).

They are stored in **fp16 (~56 MB)**. Not a quality trade — the service already computes
in fp16 on GPU, and `load_state_dict` casts back up for CPU (measured: identical top-1,
max logit difference 0.0008). The reason is hard: the fp32 checkpoint is ~114 MB and
**GitHub rejects any file over 100 MB**, so it cannot be committed at all.

After retraining, repack before committing:

```bash
python scripts/pack_weights.py --src /path/to/modelgate_v2_artifacts
git add -f service/artifacts/model.pt service/artifacts/config.json
git commit -m "Update trained weights"
```

---

## What's where

| Path | |
|---|---|
| `README.md` | the project, the results, the method |
| `ROADMAP.md` | every stage mapped to the file that implements it |
| `service/` | FastAPI backend + operations console ([its own README](service/README.md)) |
| `notebooks/` | the reproducible pipeline |
| `scripts/` | scraper, dedup, resolution sweep, weight packer |
| `docs/` | showcase page, in-browser demo, data runbook, manual-review procedure |
| `data/README.md` | dataset provenance, counts, issue log, ethics |
