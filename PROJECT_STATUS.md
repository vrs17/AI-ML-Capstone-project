# Project Status

## Project
Uzbek Car Model Recognizer — a two-stage computer-vision system that identifies five
Uzbek-market car models (Cobalt, Nexia 3, Spark, Gentra, Damas) from a photo **and can
reject a car it doesn't recognize** via a sixth `others` (open-set) class, answering only
when it can hold high precision. Framed as a real product: fixed-camera analytics for gas
stations / car washes, scaling to 20–50+ models.

## Current stage
**Data Gate (C3) — complete, now 6-class open-set.** Moving to the Model Gate (C4) retrain.

## Completed
- Planning & scope: problem, two-stage architecture (YOLO detector → fine-tuned classifier), key model decisions (fine-tune ConvNeXt-Tiny; 99%-precision-via-abstention).
- Repo setup: branch, `.gitignore`, scraper, data-collection runbook.
- Dataset collected from avtoelon.uz, deduplicated, and Damas expanded (+~5,000 raw) → **14,615 deduped images**.
- YOLO crop + exterior/interior filter, then CLIP label cleaning → **9,525 clean crops**.
- **Leakage-safe split by listing** (70/15/15, stratified), verified **0 cross-split listings**.
- **First 5-class model trained** (Model Gate v1): ConvNeXt-Tiny reached **92.3% acc / 91.7% macro-F1** on the sealed leakage-free test. Error analysis exposed the open-set gap (confident labels on non-target cars, e.g. a BYD scored 0.97 as `spark`).
- **Open-set pivot + manual golden relabeling:** every crop human-verified against its folder; misfiled images corrected; non-target cars split into a new **`others`** class; junk removed. Labels upgraded from weak → human-verified golden. Dataset is now **6 classes** (`cobalt, damas, gentra, nexia3, others, spark`) in the same `train/val/test` layout.
- **2026 backbone/deployment research** (multi-agent): decision = keep ConvNeXt-Tiny for the capstone (the gain is in the training recipe, not the architecture); for production use MobileNetV4 + INT8 + embedding-distance OOD + prototype gallery to scale. Documented for the Model/Trust gates.
- Reproducible `notebooks/data_gate.ipynb`, `notebooks/model_gate.ipynb`, `notebooks/trust_layer.ipynb`; `data/README.md` + issue log (now incl. Issue 5, open-set), all pushed.

## Current task
Retrain the Model Gate on the 6-class dataset. Code already derives classes from the folders,
so it becomes 6-class automatically — the work is the recipe upgrade, not plumbing.

## Next
- **Model Gate (C4), 6-class retrain** with the research-backed recipe: **sub-center ArcFace head + 384px fine-tune + FG-aware mixing (SnapMix)** to attack the Cobalt/Gentra/Nexia 3 look-alike confusion; class-weighted loss + macro-F1; MLflow; sealed-test eval + error analysis.
- **Trust layer:** recalibrate (temperature scaling) + precision–coverage curve for the 99%-precision target, measured on the **five known models** (with `others` as the reject bucket). Add an embedding-distance OOD score as the production-facing backstop.
- **Production track (post-capstone):** MobileNetV4-Conv-Medium student distilled from a heavier teacher, INT8 via ONNX→TensorRT/OpenVINO, prototype/kNN gallery to add models without full retrains.

## Known problems / blockers
- **`others` is heterogeneous and likely smaller** than the five known classes — watch its per-split counts and per-class recall; class-weighted loss mitigates.
- **Leakage re-confirmation:** relabeling only moved images within their existing split, so leakage-safety is preserved by construction; the filename-based check should be re-run once on the uploaded 6-class set to reconfirm 0.
- **Licensing for the product:** ConvNeXtV2 weights are CC-BY-NC — the commercial build must stay on Apache/MIT backbones (MobileNetV4, EfficientNetV2, DINOv2, EVA-02). Current ConvNeXt-Tiny (V1) is fine.
- Still owed for C2: top-level `README.md`.
