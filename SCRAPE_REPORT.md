# Scrape Report — avtoelon.uz collection run (2026-08-27, overnight)

Status: **IN PROGRESS** — this file is updated as phases complete.

## Phase 0 — scraper verification against the live site (gate)

The `select_gallery()` fix had only ever been tested against mock HTML. Auditing against
the live site (`--audit 5`, downloads nothing) and hand-counting galleries in a real
browser found **every checked listing wrong**, three distinct ways:

| Listing | Real gallery (hand-counted) | kept (before fix) | kept (after fix) |
|---|---|---|---|
| [gentra 7440799](https://avtoelon.uz/a/show/7440799) | 17 | 19 ✗ | **17** ✓ |
| [damas 7488478](https://avtoelon.uz/a/show/7488478) | 6 | 8 ✗ | **6** ✓ |
| [cobalt 7493388](https://avtoelon.uz/a/show/7493388) | 0 (posted with no photos) | 1 ✗ (a *different car's* thumbnail) | **0** ✓ |
| [nexia3 7461205](https://avtoelon.uz/a/show/7461205) | 13 | — (fresh sample) | **13** ✓ |

"Real gallery" was counted three independent ways per listing (thumbnail-strip `<li>`
count, gallery-block distinct photos, dominant CDN directory) and all agreed.

### Bugs found and fixed (commit `0b1cc04`)

1. **Social-share buttons counted as photos (+2 on every listing).** The VK and OK share
   buttons embed the listing's first photo URL percent-encoded inside their `href`
   (`https://vk.com/share.php?...image=...kcdn.online...1-full.webp`). The photo-host
   check was a substring test over the whole URL, so each share button formed a bogus
   1-photo "lightbox" group. Fix: the extracted URL's **own hostname** must be the photo
   CDN (`urlparse(u).netloc`).

2. **Photo-less listings saved a stranger's car.** A listing posted without photos still
   shows kcdn thumbnails — the "similar listings" sidebar. With no lightbox/og signal,
   the unconditional "largest photo group" fallback kept one recommendation thumbnail,
   i.e. a *different car* labelled as this model. Fix: the fallback now requires a
   clearly dominant group (≥3 photos); otherwise the listing keeps nothing.

3. **`--discover` counted 0 listings for every model.** `COUNT_RE` allowed a digit-less
   match, so it hit the nav text "Мои объявления" (which precedes the real "Найдено N
   объявлений" line), captured only whitespace → 0 — and the successful-but-empty match
   also suppressed the card-count fallback. Fix: the number must start with a digit.
   Verified: Cobalt now counts 2 965 listings (matches the page by eye).

4. **`UnicodeEncodeError` writing CSVs on Windows.** File opens defaulted to cp1252,
   which cannot encode the Cyrillic page titles; `discovered_models.csv` died on its
   first data row. Fix: explicit `encoding="utf-8"` on all four CSV opens.

Also hardened while in there:
- the zero-candidate report dict now carries all keys the audit printer uses;
- the manifest is appended **per listing** rather than per model, so a crash costs at
  most the listings in flight and resume loses nothing.

**Gate result: PASSED** — 4/4 hand-verified listings match exactly after the fix.

### Environment note

Playwright's bundled Chromium cannot launch on this machine (`spawn UNKNOWN`; real cause:
missing Microsoft VC++ x64 Redistributable → "side-by-side configuration is incorrect").
All runs therefore use the installed Chrome via `--channel chrome`. The gallery logic is
browser-independent; the audit above validates it end-to-end in this exact configuration.

## Phase 1 — model selection

**Interrupted by site unreachability — event log:**

- ~00:08 — first `--discover 60` run launched; the tool-runner backgrounded it at its
  10-minute timeout and the job stalled (near-zero CPU, browser gone, no CSV). Killed at 00:24.
- ~00:25 — relaunched unbuffered with logging. `robots.txt` read timed out (urllib is
  bot-blocked; expected), then `https://avtoelon.uz/avto/` itself returned
  `ERR_CONNECTION_TIMED_OUT` twice (45s timeout + 60s polite backoff + retry).
- Diagnosis: kun.uz (also UZ-hosted) loads fine from this machine; avtoelon.uz specifically
  refuses connections — consistent with a temporary IP-level throttle after the evening's
  audits plus the stalled run's idle browser sessions.
- Response per the mission's rules: **all scraping stopped.** A gentle probe (one homepage
  load) runs every 10 minutes; collection resumes only when the site answers again. No
  partial data was lost — no images had been downloaded yet (`data/raw/` holds only the two
  audit/discovery CSVs).

**Site recovered ~02:06** (probe loop; block lasted ~95 min). Discovery then ran clean at
gentler settings (concurrency 2, delay 1.5s): all 59 catalogue models ranked with real
counts — and Cobalt's 2,964 matches the "Найдено 2 965" I had read off the live page by
eye, validating the fixed counter.

**Class list: 31 models** (`data/raw/models_config.py`), selected by
`scripts/curate_models.py` with every decision recorded in `data/raw/curated_models.csv`:

- **Alias merge (live-verified):** the site serves one listing pool per model *family* —
  `/avto/chevrolet/gentra/` and `/avto/chevrolet/lacetti/` return the identical 4,452
  listings (same first-page IDs, same order); matiz == matiz-best likewise. Aliases and
  Daewoo/Chevrolet badge twins are merged so one visual class is one label: 6 catalogue
  entries dropped this way.
- **Volume floor 100 listings:** 22 models dropped (Sportage 99 down to VAZ-2101 54) —
  below ~100 live listings even a partial sample is too thin to be a usable class. The
  2,000-image target is reachable for roughly the top 14 classes (400+ listings); classes
  15–31 will land below target and the report will state the real per-model counts.
- **Keyword guard fixed for digit models:** single-token slugs (nexia3) never appear in
  real titles ("Chevrolet Nexia 3") — spelled-out Latin + Cyrillic forms generated for
  every class (нексия 3, кобальт, ларгус/largus for Lada R90, song plus, к5…).

## Phase 2 addendum — stopping logic live-tested

`--only chevrolet_cobalt --target-images 12 --max-pages 1`: stopped at 15/12 (in-flight
overshoot only, 26 listings skipped), 21 files on disk with listing-id filenames, hashed
manifest rows with `-full.webp` URLs; re-run reported "target met (21/12) — skipping"
without crawling pagination. A downloaded photo was opened and verified: full-resolution
exterior shot of a Cobalt LTZ (badge legible).

## Phase 3 — collection

**Launched ~02:13** — 31 models, `--target-images 2000 --max-pages 30 --concurrency 3`,
politeness settings untouched (2s listing hold, 0.4s image spacing). Running headless in
the background with a log watchdog (crashes, backoffs, per-model results). Disk before
run: 253 GB free.

**02:28 — proactive slowdown.** After ~330 listings (~1,840 images in ~15 min), timeouts
began clustering: 3 listings failed even after their 60s backoff retry, including a QUIC
protocol error — the same pattern that preceded the evening's 95-minute block. Per the
mission rule ("if the site starts timing out, back off and slow down further") the run was
stopped gracefully (per-listing manifest writes mean nothing was lost: 1,822 gentra +
21 cobalt images banked), given a cooldown, and resumed at half pressure
(`--concurrency 2 --delay 2`). ETA roughly doubles; surviving the night beats speed.

The first model also demonstrated the purity guard working on the site's pooled listings:
80 lacetti-titled cars were SKIPped out of the gentra crawl (the site serves one combined
gentra/lacetti pool; the title guard keeps the class clean), and photo-less listings now
correctly yield 0 images instead of a recommendation thumbnail.

## Phase 2 — code changes for the run

- `--target-images N`: stops each model once N images are stored on disk. Resume-aware —
  images already recorded in the manifest count toward the target; a model at target is
  skipped before its pagination is even crawled. Re-checked before each listing so the
  run stops launching new listings the moment the target is reached (overshoot bounded
  by the few listings in flight).
- `--only m1,m2`: restricts a run to a subset of MODELS keys (testing / re-runs).
- Politeness settings untouched: `LISTING_SETTLE = 2.0`, `IMG_DOWNLOAD_DELAY = 0.4`,
  default concurrency 3.

## Phase 3 — collection

_(pending)_

### Overnight timeline (the honest version)

| When | Event |
|---|---|
| ~23:4x–00:0x | Phase 0: bugs found on live site, fixed, re-audited — 4/4 match |
| ~00:20 | **Block 1** begins (~95 min): site refuses all connections |
| ~02:06 | Recovery; discovery ranks all 59 models with real counts |
| ~02:1x | 31-class list curated (alias pools verified live); stop-logic live-tested |
| 02:13 | **Collection launched** (conc 3) — 1,843 images in 15 min |
| 02:28 | Timeout cluster → proactive stop. **Block 2** (~2.5 h) |
| 04:57 | Auto-resume at conc 2 / delay 2 — gentra completes at 2,004 ✓ |
| ~05:25 | Second timeout pair → stop per trigger. **Block 3** begins; cobalt at 1,348 |
| 05:36→ | Auto-resume chain probing every 10 min; on recovery continues at conc 1 / delay 3 |

Banked so far (all resumable, per-listing manifest): **gentra 2,004 (target met) ·
cobalt 1,348 · total ≈ 3,352 images**, zero data loss across three interruptions.

**Assessment:** the site applies an escalating night-time IP throttle triggered by
sustained crawl volume (blocks: ~95 min → ~150 min → ongoing), independent of our
politeness settings' per-request gentleness. The run therefore continues into the day at
minimum footprint (one page at a time, 3s delays), auto-resuming after each block. At
~600 listings/hour the remaining 29 models need roughly 10 further crawl-hours — real
completion will land during the day, not by morning. Every stop/resume is automatic;
partial data stays intact and honest per-model counts will be reported when the run ends.

## Scope change (2026-08-31)

Five classes — cobalt, spark, damas, gentra, nexia3 — were already covered elsewhere, so
they were excluded from further collection. The exclusion **cascades to the same car under
other labels**, which matters more than the flag itself: excluding `gentra` alone would have
un-suppressed `chevrolet_lacetti` (the site serves it from gentra's identical listing pool)
and `daewoo_damas` (chevrolet_damas's badge twin), re-collecting the very images being
skipped. 31 → 26 classes. `daewoo_nexia` is deliberately retained: Nexia 1 is a visually
distinct car from Nexia 3, confirmed on the contact sheets.

Per-class target lowered 2000 → **800** to favour breadth over depth given the throttle.

## Collection progress (2026-09-10, 00:55)

Session 13 (2026-09-10, 00:22–00:53, phone-hotspot IP 95.214.211.4) resumed the six
unfinished classes with `--only` and the same slow settings (concurrency 2, delay 2). It
finished kia_sorento, chery_tiggo_7_pro and chery_arrizo_6_pro, got byd_song_plus_dm_i_champion
to 471, and then hit **block #10** after ~390 listings in 31 minutes — the same per-IP budget as
before. The scraper was stopped at onset (both retries timed out, file count frozen for
2 minutes); nothing was lost.

| Class | Images | Listings | Status |
|---|---|---|---|
| vaz_vesta | 821 | 152 | at target |
| chevrolet_epica | 816 | 137 | at target |
| vaz_2106 | 812 | 164 | at target |
| chevrolet_tracker | 811 | 188 | at target |
| kia_k_5 | 811 | 146 | at target |
| vaz_2107 | 811 | 153 | at target |
| chevrolet_malibu | 810 | 148 | at target |
| chevrolet_nexia2 | 809 | 138 | at target |
| chevrolet_captiva | 808 | 145 | at target |
| chevrolet_labo | 807 | 171 | at target |
| chevrolet_matiz | 807 | 151 | at target |
| chevrolet_onix | 807 | 189 | at target |
| chevrolet_malibu2 | 806 | 144 | at target |
| daewoo_nexia | 806 | 135 | at target |
| daewoo_tico | 806 | 172 | at target |
| chevrolet_tracker_2 | 805 | 161 | at target |
| chevrolet_equinox | 803 | 150 | at target |
| chevrolet_monza | 719 | 155 | complete (inventory ceiling: 155 listings on the site) |
| kia_sorento | 698 | 116 | complete (inventory ceiling: 116 listings on the site) |
| kia_sonet | 691 | 147 | complete (inventory ceiling: 147 listings on the site) |
| chery_tiggo_7_pro | 652 | 120 | complete (inventory ceiling: 120 listings on the site) |
| byd_chazor | 617 | 127 | complete (inventory ceiling: 127 listings on the site) |
| chery_arrizo_6_pro | 475 | 92 | complete (inventory ceiling: 92 listings on the site) |
| byd_song_plus_dm_i_champion | 471 | 94 | **partial** — block #10 hit mid-class (94 of 126 listings done) |
| vaz_lada_r90 | 0 | 0 | **not started** |
| kia_sportage | 0 | 0 | **not started** |
| chevrolet_cobalt | 2007 | 392 | pre-existing |
| chevrolet_gentra | 2004 | 344 | pre-existing |
| chevrolet_damas | 681 | 209 | pre-existing |

**Total: 22,771 images** across 27 folders, every one recorded in
`data/raw/manifest.csv` with its SHA-256; every filename is `<listing_id>_<n>.webp`.

**23 of 26 in-scope classes complete; 1 partial; 2 not started.**

Classes below 800 are **inventory-limited, not shortfalls**: the site has no more listings
for them, so re-running adds zero images (verified directly on monza and sonet — a second
pass saved 0). The three Chery/BYD/Kia classes collected this session came in at 475–698 from
105–132 listings, exactly the 500–700 range predicted from their listing counts.

### Remaining work
byd_song_plus_dm_i_champion (471, ~32 listings left), vaz_lada_r90 (~110 listings) and
kia_sportage (101 listings). A cooldown chain probes the site every 10 minutes after a 25-minute
wait and relaunches automatically; a network switch resumes it immediately.

### Class-list changes during the run
- 5 classes excluded as already collected (cobalt, spark, damas, gentra, nexia3), which
  **cascaded** to 3 more that are the same cars under other labels (chevrolet_lacetti and
  daewoo_lacetti share gentra's listing pool; daewoo_damas is chevrolet_damas rebadged).
- vaz_2121 dropped by request; kia_sportage added in its place. Checked the alternatives
  rather than assuming: Toyota has 159 listings across 44 models, Kia 857 across ~40, BYD
  692 across ~50 — spread so thin that only kia_sportage (101) cleared the 100 floor, and
  the Kia/BYD models already in the list were the most common ones available.
- Target lowered 2000 -> 800 to favour breadth over depth.

## Phase 3 — reliability findings

**Site throttle:** a per-IP budget of roughly 400-600 listings, then 1.5-2.5 hours of
refused connections. Politeness settings do not prevent it (concurrency 3 reached ~330
listings, concurrency 2 reached ~500-600). Switching networks resets the counter but not
its size. Ten blocks were absorbed this way; the tenth (2026-09-10) came after ~390
listings in 31 minutes at concurrency 2, so the budget is closer to 400 than 600 on a
fresh IP.

**Local network faults are a different failure and need a different response.** Three
distinct signatures appeared, and confusing them wastes hours:

| Signature | Cause | Correct response |
|---|---|---|
| `ERR_CONNECTION_TIMED_OUT`, gradual | site throttle | stop, cool down 1.5-2.5h |
| `ERR_NAME_NOT_RESOLVED`, all at once | DNS / connection down | stop, wait for the network |
| `ERR_NETWORK_IO_SUSPENDED` / `CHANGED` | machine slept or Wi-Fi switched | restart once connected |

A curl test is useless for diagnosis here: avtoelon.uz blocks plain HTTP clients as bot
protection, so curl fails permanently regardless of our status. Only the browser probe is
diagnostic — that distinction cost real time before it was pinned down.

**Guard added after two DNS outages** each burned a full pass: the run now aborts when 3
consecutive models return no listings, since that means the connection is down rather than
the models being empty. It fired correctly on its first outing, stopping after 3 classes
instead of churning through 8.

**Stop-at-onset discipline:** a bounded no-progress check (listing count *and* file count
frozen for 60s+) distinguishes a real block from an isolated dead listing. Stopping early
consistently preserved more data than crawling into a block. Across nine blocks, two
process kills, a two-hour machine sleep and several network flips, **nothing collected was
ever lost** — the per-listing manifest writes made every interruption resumable.

## Phase 4 — verification (2026-09-10, 27 folders present)

All checks are read-only scripts, re-runnable; none modify `data/raw/`.

**Filenames and manifest — clean.** For every class: every file matches
`<listing_id>_<n>.<ext>`, every file has a manifest row with the same path, and every
manifest row that is not a duplicate pointer has its file on disk (scratchpad
`check_manifest.py`; the only transient mismatch was the listing being downloaded at the
moment of the check). Images per listing average 4.3–6.1 by class.

**Label purity — contact sheets clean for 22 classes; the two generic classes hide the newer
generation, and it is far worse than the sheets suggested.** Seeded 20-image sheets
(`scripts/contact_sheets.py` → `reports/contact_sheets/`) show the right car in every class except
`chevrolet_tracker` and `chevrolet_malibu`, where some listings are the newer car that has its
own class. Eyeballing 20 thumbnails put it at 3–4 and 2 of 20 listings. Two independent
measurements say otherwise:

1. *Listing years from the category pages* (`scripts/year_audit.py`, one page load per listing
   page, run 2026-09-10 05:05 → `reports/generation_audit.csv`). The old Tracker (Trax-based) was
   sold to 2020, the new one from 2021; Malibu 1 to 2016, Malibu 2 from 2017. Of 210 listings in
   the generic "Tracker" category, **96 (46%) are 2022 or newer**. In our own tracker folder, 90 of
   the 188 scraped listings were still on the site: 41 are 2022+ (140 images), 44 are ≤2020, 4 are
   2021. Malibu: 3 of 84 matched listings are 2017+. The newer classes are clean the other way:
   2% of `tracker_2` listings are ≤2020, none of `malibu2` are ≤2016.
2. *A visual vote for the listings the site no longer shows* (`scripts/generation_vote.py` →
   `reports/generation_vote_*.csv`): exterior shots only (YOLO vehicle box ≥12% of frame),
   cropped to the car and embedded with the project's own ArcFace-trained ConvNeXt-Tiny
   (`service/artifacts/model.pt`), then a class-balanced logistic regression trained on the
   year-verified old listings vs the newer class. Listing-grouped 5-fold CV: **94.4% image
   accuracy**, listing vote 88.6% on old / 99.4% on new (tracker), 100%/100% (malibu). Held-out
   year-verified new-generation listings hiding inside the generic class: **34 of 35** voted new
   (tracker), 3 of 3 (malibu).

Combined verdict for the generic classes (year first, visual vote where the year is unknown):

| Class | Listings | Old generation | Newer generation | Undecided / conflict |
|---|---|---|---|---|
| chevrolet_tracker | 175 with files | 84 listings, 424 images | **87 listings, 375 images (46%)** | 4 listings, 12 images |
| chevrolet_malibu | 143 with files | 134 listings, 757 images | 8 listings, 50 images (6%) | 1 listing, 3 images |

Sellers simply file a 2023 Tracker under "Tracker". Three signals agree — the sheets, the scrape-time
hash skips (cross-class only for these two pairs) and the perceptual dedup (cross-class groups only
for these two pairs, e.g. malibu listing 7487252 = malibu2 listing 7486043, one car posted twice).

**What was done about it — nothing to `data/raw/`, everything to the training set.** The raw folders
stay exactly as scraped (the manifest must remain a truthful record of the source). Instead
`reports/label_overrides.csv` lists the **95 listings (425 images)** to relabel
(`listing_id,from_class,to_class,reason`), and `scripts/deduplicate.py --relabel` applies it while
building `data/dedup/`: after it, tracker holds only the old body (≈436 images from 84 listings,
below target but clean), tracker_2 gains the 375 (≈1,180), malibu ≈760, malibu2 ≈856. The undecided
listings stay where the site put them and are flagged in the vote CSVs. Reversible by deleting one
CSV. If the split is not wanted at all, merging tracker+tracker_2 into one class is the other
consistent choice — what is not defensible is training on the raw folders as they are.

**Duplicates — none left at byte or pixel level; 211 near-duplicates at perceptual level.**
`scripts/deduplicate.py --dry-run` (SHA-256 of decoded pixels) over the 21,173 images present
at the time found **0** duplicates within or across classes. That is because the scraper
already skips byte-identical files at download time: the manifest holds 1,122 `duplicate_of`
pointers, 1,110 within the same class and **12 across classes** (from 3 listings: one
tracker listing whose photos were already stored under tracker_2, one malibu/malibu2 pair,
one sonet listing reusing tracker photos) — those 12 sit in whichever class was scraped
first. The perceptual pass (`--perceptual`, 64-bit dHash, catches re-encoded reposts) over
22,406 images found **211 near-duplicates** inside classes and **24 images in 12 cross-class
groups** — all tracker/tracker_2 or malibu/malibu2, e.g. malibu listing 7487252 and malibu2
listing 7486043 are the same car posted twice. The final deduplicated set is produced with
`--perceptual` into `data/dedup/` once collection ends (numbers in Phase 5).

Incidentally, the dedup script crashed after hashing 21k images when printing box-drawing
characters to a Windows cp1252 console — the same encoding bug the scraper hit on
2026-08-27; fixed the same way (commit `cbe158f`).

**Composition — detector-visible vehicle share per class (150 sampled each).**
`scripts/audit_composition.py` (YOLO11s, vehicle box ≥12% of frame, read-only):

Overall **75.6%** (3,061 of 4,050 sampled images; 150 per class, seed 42):

| Class | visible | Class | visible | Class | visible |
|---|---|---|---|---|---|
| vaz_2106 | 80.7% | chev. matiz | 77.3% | chev. cobalt | 73.3% |
| chev. equinox | 80.0% | byd song plus | 76.7% | chev. malibu2 | 73.3% |
| chev. monza | 79.3% | daewoo nexia | 76.7% | kia_k_5 | 73.3% |
| chev. tracker | 79.3% | byd_chazor | 76.0% | kia_sonet | 73.3% |
| chev. tracker_2 | 79.3% | chev. nexia2 | 75.3% | chev. damas | 72.7% |
| vaz_2107 | 79.3% | chev. onix | 75.3% | daewoo tico | 72.7% |
| vaz_vesta | 79.3% | chev. labo | 74.7% | chev. gentra | 72.0% |
| chev. malibu | 78.7% | chery_tiggo_7_pro | 74.0% | chev. epica | 70.0% |
| chery_arrizo_6_pro | 78.0% | chev. captiva | 74.0% | kia_sorento | 66.0% |

Validated against hand-labelled photos on 2026-08-31, the detector correctly rejects
dashboards, seats and instrument clusters (0% car area) but **wrongly keeps engine bays
(64–98%) and door cards (90%)** — they fill the frame with bodywork. Raising the area
threshold makes this worse, since an engine bay outscores a genuine side shot. True whole-car
yield is therefore about **60–65%** of the figures above, i.e. roughly 450–520 usable images
per 800-image class and 280–420 for the inventory-limited ones. Separating detail shots needs
shape reasoning or a purpose-trained classifier, not a detector-area rule.
