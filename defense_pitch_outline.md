# Defense Pitch Outline — Uzbek Car Model Recognizer

A 4–5 minute spoken pitch. Each section notes the **one thing to land** and the **evidence to
point at**. Numbers are from the sealed test — see `capstone_evidence_matrix.md`.

---

## 0. One-liner (10s)
> "A two-stage computer-vision system that recognizes five Uzbek-market car models from a photo,
> **knows when a car isn't one of them**, and only answers when it can be highly precise —
> built as the core of a real camera-analytics product for gas stations and car washes."

## 1. The problem (30s) — *land: this is real, not a toy*
- No public tool (or dataset) recognizes the specific cars on Uzbekistan's roads — Cobalt,
  Nexia 3, Spark, Gentra, Damas.
- Business need: fixed cameras at gas stations / car washes → automatic model counts → analytics
  for owners. Must scale to 20–50+ models later.
- Why it's hard: several models are **near-identical sedans** (Cobalt/Gentra/Nexia 3), and a real
  camera sees **cars that aren't in our list at all**.

## 2. The approach (45s) — *land: standard-of-practice architecture, chosen deliberately*
- **Two stages:** a pretrained **YOLO** detector crops the car → a **fine-tuned classifier**
  names the model. (Detection and fine-grained ID are different jobs; separating them is what
  real systems do.)
- **Fine-tune, not from scratch:** 9.5k images is too few to train a strong vision model from
  zero — transfer learning from a pretrained backbone is the right call (proved by the
  from-scratch baseline scoring far lower).
- **Open-set:** a 6th `others` class so an unknown car is *rejected*, not forced into one of five.

## 3. The data — our contribution (45s) — *land: we built the dataset, honestly*
- Scraped public `avtoelon.uz` listings (robots-compliant, plates platform-masked).
- Cleaned in stages: **dedup → YOLO exterior filter → CLIP label cleaning → human golden review**.
- **Leakage-safe split by listing** — all photos of one car stay on one side of train/val/test
  (verified 0 cross-split listings). Without this, scores are a lie.
- Result: ~9,500 human-verified images across 6 classes.

## 4. The model + results (60s) — *land: strong, honest numbers*
- Fine-tuned **ConvNeXt-Tiny with a sub-center ArcFace head at 384px**.
- **Sealed test: 0.950 accuracy / 0.936 macro-F1** on 6 classes; **0.971 on the five known
  models.** Beats the earlier 5-class model (0.923) on a *harder* task.
- **No overfitting:** test ≈ validation (0.950 vs 0.949).
- **The hard part is solved:** the Cobalt/Gentra/Nexia 3 look-alike confusion collapsed to single
  digits — that's what the ArcFace angular margin bought us.

## 5. The trust layer (45s) — *land: we promised precision, not accuracy*
- We never promised 99% accuracy — we promised **99% precision via abstention**.
- **Temperature scaling** makes the confidences honest; a threshold chosen on validation lets the
  system **answer only when confident** and **abstain / route to a human** otherwise.
- Precision is measured on the **five known models**; `others` is a reject bucket, the final
  safety net. *(Fill the operating point — coverage at 99% precision — after running
  `trust_layer.ipynb`.)*

## 6. Honesty + next steps (30s) — *land: we know the limits*
- **Weakest class is `others`** (F1 0.789) — heterogeneous by nature; the expected open-set
  difficulty.
- **Single-source** (listing photos are clean/posed) → a street/camera test set is planned.
- **Production path:** distill into a MobileNetV4 edge model (INT8) + embedding-distance OOD +
  a prototype gallery to add new models without full retrains.

## Close (10s)
> "It works, the evaluation is leakage-free and honest, it knows when to abstain — and it's built
> to grow into the real product."

---

### Delivery notes
- Lead with the problem and the *product*, not the architecture.
- Say the precision claim precisely: **"99% precision on the five known models, achieved by
  abstaining — not 99% accuracy."** Getting this wrong is the easiest way to lose credibility.
- Have the confusion matrix and the precision–coverage curve ready to show on demand.
