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

_(pending)_

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

## Phase 4 — verification

_(pending)_
