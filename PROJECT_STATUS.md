# Project Status

## Project
Uzbek Car Model Recognizer — a two-stage computer-vision system that identifies five
Uzbek-market car models (Cobalt, Nexia 3, Spark, Gentra, Damas) from a photo **and can
reject a car it doesn't recognize** via a sixth `others` (open-set) class, answering only
when it can hold high precision. Framed as a real product: fixed-camera analytics for gas
stations / car washes, scaling to 20–50+ models.

## Current stage
**Trust Layer — complete. The headline promise is delivered.** On the sealed test the system
holds **99.0% precision on the five known models at 83.5% coverage** (temperature T = 2.894,
abstain threshold 0.857). Remaining work is presentation, not modeling.

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
- **Trust Layer complete:** T = 2.894, threshold 0.857 → **99.0% precision on the five known models at 83.5% coverage** on the sealed test (8.7% rejected as `others`, 7.8% abstained; 96.1% @ 91.3% without abstention).
- Showcase page + in-browser ONNX demo in `docs/`; defense docs (`defense_*.md`, `capstone_evidence_matrix.md`, `final_action_plan.md`).
- Reproducible `notebooks/data_gate.ipynb`, `notebooks/model_gate.ipynb`, `notebooks/model_gate_v2.ipynb`, `notebooks/trust_layer.ipynb`, `notebooks/export_onnx.ipynb`; `data/README.md` + issue log (now incl. Issue 5, open-set), all pushed.

## Current task
Defense preparation: build the slides from `defense_pitch_outline.md` and rehearse
`defense_question_bank.md`. Optionally export the model (`notebooks/export_onnx.ipynb`) and
publish the showcase + live demo in `docs/` via GitHub Pages.

## Next
- **Publish** `docs/` on GitHub Pages; fill the student name in the showcase footer.
- **Production track (post-capstone):** MobileNetV4-Conv-Medium student distilled from a heavier
  teacher, INT8 via ONNX→TensorRT/OpenVINO, embedding-distance OOD, and a prototype/kNN gallery
  so new models can be added without full retrains.

## Known problems / blockers
- **`others` is heterogeneous and likely smaller** than the five known classes — watch its per-split counts and per-class recall; class-weighted loss mitigates.
- **Leakage re-confirmation:** relabeling only moved images within their existing split, so leakage-safety is preserved by construction; the filename-based check should be re-run once on the uploaded 6-class set to reconfirm 0.
- **Licensing for the product:** ConvNeXtV2 weights are CC-BY-NC — the commercial build must stay on Apache/MIT backbones (MobileNetV4, EfficientNetV2, DINOv2, EVA-02). Current ConvNeXt-Tiny (V1) is fine.
