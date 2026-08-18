# Dataset — Uzbek Car Model Recognition

Fine-grained image dataset for recognizing five car models common on Uzbekistan's
roads — **Chevrolet Cobalt, Nexia 3, Spark, Gentra, Damas** — **plus a sixth
`others` class** for any car that is none of the five. That sixth class turns the
system from *closed-set* (must force every photo into one of five) into *open-set*
(can say "this is a car we don't recognize"), which is what a real deployment on
gas-station / car-wash cameras needs.

No public dataset of Uzbek-market cars exists, so this dataset was **collected and
cleaned for this capstone**. It is documented here for reproducibility; the images
themselves are **not redistributed** in this repository (see *License & ethics*).

---

## 1. Source & collection

| | |
|---|---|
| **Source** | Public vehicle listings on [avtoelon.uz](https://avtoelon.uz) (Uzbekistan's largest car marketplace) |
| **Labels** | The seller-declared make/model of each listing (a *weak* label — see limitations) |
| **Method** | A polite, browser-based scraper (`scripts/avtoelon_scraper.py`): bounded parallelism, rate-limited, resumable |
| **Compliance** | `robots.txt` honored — only clean listing paths + the explicitly whitelisted `?page=` pagination; the disallowed `?price-currency=` query was stripped |
| **Privacy** | License plates are auto-masked by the platform before upload |

Each image filename keeps its **listing id** (`<listing_id>_<n>.jpg`) — this is required
for the leakage-safe split (all photos of one car stay on the same side of the split).

## 2. Processing pipeline

Raw listing photos → clean, model-ready crops, in five stages:

1. **Deduplication (hash-based).** Removed exact duplicate files (same car reposted).
   → **11,101** unique labeled images. 475 images that appeared under *multiple* model
   folders were quarantined as `_ambiguous` (can't be trusted to one label) and excluded.
   Reproducible via [`scripts/deduplicate.py`](../scripts/deduplicate.py) (content hash +
   cross-folder quarantine, writes a per-file `dedup_report.csv`).
2. **Crop + exterior filter (YOLO11s, object detection).** A COCO-pretrained detector
   finds the car (classes car/bus/truck — Damas is a microvan), crops to the largest box
   with 8% padding, and **drops photos with no car** (interiors / engine / documents /
   stock images). → **9,111** crops kept, **1,990** dropped (1,852 `no_car`, 138 `car_too_small`).
3. **Label cleaning (CLIP, semantic).** See the issue log below — catches interiors that
   survived YOLO and non-target cars sitting under the wrong label.
4. **Leakage-safe split by listing.** Grouped so no listing spans train/val/test
   (70/15/15, stratified) — verified **0 cross-split listings**.
5. **Manual golden relabeling + open-set `others` class.** Every crop was
   **human-verified against its folder** (see Issue 5): correctly-placed images kept,
   misfiled ones moved to the right model, and cars that are **none of the five** moved
   into a new **`others`** folder. Non-car frames that slipped through were removed.
   This upgrades the labels from *weak (seller-declared)* to *human-verified golden*
   and produces the final **6-class** dataset the models train on.

### Class counts

The Damas class was initially under-represented (534), so ~5,000 additional raw Damas images
were scraped and merged before finalizing, deduplicated against the existing set.

**After the automated pipeline (stages 1–4), 5-class clean crops:**

| Class | Raw (deduped) | Clean crops | train / val / test |
|---|--:|--:|:--|
| Cobalt | 3,302 | 2,161 | 1,502 / 298 / 361 |
| Nexia 3 | 2,912 | 1,935 | 1,343 / 288 / 304 |
| Spark | 2,312 | 1,523 | 1,066 / 240 / 217 |
| Gentra | 1,953 | 1,170 | 809 / 171 / 190 |
| Damas | 4,136 | 2,736 | 1,915 / 422 / 399 |
| **Total** | **14,615** | **9,525** | 2,563 listings |

The automated split is **70/15/15 by listing** (not by photo), stratified per class; the leakage
check confirms **0 listings appear in more than one split**. Imbalance across the five is a mild
**2.3:1** (Damas largest, Gentra smallest).

**After the manual golden-relabel pass (stage 5), 6-class dataset:** the human verification
reassigned misfiled crops and split off the new `others` class. Because relabeling only moved
images **within their existing split**, the by-listing leakage-safety from stage 4 is preserved by
construction (re-confirmable with the filename-based check in the notebook). The exact
post-relabel per-class counts are those of the uploaded `dataset_split.zip` and are printed by the
Model Gate's class-count cell at train time; `others` is the newest and most heterogeneous class
(many non-target makes), so it is watched in error analysis alongside Gentra.

---

## 3. Issue log

*Observation → Risk → Decision → Evidence → Status*

### Issue 1 — Listing galleries mix non-exterior photos
- **Observation:** each listing includes interior, dashboard, engine, and document shots alongside exterior photos.
- **Risk:** non-exterior images inherit the model label → training noise, unfair evaluation.
- **Decision:** use a pretrained YOLO detector as an exterior filter — no car box ⇒ drop; otherwise crop to the car.
- **Evidence:** 1,852 photos dropped as `no_car`; drop-reason table + sample grids in `notebooks/`.
- **Status:** ✅ Resolved (majority). Residual interiors handled by Issue 3.

### Issue 2 — Same car reposted across listings
- **Observation:** identical photos recur across multiple listings.
- **Risk:** data leakage — the same car in both train and test inflates accuracy.
- **Decision:** hash-based deduplication, **plus** a split grouped by listing id (not by photo).
- **Evidence:** unique images per hash; cross-model duplicates quarantined; final split grouped by listing id with a runtime assertion of 0 cross-split listings.
- **Status:** ✅ Resolved — dedup + leakage-safe split by listing (verified 0 leakage).

### Issue 3 — Surviving interiors + wrong-model contamination *(the main data-quality problem)*
- **Observation:** after YOLO, some interiors remained (a car seen through a window), and
  some crops were the **wrong car** — non-target models (e.g. Cruze, BYD) pulled in from
  "related ad" widgets on a listing page, mislabeled as one of our five classes.
- **Risk:** label noise caps achievable precision and corrupts the evaluation.
- **Decision — data-centric cleaning with CLIP (image–text embeddings):**
  - **Interiors:** zero-shot text matching — score each crop against *"a car from outside"*
    vs *"the inside of a car: dashboard, seats"*; flag the interiors.
  - **Wrong model:** embed every crop; for each, check its 10 nearest neighbors — if most
    carry a *different* label, flag it.
  - **Conservative threshold** on the wrong-model flag (neighbor-agreement `< 0.15`, not `0.3`):
    generic CLIP cannot separate genuine look-alike sedans (a real Gentra resembles a Cobalt),
    so a loose cutoff would delete *correct* hard examples. We remove only clear outliers now
    and **defer subtle mislabels to post-training confident learning**, where the fine-tuned
    model — which actually distinguishes the five models — is the better judge.
- **Evidence:** CLIP flagged and removed interiors + low-agreement (wrong-model) crops per class
  (e.g. final run removed 428 interior + 62 wrong-model from Cobalt); `clean_scores.csv` /
  `split_manifest.csv` record every keep/remove decision (nothing deleted blind).
- **Status:** ✅ Resolved — 14,615 deduped → 9,525 clean. The subtle residual mislabels that were
  deferred here were later cleared by the **manual golden-relabel pass** (Issue 5), which replaced
  the planned automated confident-learning step with a stronger human-in-the-loop verification.

### Issue 4 — Class imbalance
- **Observation:** Cobalt dominated the initial scrape (~6.4:1 over Damas), mirroring real market share, leaving Damas under-represented (534).
- **Risk:** a model biased to the majority; plain accuracy is misleading; the minority test set too small to trust.
- **Decision:** scraped ~5,000 more Damas and merged → imbalance now a mild **2.3:1**; still keep class-weighted loss + **macro-F1** and per-class metrics.
- **Status:** ✅ Resolved — Damas 534 → 2,736 (80 → 757 listings); Gentra (1,170) is the smallest of the five; the new `others` class is now also watched.

### Issue 5 — Closed-set model is over-confident on non-target cars → open-set `others` class
- **Observation:** the first 5-class ConvNeXt (92.3% acc / 91.7% macro-F1 on the sealed test) was a strong *closed-set* classifier, but error analysis showed it assigning **high-confidence** labels to cars that aren't in our five at all (e.g. a BYD scored 0.97 as `spark`). A closed-set softmax has *nowhere* to put an unknown car, so it forces one of five — exactly the wrong behavior for a camera in the field.
- **Risk:** in production this silently miscounts foreign makes as one of our models, corrupting the business analytics; residual mislabeled non-target crops (BYD, Lada, Cruze, Malibu) also still sat under the five labels.
- **Decision — two moves:**
  - **Manual golden relabeling:** every crop was human-verified against its folder (an eye-run pass over the split). Correctly-placed images kept; misfiled ones moved to the correct model; unusable frames removed.
  - **Open-set `others` class:** cars that are genuinely none of the five were collected into a new `others` folder, giving the model a place to reject unknowns. The look-alike sedan trio (Cobalt/Gentra/Nexia 3) was reviewed conservatively — genuinely ambiguous sedans set aside rather than guessed.
- **Evidence:** the dataset is now **6 classes** (`cobalt, damas, gentra, nexia3, others, spark`) in the same leakage-safe `train/val/test` layout; the contamination that Issue 3 flagged became the *seed* of `others` rather than being discarded.
- **Status:** ✅ Resolved for the capstone — labels are now human-verified golden and the model can abstain to `others`. *Validated:* the 6-class model (sub-center ArcFace @384, `notebooks/model_gate_v2.ipynb`) reached **0.950 acc / 0.936 macro-F1** on the sealed test with **0.971** accuracy on the five known models; `others` is the hardest class (F1 0.789), exactly the expected open-set difficulty. *Production note:* at 20–50+ models a fixed softmax `others` class becomes brittle, so the scaled system will move to embedding-distance OOD (kNN / Mahalanobis) on top of the same trust layer; the `others` class remains the capstone's demonstration of open-set behavior.

---

## 4. Known limitations

- **Labels now human-verified (golden), but from a single annotator:** the seller-declared labels were hand-verified in the manual relabel pass (Issue 5), so residual mislabels are now minimal — but verification was done by one person, not a panel, so rare subjective calls on the look-alike sedans may remain.
- **`others` is heterogeneous:** the open-set class mixes many different non-target makes, so its internal distribution is broad and its examples are fewer/less balanced than the five known models — watched in error analysis, and handled by class-weighted loss.
- **Single-source / domain shift:** listing photos are posed and clean; real street/camera photos differ. A separately-collected **street-photo test set** is planned to measure this gap honestly.
- **Smallest known class:** among the five, Gentra (1,170) remains the smallest and the one to watch alongside `others`.
- **Detector-based filter is imperfect:** an interior showing a car through a window can still slip through (mitigated by the CLIP interior pass **and** the manual relabel).

## 5. License & ethics

Images were collected from **public** avtoelon.uz listings for **academic/educational use
only**. License plates are platform-masked. `robots.txt` was honored. Raw and cropped
images are **git-ignored and not redistributed** here — this file documents provenance and
method so the pipeline can be reproduced, not the data re-published.
