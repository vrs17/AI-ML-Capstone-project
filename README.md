# Uzbek Car Model Recognizer

A two-stage computer-vision system that recognizes five Uzbek-market car models from a photo —
**Chevrolet Cobalt, Nexia 3, Spark, Gentra, Damas** — **rejects a car that is none of them**
(open-set recognition), and **only answers when it can hold high precision**, abstaining on the
hard cases. Built as the core of a real camera-analytics product (gas stations, car washes),
designed to scale to 20–50+ models.

> **Sealed-test result (6-class):** **0.950 accuracy · 0.936 macro-F1**, and **0.971 accuracy on
> the five known models**. Test ≈ validation (0.950 vs 0.949) — no overfitting.
>
> **With the trust layer:** **99.0% precision on the five known models at 83.5% coverage** —
> it rejects 8.7% of photos as unknown cars and abstains on 7.8% rather than guess.

---

## Why this project

There is no public tool — or dataset — that recognizes the specific cars common on Uzbekistan's
roads. Several of them are **near-identical sedans** (Cobalt / Gentra / Nexia 3), and a real
camera constantly sees **cars that aren't in the target list at all**. Both are handled here:
fine-grained separation via an angular-margin classifier, and an open-set `others` class plus
calibrated abstention so unknown cars are rejected rather than mislabeled.

## How it works

```mermaid
flowchart LR
    A["📷 Photo"] --> B["Stage 1 · YOLO detector<br/>(pretrained) — crop the car"]
    B --> C["Stage 2 · ConvNeXt + ArcFace<br/>(fine-tuned) @384px"]
    C --> D{"Trust layer<br/>calibrated confidence"}
    D -->|confident, a known model| E["✅ Cobalt / Nexia3 / Spark / Gentra / Damas"]
    D -->|a non-target car| F["🚗 others (reject)"]
    D -->|uncertain| G["🙋 abstain → human"]
```

- **Stage 1 — detect & crop.** A pretrained **YOLO** detector localizes the car and crops to it
  (detection is already solved; we don't waste our data re-learning it).
- **Stage 2 — classify.** A **ConvNeXt-Tiny** backbone fine-tuned with a **sub-center ArcFace
  head at 384px** names the model. ArcFace's angular margin is what separates the look-alike
  sedans; 384px exposes the sub-pixel cues (grille, lights, badge).
- **Trust layer.** Temperature scaling makes the confidences honest; a validation-chosen
  threshold lets the system **answer only when confident on a known model**, and otherwise
  **abstain**. Measured on the sealed test: **99.0% precision at 83.5% coverage**
  (T = 2.894, threshold 0.857) — see `notebooks/trust_layer.ipynb`.

## Results

| Model | Classes | Accuracy | Macro-F1 | Notes |
|---|---|--:|--:|---|
| Majority baseline | 6 | 0.279 | 0.073 | trivial floor |
| ConvNeXt-Tiny (v1) | 5 | 0.923 | 0.917 | first iteration |
| **ConvNeXt + ArcFace @384 (v2)** | **6** | **0.950** | **0.936** | sealed test; **0.971 on the 5 known** |

The Cobalt/Gentra/Nexia 3 look-alike confusion — v1's main weakness — collapsed to single digits
in v2. The weakest class is `others` (F1 0.789), the expected open-set difficulty. Full per-class
report and confusion matrix are in `notebooks/model_gate_v2.ipynb`.

## Repository layout

```
├── README.md                     ← you are here
├── SETUP.md                      executable runbook — start the project end to end
├── QUICKSTART.md                 clone → running service, and how to retrain
├── ROADMAP.md                    end-to-end methodology, each step → its artifact
├── PROJECT_STATUS.md             living status
├── data/
│   └── README.md                 dataset provenance, counts, issue log, ethics
├── docs/
│   ├── data_collection_runbook.md   how to run the scraper
│   └── manual_review.md             the human golden-review procedure
├── scripts/
│   ├── avtoelon_scraper.py          polite, robots-compliant image scraper
│   ├── deduplicate.py               hash dedup + cross-folder quarantine
│   ├── resolution_sweep.py          accuracy vs. capture size — the operating envelope
│   ├── pack_weights.py              fp16 repack so the checkpoint fits GitHub's 100 MB cap
│   └── setup.py                     one-command install + verification
├── notebooks/
│   ├── data_gate.ipynb              clean + leakage-safe split (YOLO + CLIP)
│   ├── model_gate.ipynb             v1: baselines + ConvNeXt/ResNet (5-class)
│   ├── model_gate_v2.ipynb          v2: ArcFace @384 (6-class, open-set)
│   ├── trust_layer.ipynb            calibration + 99%-precision abstention
│   └── export_onnx.ipynb            ONNX export + numerical verification
├── service/                      FastAPI backend (Docker, GPU)
│   ├── app.py  pipeline.py       photo + batch inference
│   ├── video.py  overlay.py      ByteTrack + temporal voting + in-frame detection graphics
│   └── static/                   operations console · snapshot inspector · shared theme
├── docs/index.html               showcase page · docs/demo.html in-browser demo
└── defense_*.md / capstone_*.md / final_action_plan.md   defense-prep docs
```

## Run it

Weights are committed, so a clone runs with nothing else to download:

> **⚠️ Temporary: the model weights are committed to git.**
> `service/artifacts/model.pt` (~56 MB, fp16) would normally be in `.gitignore` — binary
> weights do not belong in version control, and every future version adds another 56 MB
> to history permanently. It is committed **deliberately and temporarily** so the project
> runs straight from a clone during testing and demos. Before this repo is treated as
> long-lived, move it back out — see *Temporary decisions* in [`SETUP.md`](SETUP.md).


```bash
git clone https://github.com/vrs17/AI-ML-Capstone-project.git
cd AI-ML-Capstone-project/service
pip install -r requirements.txt      # plus torch, see QUICKSTART
uvicorn app:app --port 8000          # -> http://localhost:8000
```

Or run **`python scripts/setup.py`**, which does all of the above and verifies it.
Full instructions are in **[`SETUP.md`](SETUP.md)** (step-by-step, with every failure mode
and its fix) and **[`QUICKSTART.md`](QUICKSTART.md)** (the short version).

## Reproduce it

The full pipeline, with every stage mapped to its file, is in **[`ROADMAP.md`](ROADMAP.md)**. In short:

1. **Scrape** — `scripts/avtoelon_scraper.py` (see `docs/data_collection_runbook.md`).
2. **Deduplicate** — `scripts/deduplicate.py --src data/raw --dst data/dedup`.
3. **Data Gate** — `notebooks/data_gate.ipynb` → clean, leakage-safe split.
4. **Manual golden review** — `docs/manual_review.md` → 6-class `dataset_final.zip`.
5. **Model Gate v2** — `notebooks/model_gate_v2.ipynb` (Colab T4) → the trained model.
6. **Trust Layer** — `notebooks/trust_layer.ipynb` → calibrated, abstaining system.

The notebooks run on a free Google Colab **T4 GPU** and read the dataset zip from Google Drive.

## Data

The dataset was **collected and cleaned for this capstone** from public
[avtoelon.uz](https://avtoelon.uz) listings — no public Uzbek-market car dataset exists. It is
documented in **[`data/README.md`](data/README.md)** (provenance, counts, a full issue log, and
limitations). The **images themselves are git-ignored and not redistributed** — the repo
documents the method so the pipeline can be reproduced, not the data re-published.

Key data-quality guarantees:
- **Leakage-safe split by listing** — all photos of one car stay on one side of train/val/test
  (verified 0 cross-split listings).
- **Human-verified labels** — every crop was reviewed; non-target cars became the `others` class.

## Ethics & license

Images were collected from **public** listings for **academic/educational use only**. `robots.txt`
was honored, license plates are platform-masked, and the raw/cropped images are **not
redistributed** here. See `data/README.md` §5.

## The product surface

The service ships two screens, both served by the same FastAPI app:

- **`/` — Operations console.** The live screen: annotated video, unique-vehicle count and
  throughput, how each vehicle was handled (identified / outside catalogue / sent to staff),
  fleet mix, and a rolling decision log. It speaks in operator events, not tensors.
- **`/photo` — Snapshot inspector.** One image, with the detector's box, the verdict, the
  calibrated per-class probabilities and stage timings — the "why" view.

Real-time video adds **ByteTrack** (so it counts *unique vehicles*, not per-frame hits) and
**temporal voting**: each track is classified on several frames, every look is judged with the
calibrated threshold, and the track takes the majority of the looks that passed. Detection
graphics are burned into the frame server-side, which is what keeps them pixel-locked to a moving
car. Details in [`service/README.md`](service/README.md).

## Status & roadmap

All four gates are complete — data, model, trust layer, and delivery. The production
direction (edge deployment via MobileNetV4 + INT8, embedding-distance OOD, a prototype gallery to
scale to 20–50+ models) is outlined in `ROADMAP.md` (Stage 7). Current status lives in
`PROJECT_STATUS.md`.
