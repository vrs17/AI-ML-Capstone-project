# Dataset — Uzbek Car Model Recognition

Fine-grained image dataset for classifying five car models common on Uzbekistan's
roads: **Chevrolet Cobalt, Nexia 3, Spark, Gentra, and Damas**.

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

Raw listing photos → clean, model-ready crops, in four stages:

1. **Deduplication (hash-based).** Removed exact duplicate files (same car reposted).
   → **11,101** unique labeled images. 475 images that appeared under *multiple* model
   folders were quarantined as `_ambiguous` (can't be trusted to one label) and excluded.
2. **Crop + exterior filter (YOLO11s, object detection).** A COCO-pretrained detector
   finds the car (classes car/bus/truck — Damas is a microvan), crops to the largest box
   with 8% padding, and **drops photos with no car** (interiors / engine / documents /
   stock images). → **9,111** crops kept, **1,990** dropped (1,852 `no_car`, 138 `car_too_small`).
3. **Label cleaning (CLIP, semantic).** See the issue log below — catches interiors that
   survived YOLO and non-target cars sitting under the wrong label.
4. **Leakage-safe split by listing** *(pending)* — grouped so no listing spans train/test.

### Class counts (final)

The Damas class was initially under-represented (534), so ~5,000 additional raw Damas images
were scraped and merged before finalizing, deduplicated against the existing set.

| Class | Raw (deduped) | Clean (final) | train / val / test |
|---|--:|--:|:--|
| Cobalt | 3,302 | 2,161 | 1,502 / 298 / 361 |
| Nexia 3 | 2,912 | 1,935 | 1,343 / 288 / 304 |
| Spark | 2,312 | 1,523 | 1,066 / 240 / 217 |
| Gentra | 1,953 | 1,170 | 809 / 171 / 190 |
| Damas | 4,136 | 2,736 | 1,915 / 422 / 399 |
| **Total** | **14,615** | **9,525** | 2,563 listings |

Split is **70/15/15 by listing** (not by photo), stratified per class; the leakage check
confirms **0 listings appear in more than one split**. After the Damas expansion the imbalance
is a mild **2.3:1** (Damas largest, Gentra smallest).

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
- **Status:** ✅ Resolved — 14,615 deduped → 9,525 clean. Subtle residual mislabels deferred to a post-training confident-learning pass.

### Issue 4 — Class imbalance
- **Observation:** Cobalt dominated the initial scrape (~6.4:1 over Damas), mirroring real market share, leaving Damas under-represented (534).
- **Risk:** a model biased to the majority; plain accuracy is misleading; the minority test set too small to trust.
- **Decision:** scraped ~5,000 more Damas and merged → imbalance now a mild **2.3:1**; still keep class-weighted loss + **macro-F1** and per-class metrics.
- **Status:** ✅ Resolved — Damas 534 → 2,736 (80 → 757 listings); Gentra (1,170) is now the smallest class to watch.

---

## 4. Known limitations

- **Weak labels:** models are seller-declared, not expert-verified — some residual noise remains after conservative cleaning (to be reduced via confident learning after the first model trains).
- **Single-source / domain shift:** listing photos are posed and clean; real street photos differ. A separately-collected **street-photo test set** is planned to measure this gap honestly.
- **Smallest class:** after the Damas expansion, Gentra (1,170) is now the smallest class and the one to watch in error analysis.
- **Detector-based filter is imperfect:** an interior showing a car through a window can still slip through (mitigated by the CLIP interior pass).

## 5. License & ethics

Images were collected from **public** avtoelon.uz listings for **academic/educational use
only**. License plates are platform-masked. `robots.txt` was honored. Raw and cropped
images are **git-ignored and not redistributed** here — this file documents provenance and
method so the pipeline can be reproduced, not the data re-published.
