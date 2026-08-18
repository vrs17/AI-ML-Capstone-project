# Capstone Evidence Matrix — Uzbek Car Model Recognizer

Maps each capstone dimension to **what we did**, the **evidence in this repo**, and the
**result**. Cross-check the left column against the *official rubric* in your course archive and
rename rows to match its exact wording where needed — the evidence stays the same.

Legend: 📓 notebook · 🐍 script · 📄 doc.

---

## Data Gate (C3)

| Dimension | What we did | Evidence | Result |
|---|---|---|---|
| Problem framing | Fine-grained + open-set vehicle ID for a real camera product | 📄 `ROADMAP.md` §Stage 0, `defense_pitch_outline.md` | Clear scope, 5 known + `others` |
| Data sourcing | Scraped public avtoelon.uz listings, robots-compliant | 🐍 `scripts/avtoelon_scraper.py`, 📄 `docs/data_collection_runbook.md` | ~14,615 deduped images |
| Deduplication | Hash dedup + cross-folder ambiguous quarantine | 🐍 `scripts/deduplicate.py` (+ `dedup_report.csv`) | 11,101 unique; 475 ambiguous excluded |
| Automated cleaning | YOLO exterior crop + CLIP interior/wrong-model cleaning | 📓 `data_gate.ipynb` (Cells 4–5), 📄 `data/README.md` | 9,525 clean crops |
| Leakage control | Split **by listing**, not by photo | 📓 `data_gate.ipynb` (Cell 6) | **0 cross-split listings** (asserted) |
| Human golden review | Verified every crop; built the open-set `others` class | 📄 `docs/manual_review.md`, `data/README.md` (Issue 5) | 6-class human-verified dataset |
| Imbalance handling | Damas expansion + class-weighted loss + macro-F1 | 📄 `data/README.md` (Issue 4) | Mild ~3.6:1; minority classes tracked |
| Documentation | Issue log (Observation→Risk→Decision→Evidence→Status) | 📄 `data/README.md` | 5 documented issues, all resolved |

## Model Gate (C4)

| Dimension | What we did | Evidence | Result |
|---|---|---|---|
| Baselines | Majority-class + from-scratch CNN | 📓 `model_gate.ipynb` (Cells 5–6), `model_gate_v2.ipynb` (Cell 5) | Establishes the floor |
| Transfer learning | Fine-tuned ConvNeXt-Tiny (freeze→unfreeze, discriminative LRs, AMP) | 📓 `model_gate.ipynb` (Cell 7) | v1: 92.3% / 91.7% (5-class) |
| Architecture comparison | ConvNeXt vs ResNet-50 vs scratch | 📓 `model_gate.ipynb` (Cells 7–9) | ConvNeXt wins on validation |
| Advanced technique 1 | **Sub-center ArcFace head** (angular margin, K=3) | 📓 `model_gate_v2.ipynb` (Cells 6, 8) | Look-alike sedan confusion → single digits |
| Advanced technique 2 | **384px** fine-tune (sub-pixel cues) | 📓 `model_gate_v2.ipynb` (Cell 3) | Part of the v2 gain |
| Augmentation choice | FG-safe (RandAug + Erasing), **not** MixUp/CutMix; optional SnapMix | 📓 `model_gate_v2.ipynb` (Cells 3, 9) | Justified, research-backed |
| Model selection | Pick winner on **validation**, sealed test **once** | 📓 `model_gate_v2.ipynb` (Cells 10–11) | ArcFace @384 selected |
| Sealed-test result | 6-class open-set evaluation | 📓 `model_gate_v2.ipynb` (Cell 11) | **0.950 acc / 0.936 macro-F1; 0.971 on 5 known** |
| Overfitting check | Compare test vs validation | val 0.949 vs test 0.950 | Test ≈ val → no overfitting |
| Error analysis | Per-class report + confusion matrix | 📓 `model_gate_v2.ipynb` (Cell 11) | `others` weakest (F1 0.789), documented |
| Experiment tracking | MLflow runs + params/metrics | 📓 `model_gate_v2.ipynb` (Cell 4), backed up | All runs logged |
| Reproducibility | Head-aware save + clean reload check | 📓 `model_gate_v2.ipynb` (Cell 12) | Reload verified |

## Trust Layer (C4/C5)

| Dimension | What we did | Evidence | Result |
|---|---|---|---|
| Calibration | Temperature scaling (Guo et al.) | 📓 `trust_layer.ipynb` (Cell 4) | ECE reported before/after |
| Selective prediction | ≥99% precision threshold on the **five known models** | 📓 `trust_layer.ipynb` (Cells 5–6) | *fill operating point after running* |
| Risk–coverage | Precision–coverage curve on sealed test | 📓 `trust_layer.ipynb` (Cell 6) | *fill after running* |
| Reliability | Reliability diagrams before/after | 📓 `trust_layer.ipynb` (Cell 7) | *fill after running* |
| Error grid | Most confident precision-breaking mistakes | 📓 `trust_layer.ipynb` (Cell 8) | *fill after running* |

## Engineering / Delivery (C2/C5)

| Dimension | What we did | Evidence | Result |
|---|---|---|---|
| Reproducible pipeline | Every automated stage is committed code | 🐍 scripts + 📓 notebooks | Runnable end-to-end |
| Traceability | Roadmap maps each step to its artifact | 📄 `ROADMAP.md` (+ "known code↔step gaps") | Narrative == repo |
| Version discipline | Meaningful commits per stage on a feature branch | git history | Clean, staged history |
| Ethics / license | Public data, robots.txt honored, plates masked, images not redistributed | 📄 `data/README.md` §5 | Documented |
| Production thinking | Edge backbone + INT8 + OOD + scaling plan | 📄 `PROJECT_STATUS.md`, `ROADMAP.md` (Stage 7) | Post-capstone roadmap |
| Top-level README | Repo entry point | ⏳ **owed** (see `final_action_plan.md`) | Pending |

---

**How to use this in the defense:** for any rubric line, point at the **Evidence** cell — open
the notebook/doc and show the exact cell or section. Every claim here is backed by something a
reviewer can click.
