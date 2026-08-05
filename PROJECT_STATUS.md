# Project Status

## Project
Uzbek Car Model Recognizer — a two-stage computer-vision system that identifies five
Uzbek-market car models (Cobalt, Nexia 3, Spark, Gentra, Damas) from a photo, answering
only when it can hold high precision.

## Current stage
**Data Gate (C3) — complete.** Moving to the Model Gate (C4).

## Completed
- Planning & scope: problem, two-stage architecture (YOLO detector → fine-tuned classifier), key model decisions (fine-tune ConvNeXt-Tiny; DINOv3-frozen as the second approach; 99%-precision-via-abstention).
- Repo setup: branch, `.gitignore`, scraper, data-collection runbook.
- Dataset collected from avtoelon.uz, deduplicated, and Damas expanded (+~5,000 raw) → **14,615 deduped images**.
- YOLO crop + exterior/interior filter, then CLIP label cleaning (interior via zero-shot text; wrong-model via embedding neighbors) → **9,525 clean crops**.
- **Leakage-safe split by listing** (70/15/15, stratified), verified **0 cross-split listings**. Imbalance reduced to 2.3:1.
- Reproducible `notebooks/data_gate.ipynb`, `data/README.md` + issue log, all pushed.

## Current task
Start the Model Gate: baselines, then fine-tune ConvNeXt-Tiny.

## Next
- Model Gate (C4): baselines (majority, from-scratch CNN) → fine-tune ConvNeXt-Tiny → DINOv3-frozen second approach → MLflow tracking → protected test evaluation → error analysis.
- Trust layer: calibration + precision–coverage curve for the 99%-precision abstention target.
- Post-training confident-learning pass to catch residual label noise.

## Known problems / blockers
- Residual label noise after conservative cleaning — deferred to post-training confident learning.
- Gentra is now the smallest class (1,170) — watch it in error analysis.
- Still owed for C2: top-level `README.md`.
