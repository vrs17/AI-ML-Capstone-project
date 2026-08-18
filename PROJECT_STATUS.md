# Project Status

## Project
Uzbek Car Model Recognizer — a two-stage computer-vision system that identifies five
Uzbek-market car models (Cobalt, Nexia 3, Spark, Gentra, Damas) from a photo **and can
reject a car it doesn't recognize** via a sixth `others` (open-set) class, answering only
when it can hold high precision. Framed as a real product: fixed-camera analytics for gas
stations / car washes, scaling to 20–50+ models.

## Current stage
**Model Gate (C4) — complete (v2 trained).** Sub-center ArcFace @384 wins: **0.950 acc /
0.936 macro-F1** on the sealed 6-class test, **0.971 accuracy on the five known models**.
Moving to the Trust Layer (calibration + abstention).

## Completed
- Planning & scope: problem, two-stage architecture (YOLO detector → fine-tuned classifier), key model decisions (fine-tune ConvNeXt-Tiny; 99%-precision-via-abstention).
- Repo setup: branch, `.gitignore`, scraper, deduplication script, data-collection runbook.
- Dataset collected from avtoelon.uz, deduplicated, and Damas expanded (+~5,000 raw) → **14,615 deduped images**.
- YOLO crop + exterior/interior filter, then CLIP label cleaning → **9,525 clean crops**.
- **Leakage-safe split by listing** (70/15/15, stratified), verified **0 cross-split listings**.
- **First 5-class model trained** (Model Gate v1): ConvNeXt-Tiny reached **92.3% acc / 91.7% macro-F1** on the sealed leakage-free test. Error analysis exposed the open-set gap (confident labels on non-target cars, e.g. a BYD scored 0.97 as `spark`).
- **6-class model trained** (Model Gate v2, `model_gate_v2.ipynb`): **sub-center ArcFace @384** beat the plain-softmax @384 baseline on validation and reached **0.950 acc / 0.936 macro-F1** on the sealed test — better than v1 on a *harder* (open-set) task. Test ≈ validation (0.950 vs 0.949) → no overfitting. The Cobalt/Gentra/Nexia 3 look-alike confusion collapsed to single digits (all three F1 ≥ 0.953); `others` is the weakest class (F1 0.789, the expected open-set difficulty). Accuracy on the five known models = **0.971**.
- **Open-set pivot + manual golden relabeling:** every crop human-verified against its folder; misfiled images corrected; non-target cars split into a new **`others`** class; junk removed. Labels upgraded from weak → human-verified golden. Dataset is now **6 classes** (`cobalt, damas, gentra, nexia3, others, spark`) in the same `train/val/test` layout.
- **2026 backbone/deployment research** (multi-agent): decision = keep ConvNeXt-Tiny for the capstone (the gain is in the training recipe, not the architecture); for production use MobileNetV4 + INT8 + embedding-distance OOD + prototype gallery to scale. Documented for the Model/Trust gates.
- Reproducible `notebooks/data_gate.ipynb`, `notebooks/model_gate.ipynb`, `notebooks/trust_layer.ipynb`; `data/README.md` + issue log (now incl. Issue 5, open-set), all pushed.

## Current task
Run `notebooks/trust_layer.ipynb` (updated for the ArcFace v2 model) on the T4: temperature
scaling → the ≥99%-precision threshold on the **five known models** (validation) → apply once
to the sealed test → precision–coverage curve + reliability diagrams. Record the operating point
(coverage at 99% precision).

## Next
- **Run the Trust Layer** and record the operating point (threshold, coverage at 99% known-class precision, abstain/reject rates).
- **Production track (post-capstone):** add an embedding-distance OOD score as the production-facing backstop; distill into a MobileNetV4 edge student (INT8) with a prototype/kNN gallery to scale to 20–50+ models.
- **Defense prep:** presentation + rehearse the Q&A (`defense_*.md`). Top-level `README.md` ✅ done.
- **Production track (post-capstone):** MobileNetV4-Conv-Medium student distilled from a heavier teacher, INT8 via ONNX→TensorRT/OpenVINO, prototype/kNN gallery to add models without full retrains.

## Known problems / blockers
- **`others` is heterogeneous and likely smaller** than the five known classes — watch its per-split counts and per-class recall; class-weighted loss mitigates.
- **Leakage re-confirmation:** relabeling only moved images within their existing split, so leakage-safety is preserved by construction; the filename-based check should be re-run once on the uploaded 6-class set to reconfirm 0.
- **Licensing for the product:** ConvNeXtV2 weights are CC-BY-NC — the commercial build must stay on Apache/MIT backbones (MobileNetV4, EfficientNetV2, DINOv2, EVA-02). Current ConvNeXt-Tiny (V1) is fine.
- Still owed for C2: top-level `README.md`.
