# Final Action Plan — Uzbek Car Model Recognizer

What's left between now and the defense. Ordered by priority. Check items off as you go.

---

## 🔴 Must-do before defense

- [ ] **Run the Trust Layer** (`notebooks/trust_layer.ipynb`) on the T4.
      Record: fitted temperature `T`, the ≥99%-precision threshold, and the **operating point**
      (coverage at 99% known-class precision, reject %, abstain %).
      → Then fill the "*fill after running*" cells in `capstone_evidence_matrix.md`,
      `defense_pitch_outline.md` §5, and `defense_question_bank.md` (precision-cost answer).

- [x] **Write the top-level `README.md`** (owed for C2). ✅ Done — problem, two-stage
      architecture (with diagram), headline results, reproduce steps, repo layout, ethics.

- [ ] **Prepare the presentation** using the course's PPTX. Mirror `defense_pitch_outline.md`.
      Have ready to show on demand: the **confusion matrix**, the **precision–coverage curve**,
      and the per-class report.

- [ ] **Rehearse the Q&A** from `defense_question_bank.md` — especially the **leakage**,
      **overfitting**, and **"99% precision not accuracy"** answers. Say each bold one-liner out loud.

## 🟡 Should-do (strengthens the defense)

- [ ] **Verify every notebook runs end-to-end** from a clean runtime, in order
      (data_gate → model_gate_v2 → trust_layer), so a live demo can't surprise you.
- [ ] **Cross-check the evidence matrix** against the *official rubric* in the course archive;
      rename rows to match its exact wording (the evidence stays the same).
- [ ] **Screenshot the key results** (confusion matrix, precision–coverage, MLflow runs) into the
      slides so nothing depends on a live GPU during the talk.

## 🟢 Optional (nice-to-have / shows ambition)

- [ ] **SnapMix arm** — set `RUN_SNAPMIX=True` in `model_gate_v2.ipynb` Cell 9 to test the
      FG-aware mixing experiment (~+1% possible).
- [ ] **Street-photo mini test set** — a small set of real street/camera photos to measure the
      domain gap honestly (currently a stated limitation).
- [ ] **Production spike** — export the model to ONNX + INT8 to demonstrate the edge-deployment
      path (MobileNetV4 distillation is the fuller plan in `ROADMAP.md` Stage 7).

---

## Status snapshot (keep in sync with `PROJECT_STATUS.md`)

| Stage | State |
|---|---|
| Data Gate (scrape → dedup → clean → leakage-safe split → manual golden review) | ✅ done |
| Model Gate v1 (5-class ConvNeXt, 0.923) | ✅ done |
| Model Gate v2 (6-class ArcFace @384, **0.950 / 0.936**, 0.971 on 5 known) | ✅ done |
| Trust Layer (calibration + abstention) | 🟡 notebook ready — **run + record** |
| Top-level README | ✅ done |
| Presentation + rehearsal | 🔴 to do |

**Definition of done for the defense:** trust-layer operating point recorded, README written,
slides mirror the pitch outline, and the Q&A answers are rehearsed out loud.
