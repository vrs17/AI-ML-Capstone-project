# Defense Question Bank — Uzbek Car Model Recognizer

Anticipated questions with **crisp, honest answers** grounded in this repo. Practice saying the
**bold one-liner** first, then expand only if asked. Never bluff — "we deferred that, here's why"
beats a confident wrong answer.

---

## Problem & scope

**Q: Why is this worth doing — isn't car recognition solved?**
Generic car detection is solved; **fine-grained Uzbek-market model ID is not**, and no public
dataset exists. It's the core of a real product (camera analytics for gas stations / car washes).

**Q: Why only 5 models?**
A deliberate, well-scoped start — the most common cars on Uzbek roads. The architecture is built
to scale to 20–50+ (the `others` class + the production plan handle growth).

## Architecture

**Q: Why two stages instead of one end-to-end model?**
**Detection and fine-grained classification are different jobs.** A pretrained YOLO already
localizes cars perfectly; spending our limited data on re-learning that would be wasteful. We use
YOLO to crop, then spend all our training signal on the hard part — telling the models apart.

**Q: Why fine-tune a pretrained model instead of training from scratch?**
9.5k images is far too few to train a strong vision model from zero. **Our from-scratch CNN
baseline proves it** — it scored far below the fine-tuned ConvNeXt. Transfer learning reuses
features learned from millions of images.

**Q: Why ConvNeXt-Tiny specifically?**
Strong accuracy-per-parameter, clean to fine-tune and (later) quantize. We also confirmed it beat
ResNet-50 on our validation set. We researched 2026 alternatives (EVA-02, ConvNeXtV2, MobileNetV4)
and concluded the *recipe* mattered more than the backbone at our data scale.

## Data quality (the leakage questions — expect these)

**Q: How did you prevent data leakage?**
**We split by listing, not by photo.** The same car is often posted with many photos; if some
landed in train and others in test, the model would "recognize" them and inflate the score. We
group all photos of one listing onto one side — and assert **0 cross-split listings** at runtime.

**Q: How do you know your labels are correct?**
Three passes: automated (YOLO drops non-cars, CLIP flags interiors/wrong-models), then a **human
golden review** of every crop (`docs/manual_review.md`). Labels went from weak (seller-declared)
to human-verified.

**Q: Is scraping this data ethical/legal?**
Public listings, `robots.txt` honored (only allowed paths), license plates are **platform-masked**
before upload, and we **do not redistribute** the images — the repo documents provenance only.

## Modeling & evaluation (the rigor questions)

**Q: How do you know you're not overfitting?**
**Test ≈ validation: 0.950 vs 0.949.** If we'd memorized the training data, the sealed test would
drop well below validation. It didn't. Plus early stopping on validation macro-F1, weight decay,
augmentation, and the leakage-safe split all guard against it.

**Q: Why report macro-F1 instead of accuracy?**
Classes are imbalanced. Accuracy lets a model coast on the big classes; **macro-F1 averages across
all six equally**, so it can't hide poor performance on `others` or the smaller sedans.

**Q: What did the ArcFace head actually buy you?**
It attacks the one real weakness — the near-identical sedans. Its angular margin pushes
Cobalt/Gentra/Nexia 3 apart in feature space. **Result: their cross-confusions dropped to single
digits**, and it costs nothing at inference (the margin is train-time only).

**Q: You only evaluate the test set once — why does that matter?**
Every time you look at the test set and change something, you leak information into your choices.
We selected everything on **validation** and touched the sealed test **exactly once** — so the
final number is honest.

## The precision claim (get this exactly right)

**Q: You claimed 99% precision — but the test accuracy is 95%. Explain.**
**We never claimed 99% accuracy.** The promise is **99% precision on the five known models,
achieved by abstaining.** The model answers only when confident; on uncertain cases it abstains or
routes to a human. That's the trust layer (`trust_layer.ipynb`): temperature scaling + a threshold
chosen on validation. Precision = "when it says 'Cobalt', how often is it right."

**Q: What's the cost of that precision?**
**Coverage — we answer 83.5% of photos instead of all of them.** At threshold 0.857 the sealed
test gives **99.0% precision** on known-model answers, with 8.7% rejected as unknown cars and
7.8% abstained. Without abstention it would answer 91.3% at only 96.1% precision — so we trade
about 8 points of coverage for 3 points of precision. In this product a wrong model name
corrupts the analytics, while a skipped photo just isn't counted.

## Open-set

**Q: Why a 6th `others` class instead of just softmax over 5?**
A closed-set softmax has **nowhere to put an unknown car** — it forced foreign makes into one of
five (a BYD scored 0.97 as a Spark). The `others` class gives the model a place to say "not one of
ours," which is essential for a real camera.

**Q: Why is `others` your weakest class (F1 0.789)?**
Because it's **not one thing** — it's every non-target car (BYD, Lada, Kia…), so it's the hardest
to model and the smallest class. That's the expected open-set difficulty, and the abstention layer
catches the confident `others`↔known errors.

**Q: How would you improve `others` / scale to 50 models?**
For production: move from a fixed softmax `others` to **embedding-distance OOD** (kNN/Mahalanobis
on the ArcFace features) and a **prototype gallery** — adding a new model becomes enrolling a few
crops, no full retrain.

## Reproducibility & honesty

**Q: Can someone reproduce your pipeline?**
Yes — `ROADMAP.md` maps every stage to a committed script/notebook. The one non-code step (the
human review) is written up as a procedure in `docs/manual_review.md`. We also list the two
"code↔step gaps" openly rather than hiding them.

**Q: What are the biggest limitations?**
(1) `others` is the weak class; (2) single-source domain shift — listing photos are clean/posed,
real street photos differ, so a street-photo test set is planned; (3) labels verified by one
annotator, not a panel.

**Q: What would you do next / differently?**
Run the trust layer to lock the operating point, collect a street test set to measure the domain
gap, and start the production distillation (MobileNetV4 + INT8) for camera deployment.

---

### Traps to avoid
- Don't say "99% accuracy." Say **"99% precision on the five known models, via abstention."**
- Don't claim the data is perfectly clean — say it's **human-verified with documented residual limits.**
- Don't oversell `others` — own that it's the hardest class and explain *why*.
