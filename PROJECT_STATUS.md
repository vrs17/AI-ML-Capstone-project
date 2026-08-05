# Project Status

## Project
Uzbek Car Model Recognizer — a two-stage computer-vision system that identifies five
Uzbek-market car models (Cobalt, Nexia 3, Spark, Gentra, Damas) from a photo, answering
only when it can hold high precision.

## Current stage
**Data Gate (C3)** — cleaning the collected dataset before modeling.

## Completed
- Planning & scope: problem, two-stage architecture (YOLO detector → fine-tuned classifier), key model decisions (fine-tune ConvNeXt-Tiny; DINOv3-frozen as the second approach; 99%-precision-via-abstention).
- Repo setup: branch, `.gitignore`, scraper, data-collection runbook.
- Dataset collected from avtoelon.uz and deduplicated → 11,101 unique labeled images.
- YOLO crop + exterior/interior filter → 9,111 clean crops.
- CLIP-based label cleaning: interior detection (zero-shot text) + wrong-model detection (embedding neighbors). See `data/README.md` issue log.

## Current task
Finalize the CLIP cleaning thresholds (conservative on wrong-model to protect look-alike
sedans), then run the leakage-safe split by listing.

## Next
- Leakage-safe train/val/test split grouped by listing id.
- Model Gate (C4): baselines → fine-tune ConvNeXt-Tiny → DINOv3-frozen second approach → MLflow tracking → protected test evaluation → error analysis.
- Post-training confident-learning pass to catch residual label noise.

## Known problems / blockers
- Residual label noise after conservative cleaning — deferred to post-training confident learning (the fine-tuned model is the better mislabel detector).
- Class imbalance (~6.4:1) — handled via class-weighted loss + macro-F1.
- Still owed for C2: top-level `README.md`.
