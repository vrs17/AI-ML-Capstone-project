#!/usr/bin/env python3
"""
avtoelon_scraper.py — polite, browser-based image collector for the Uzbek-car capstone.

WHY PLAYWRIGHT (not requests): avtoelon.uz sits behind bot protection, so a plain HTTP
client is blocked. Playwright drives a REAL browser on YOUR machine at a human-like rate,
reusing the site's own session — this is the respectful way to collect, not evasion.

WHAT IT DOES:
  1. Reads robots.txt and refuses any path the site disallows.
  2. For each car model you configure, walks the listing pages (native model filter URL).
  3. Opens each listing, grabs the gallery image URLs + the listing id.
  4. Downloads images to  data/raw/<model>/<listing_id>_<n>.jpg  (listing_id in the
     filename is REQUIRED later for the leakage-safe train/test split).
  5. Writes data/raw/manifest.csv so the Data Gate has an auditable record, and so the
     scraper can RESUME without re-downloading listings it already has.

POLITENESS: bounded concurrency (a few tabs, not a swarm) + a delay between actions +
honors any Crawl-delay in robots.txt. Please keep it that way.

RUN IT ON YOUR OWN MACHINE (where the site loads normally), not in this cloud sandbox:
    pip install playwright
    playwright install chromium
    python scripts/avtoelon_scraper.py --max-pages 15 --concurrency 3

First run headful (--show) so you can watch/verify; later add nothing to keep it headless.
"""

import argparse
import asyncio
import csv
import hashlib
import time
import random
import re
import sys
import urllib.robotparser
from pathlib import Path
from urllib.parse import urljoin, urlparse

from playwright.async_api import async_playwright

# ─────────────────────────────────────────────────────────────────────────────
# 1. CONFIG — the only site-specific part. Fill MODELS from your own browser.
#    Select a model on avtoelon.uz (native brand/model filter), then copy the URL
#    from your address bar and paste it here. label -> search/listing URL.
# ─────────────────────────────────────────────────────────────────────────────
BASE = "https://avtoelon.uz"

# NOTE: robots.txt disallows arbitrary query strings (Disallow: /*?* and /*?*=*), so we use
# the CLEAN model paths (the site's own address bar adds ?price-currency=1, which is NOT on
# the robots Allow-list — stripped here). Page 1 is the clean path; pagination below uses the
# one query param robots explicitly whitelists (Allow: /*/?page=).
MODELS = {
    "cobalt": "https://avtoelon.uz/avto/chevrolet/cobalt/",
    "nexia3": "https://avtoelon.uz/avto/chevrolet/nexia3/",
    "spark":  "https://avtoelon.uz/avto/chevrolet/spark/",
    "gentra": "https://avtoelon.uz/avto/chevrolet/gentra/",
    "damas":  "https://avtoelon.uz/avto/chevrolet/damas/",
}

# Pagination uses ?page= — the param robots.txt explicitly ALLOWS (Allow: /*/?page=).
PAGE_PARAM = "?page={n}"

# Selectors — sensible defaults + heuristic fallbacks. Confirm/override from DevTools
# (right-click a listing card -> Inspect). If a default doesn't match, the heuristics
# below usually still work; only edit if you get 0 listings / 0 images.
LISTING_LINK_SELECTOR = "a[href]"          # filtered by LISTING_HREF_RE below
GALLERY_IMG_SELECTOR = "img"               # filtered by IMG_URL_RE below

# Listing detail pages are /a/show/<numeric-id>. Anchor on that exact path so we don't
# pick up unrelated URLs that merely contain 6+ digits (e.g. third-party ad/tracker links
# like yandex.ru/adfox/354309/... that appear in the page's markup).
LISTING_HREF_RE = re.compile(r"/a/show/(\d{6,})")
# Listing photos are served from the site's image CDN; keep only real photo URLs.
IMG_URL_RE = re.compile(r"https?://[^\s\"']+\.(?:jpe?g|png|webp)", re.I)
# Skip tiny thumbnails/sprites/logos by URL hints (edit if it drops real photos).
IMG_SKIP_RE = re.compile(r"(sprite|logo|icon|placeholder|avatar|/40x|/50x|/100x)", re.I)

# Real car photos live on the kcdn.online image CDN as
#   .../webp/<xx>/<uuid>/<n>-<WxH>.webp   (sized thumbnail)
#   .../webp/<xx>/<uuid>/<n>-full.webp    (full resolution, linked from the <a> gallery)
# Restricting to this host drops the site's own og-image/footer logos and ad banners
# (which are NOT on kcdn), and we normalize every sized thumbnail up to its -full variant
# so we save training-usable photos, not 120x90 thumbs.
PHOTO_HOST_RE = re.compile(r"kcdn\.online", re.I)
_SIZE_SUFFIX_RE = re.compile(r"-(?:\d+x\d+|full)\.(?:jpe?g|png|webp)$", re.I)


def to_full(u: str) -> str:
    """Rewrite a sized thumbnail URL (…-408x306.webp) to its full-resolution variant."""
    return re.sub(r"-\d+x\d+\.(jpe?g|png|webp)$", r"-full.\1", u, flags=re.I)


def photo_base(u: str) -> str:
    """Size-independent key so each distinct photo is saved once, not once per size."""
    return _SIZE_SUFFIX_RE.sub("", u)


def photo_group(u: str) -> str:
    """The CDN directory holding one listing's photos.

    Photos are served as  .../webp/<xx>/<listing-dir>/<n>-full.webp , so every photo of a
    given listing shares the parent directory and photos of OTHER listings never do. Taking
    the parent path is format-agnostic — it needs no knowledge of how the id is generated.
    """
    return u.rsplit("/", 1)[0]


def select_gallery(candidates):
    """Keep only the photos that belong to THIS listing.

    Why this is not a CSS-selector problem
    --------------------------------------
    The previous version kept every <img> on the page except those inside an anchor to
    another listing (`closest('a[href]')` containing /a/show/). That catches a card whose
    markup is <a href="/a/show/…"><img></a>, and misses the two commonest layouts:

        <div><img><a href="/a/show/…">title</a></div>     image is a SIBLING of the link
        <div class="promo"><img></div>                    promoted block, no anchor at all

    Both slipped through, so a listing's folder collected other cars' thumbnails — which is
    label noise in training data, and worse, the same recommendation photo recurs across
    many listings, so it can land on both sides of the train/test split.

    Grouping by CDN directory is strictly stronger than any blacklist and does not depend on
    the site's class names, which can change without notice. The listing's own group is
    identified in priority order:

      1. lightbox <a href="…-full.webp"> links — unambiguously the main gallery;
      2. the og:image meta tag — the listing's primary photo;
      3. failing both, the group with the most distinct photos (a gallery has many, a
         recommendation card contributes one thumbnail).

    `candidates` is [{"url": str, "src": "lightbox"|"og"|"img"}].
    Returns (kept_urls, report_dict).
    """
    groups, order = {}, []
    for c in candidates:
        g = photo_group(c["url"])
        if g not in groups:
            groups[g] = {"urls": {}, "srcs": set()}
            order.append(g)
        # normalise to the full-resolution variant BEFORE storing: the same photo arrives
        # both as a sized <img> thumbnail and as a -full lightbox href, and storing raw
        # would let whichever came last win — silently saving a 408x306 thumb.
        groups[g]["urls"][photo_base(c["url"])] = to_full(c["url"])
        groups[g]["srcs"].add(c["src"])

    if not groups:
        return [], {"reason": "no photos found", "groups": 0, "kept": 0,
                    "dropped_foreign": 0, "dropped_groups": []}

    strong = [g for g in order if "lightbox" in groups[g]["srcs"]]
    reason = "lightbox links"
    if not strong:
        strong = [g for g in order if "og" in groups[g]["srcs"]]
        reason = "og:image"
    if not strong:
        # Live-site finding (audit 2026-08-26): a listing posted with NO photos still shows
        # kcdn thumbnails — the "similar listings" sidebar — as several 1-photo groups. The
        # old unconditional fallback then kept a DIFFERENT car's thumbnail. A real gallery
        # concentrates many photos in one CDN directory; recommendation cards contribute one
        # each. So only trust the fallback when one group clearly dominates — otherwise keep
        # nothing: a skipped odd listing is cheap, a mislabelled photo poisons the dataset.
        biggest = max(order, key=lambda g: len(groups[g]["urls"]))
        if len(groups[biggest]["urls"]) >= 3:
            strong = [biggest]
            reason = "largest photo group"
        else:
            return [], {"reason": "no reliable gallery signal", "groups": len(groups),
                        "kept": 0,
                        "dropped_foreign": sum(len(groups[g]["urls"]) for g in order),
                        "dropped_groups": list(order)}

    kept = [u for g in strong for u in groups[g]["urls"].values()]
    dropped = sum(len(groups[g]["urls"]) for g in order if g not in strong)
    return kept, {"reason": reason, "groups": len(groups), "kept": len(kept),
                  "dropped_foreign": dropped,
                  "dropped_groups": [g for g in order if g not in strong]}

# Words that must appear in a listing's title or <h1> for it to count as this model.
# Keep them lowercase and permissive enough for Cyrillic/Latin spelling variants.
MODEL_KEYWORDS = {
    "cobalt": ("cobalt", "кобальт"),
    "nexia3": ("nexia", "нексия"),
    "spark":  ("spark", "спарк"),
    "gentra": ("gentra", "джентра", "гентра"),
    "damas":  ("damas", "дамас"),
}

OUT_DIR = Path("data/raw")
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

# ─────────────────────────────────────────────────────────────────────────────
# 2. robots.txt gate — do not scrape a path the site disallows.
# ─────────────────────────────────────────────────────────────────────────────
def load_robots():
    rp = urllib.robotparser.RobotFileParser()
    rp.set_url(urljoin(BASE, "/robots.txt"))
    try:
        rp.read()
    except Exception as e:
        print(f"[robots] could not read robots.txt ({e}).")
        return None
    return rp


def allowed(rp, url) -> bool:
    if rp is None:
        return True
    return rp.can_fetch(USER_AGENT, url)


def listing_id_from(url: str) -> str:
    m = LISTING_HREF_RE.search(urlparse(url).path)
    return m.group(1) if m else re.sub(r"\W+", "", url)[-12:]


# ─────────────────────────────────────────────────────────────────────────────
# 3. Manifest — resume support + Data-Gate audit trail.
# ─────────────────────────────────────────────────────────────────────────────
MANIFEST = OUT_DIR / "manifest.csv"
MANIFEST_COLS = ["model", "listing_id", "listing_url", "image_url", "local_path",
                 "sha256", "duplicate_of"]

# sha256(bytes) -> the path we first stored those exact bytes at. Shared for the whole run
# and rebuilt from the manifest on resume, so a photo is never written to disk twice.
SEEN_HASHES: dict[str, str] = {}


def load_done_listings() -> set:
    """Resume state: which (model, listing) pairs are done, and every byte-hash already on disk.

    The hash index is rebuilt here rather than kept only in memory, so a resumed run does
    not re-download bytes an earlier run already stored. Rows written before this column
    existed simply have no hash and are skipped.
    """
    if not MANIFEST.exists():
        return set()
    done = set()
    with MANIFEST.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            done.add((r["model"], r["listing_id"]))
            h, path = r.get("sha256"), r.get("local_path")
            if h and path and h not in SEEN_HASHES:
                SEEN_HASHES[h] = path
    return done


def append_manifest(rows):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    new = not MANIFEST.exists()
    # utf-8 explicitly: Windows defaults to cp1252, which cannot encode Cyrillic and
    # crashes the write (seen live when discovered_models.csv hit a Russian page title).
    with MANIFEST.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=MANIFEST_COLS)
        if new:
            w.writeheader()
        w.writerows(rows)


# ─────────────────────────────────────────────────────────────────────────────
# 4. Scrape logic.
# ─────────────────────────────────────────────────────────────────────────────
# Politeness knobs (kept deliberately gentle — this is a courteous collector, not a swarm).
IMG_DOWNLOAD_DELAY = 0.4   # seconds to pause between individual image downloads
LISTING_SETTLE = 2.0       # base seconds to hold each listing (spaces out our request rate)
BACKOFF_SECONDS = 60       # on a site connection-timeout, wait this long and retry once


async def goto_polite(page, url, timeout=45000):
    """Navigate to url. If the site throttles us with a connection timeout, back off once
    (BACKOFF_SECONDS) and retry; if it still fails, raise so the caller skips it — the
    manifest-resume logic will pick it up on a later run."""
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=timeout)
    except Exception as e:
        if "ERR_CONNECTION_TIMED_OUT" in str(e) or "Timeout" in str(e):
            print(f"[backoff] {url} timed out — waiting {BACKOFF_SECONDS}s and retrying once")
            await asyncio.sleep(BACKOFF_SECONDS)
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout)
        else:
            raise


async def collect_listing_urls(page, model, search_url, max_pages, delay, rp):
    """Walk paginated search pages and gather unique listing URLs."""
    found, seen = [], set()
    for n in range(1, max_pages + 1):
        url = search_url if n == 1 else search_url + PAGE_PARAM.format(n=n)
        if not allowed(rp, url):
            print(f"[robots] disallowed, skipping {url}")
            break
        try:
            await goto_polite(page, url)
        except Exception as e:
            print(f"[{model}] page {n} load failed: {e}")
            break
        await asyncio.sleep(delay + random.uniform(0, delay))
        hrefs = await page.eval_on_selector_all(
            LISTING_LINK_SELECTOR, "els => els.map(e => e.href)"
        )
        page_hits = 0
        for h in hrefs:
            if h and LISTING_HREF_RE.search(urlparse(h).path) and h not in seen:
                seen.add(h)
                found.append(h)
                page_hits += 1
        print(f"[{model}] page {n}: +{page_hits} listings (total {len(found)})")
        if page_hits == 0:  # ran past the last page
            break
    return found


async def scrape_one_listing(context, model, url, delay, rp, audit=None):
    """Open a listing, extract image URLs + save them. Returns manifest rows."""
    if not allowed(rp, url):
        return []
    lid = listing_id_from(url)
    page = await context.new_page()
    rows = []
    try:
        await goto_polite(page, url)
        # Hold each listing briefly so the page settles AND our request rate stays gentle
        # (~2s between listings). Combined with the per-image delay below, this keeps us
        # comfortably under the rate limit that a faster burst can trip.
        await asyncio.sleep(LISTING_SETTLE + random.uniform(0, delay))
        # Grab <img> thumbnail srcs AND the <a> gallery links (the latter point at the
        # full-resolution -full.webp photos). Filter to the photo CDN, normalize every
        # sized thumbnail up to its full-res variant, and dedupe by photo (not by size).
        #
        # SCOPED to the listing's OWN gallery: skip any <img> that sits inside a link to
        # another listing (href contains "/a/show/"). Those are the "similar listings"
        # recommendation thumbnails — other cars' photos that repeat across pages and would
        # otherwise pollute this listing (and duplicate/contaminate the dataset). The main
        # gallery photos are wrapped in <a href="...-full.webp"> lightbox links, not /a/show/.
        # Gather every photo-CDN URL on the page, TAGGED by where it came from. The tag is
        # what lets select_gallery tell this listing's gallery from recommendation cards
        # and promoted blocks — see its docstring for why a CSS blacklist cannot.
        srcs = await page.eval_on_selector_all(
            GALLERY_IMG_SELECTOR,
            "els => els.flatMap(e => [e.src, e.currentSrc,"
            " e.getAttribute('data-src'), e.getAttribute('data-original')])",
        )
        hrefs = await page.eval_on_selector_all(
            "a[href]",
            "els => els"
            ".filter(e => !(e.getAttribute('href') || '').includes('/a/show/'))"
            ".map(e => e.href)",
        )
        og = await page.eval_on_selector_all(
            "meta[property='og:image'], meta[name='og:image']",
            "els => els.map(e => e.content)",
        )

        def _clean(raw):
            if not raw:
                return None
            m = IMG_URL_RE.search(raw)
            if not m:
                return None
            u = m.group(0)
            # The HOST itself must be the photo CDN — not merely mentioned anywhere in the
            # string. Live-site finding (audit 2026-08-26): the VK/OK share buttons embed the
            # listing's photo #1 as a percent-encoded parameter (https://vk.com/share.php?
            # ...image=...kcdn.online...1-full.webp), which a substring test passes; each
            # then formed a bogus 1-photo "lightbox" group and inflated every listing by +2.
            if not PHOTO_HOST_RE.search(urlparse(u).netloc) or IMG_SKIP_RE.search(u):
                return None
            return u

        candidates = []
        for raw, tag in ([(x, "img") for x in srcs]
                         + [(x, "lightbox") for x in hrefs]
                         + [(x, "og") for x in og]):
            u = _clean(raw)
            if u:
                candidates.append({"url": u, "src": tag})

        urls, report = select_gallery(candidates)

        # Cross-model contamination guard. A model-filtered search can still surface a
        # promoted listing for a different car, and at 30-50 classes that is no longer a
        # rounding error — one mislabelled listing is a dozen mislabelled photos. Confirm
        # the page itself says what we think it is before saving anything under this label.
        if MODEL_KEYWORDS.get(model):
            title = ((await page.title()) or "").lower()
            head = ""
            try:
                h1 = await page.query_selector("h1")
                head = ((await h1.inner_text()) if h1 else "").lower()
            except Exception:
                pass
            hay = f"{title} {head}"
            if not any(k in hay for k in MODEL_KEYWORDS[model]):
                print(f"[{model}] SKIP {lid}: page says {(head or title)[:60]!r}, "
                      f"expected one of {MODEL_KEYWORDS[model]}")
                return []

        if audit is not None:
            audit.append({"model": model, "listing_id": lid, "listing_url": url,
                          "found": len(candidates), **report})
            print(f"[{model}] {lid}: {report['kept']} kept via {report['reason']}, "
                  f"{report['dropped_foreign']} foreign dropped "
                  f"({report['groups']} photo groups on page)")
            return []

        dest = OUT_DIR / model
        dest.mkdir(parents=True, exist_ok=True)
        dup = 0
        for i, u in enumerate(urls):
            ext = (re.search(r"\.(jpe?g|png|webp)", u, re.I) or [".jpg"])[0].lower()
            out = dest / f"{lid}_{i}{ext if ext.startswith('.') else '.jpg'}"
            if out.exists():
                rows.append(dict(model=model, listing_id=lid, listing_url=url,
                                 image_url=u, local_path=str(out),
                                 sha256="", duplicate_of=""))
                continue
            try:
                # download THROUGH the browser session so Cloudflare clearance applies
                resp = await context.request.get(u, timeout=45000)
                if resp.ok:
                    data = await resp.body()
                    digest = hashlib.sha256(data).hexdigest()
                    # Byte-identical dedup. The same photo genuinely recurs — a seller
                    # relisting the same car, or one photo reused across listings — and
                    # storing it twice both wastes space and, far worse, lets one image
                    # land on BOTH sides of the train/test split. The duplicate is still
                    # recorded in the manifest, pointing at the copy we kept, so nothing
                    # is silently dropped and the Data Gate can audit it.
                    # (No await between the lookup and the insert, so concurrent listing
                    # tasks cannot interleave and both write the same bytes.)
                    prior = SEEN_HASHES.get(digest)
                    if prior:
                        rows.append(dict(model=model, listing_id=lid, listing_url=url,
                                         image_url=u, local_path="", sha256=digest,
                                         duplicate_of=prior))
                        dup += 1
                    else:
                        out.write_bytes(data)
                        SEEN_HASHES[digest] = str(out)
                        rows.append(dict(model=model, listing_id=lid, listing_url=url,
                                         image_url=u, local_path=str(out), sha256=digest,
                                         duplicate_of=""))
            except Exception as e:
                print(f"[{model}] img fail {u}: {e}")
            await asyncio.sleep(IMG_DOWNLOAD_DELAY)   # politeness: pace image downloads
        kept_n = sum(1 for r in rows if r.get("local_path"))
        print(f"[{model}] listing {lid}: {kept_n} images"
              + (f" (+{dup} byte-identical duplicates skipped)" if dup else ""))
    except Exception as e:
        print(f"[{model}] listing {url} failed: {e}")
    finally:
        await page.close()
    return rows


# Model catalogue paths look like /avto/<brand>/<model>/ — the same shape as the MODELS
# entries above, which are known-good.
CATALOG_HREF_RE = re.compile(r"^/avto/([a-z0-9\-]+)/([a-z0-9\-]+)/?$", re.I)
# "Найдено 2 965 объявлений" / "1 234 e'lon" — the number next to the noun, and it MUST
# start with a digit: the nav menu says "Мои объявления" long before the result count,
# and a digit-optional pattern matched that first (capturing only whitespace -> 0
# listings for every model, while the "successful" match also suppressed the card-count
# fallback). Found against the live site, 2026-08-26.
COUNT_RE = re.compile(r"(\d[\d\s\u00a0,\.]{0,11})\s*(?:объявлен|e['\u02bc]?lon|listing|natija)", re.I)


async def discover_models(context, rp, args):
    """Rank the site's own model catalogue by listing volume and print a MODELS dict.

    Written because the honest answer to "give me the 30-50 most common Uzbek models" is
    that popularity is a property of the site's inventory, not of anyone's recollection.
    This reads the catalogue, counts listings per model, and emits config you can paste —
    so the class list is evidence, and every URL in it is one the site actually served.
    """
    root = urljoin(BASE, "/avto/")
    if not allowed(rp, root):
        print(f"[robots] disallowed: {root}")
        return
    page = await context.new_page()
    await goto_polite(page, root)
    await asyncio.sleep(args.delay)
    hrefs = await page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
    await page.close()

    seen, models = set(), []
    for h in hrefs:
        m = CATALOG_HREF_RE.match(urlparse(h).path)
        if not m:
            continue
        brand, model = m.group(1).lower(), m.group(2).lower()
        key = f"{brand}/{model}"
        if key in seen:
            continue
        seen.add(key)
        models.append((brand, model, urljoin(BASE, f"/avto/{brand}/{model}/")))
    print(f"[discover] {len(models)} model pages linked from {root}")
    if not models:
        print("[discover] none found — the catalogue markup may have changed; open "
              f"{root} and check that model links look like /avto/<brand>/<model>/")
        return

    sem = asyncio.Semaphore(args.concurrency)

    async def count(brand, model, url):
        async with sem:
            if not allowed(rp, url):
                return None
            pg = await context.new_page()
            try:
                await goto_polite(pg, url)
                await asyncio.sleep(args.delay)
                body = await pg.inner_text("body")
                m = COUNT_RE.search(body)
                if m:
                    n = int(re.sub(r"[^\d]", "", m.group(1)) or 0)
                else:   # fall back to counting cards on page 1 — a floor, not a total
                    links = await pg.eval_on_selector_all(
                        "a[href]", "els => els.map(e => e.getAttribute('href') || '')")
                    n = len({LISTING_HREF_RE.search(h).group(1)
                             for h in links if LISTING_HREF_RE.search(h)})
                title = ((await pg.title()) or "").split("|")[0].strip()
                return (brand, model, url, n, title)
            except Exception as e:
                print(f"[discover] {brand}/{model}: {str(e)[:70]}")
                return None
            finally:
                await pg.close()

    got = [r for r in await asyncio.gather(*(count(*m) for m in models)) if r]
    got.sort(key=lambda r: -r[3])
    top = got[:args.discover]

    print(f"\n# ── top {len(top)} models by listing count "
          f"(discovered {time.strftime('%Y-%m-%d')}) ──")
    print("MODELS = {")
    for brand, model, url, n, _ in top:
        label = f"{brand}_{model}".replace("-", "_")
        print(f'    "{label}":{" " * max(1, 22 - len(label))}"{url}",'
              f'   # {n:,} listings')
    print("}\n")
    print("MODEL_KEYWORDS = {")
    for brand, model, _u, _n, _t in top:
        label = f"{brand}_{model}".replace("-", "_")
        kw = model.replace("-", " ")
        print(f'    "{label}":{" " * max(1, 22 - len(label))}("{kw}",),')
    print("}")
    print("\n# Paste both dicts over the ones at the top of this file. Add Cyrillic spellings")
    print("# to MODEL_KEYWORDS where a model is commonly written that way (e.g. \u043d\u0435\u043a\u0441\u0438\u044f for nexia) —")
    print("# the keywords are what stop a promoted listing for another car being saved")
    print("# under this label.")

    out = OUT_DIR / "discovered_models.csv"
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["rank", "brand", "model", "url", "listings", "page_title"])
        for i, (brand, model, url, n, title) in enumerate(got, 1):
            w.writerow([i, brand, model, url, n, title])
    print(f"\n[discover] full ranking of {len(got)} models -> {out}")


async def run(args):
    if not MODELS and not args.discover:
        sys.exit("Fill the MODELS dict at the top with your native model-filter URLs first.")
    rp = load_robots()
    done = load_done_listings()
    audit = [] if args.audit else None
    if audit is not None:
        print(f"[audit] dry run — inspecting up to {args.audit} listings per model, "
              f"downloading nothing\n")
    else:
        print(f"[resume] {len(done)} (model,listing) pairs already collected")

    async with async_playwright() as pw:
        # --channel drives an installed branded browser (e.g. "chrome" or "msedge") instead
        # of Playwright's bundled Chromium. Handy on machines/networks where the bundled-
        # Chromium download is blocked. Default (None) uses the bundled build.
        launch_kwargs = {"headless": not args.show}
        if args.channel:
            launch_kwargs["channel"] = args.channel
        browser = await pw.chromium.launch(**launch_kwargs)
        # --insecure opts into ignore_https_errors, needed ONLY on networks that intercept /
        # re-sign TLS (a real browser trusts the intercepting CA, but Playwright's request
        # context otherwise rejects it: "certificate signature failure"). Default is secure —
        # normal certificate verification, and no downloads over an untrusted certificate.
        context = await browser.new_context(
            user_agent=USER_AGENT,
            ignore_https_errors=args.insecure,
        )
        if args.insecure:
            print("[warn] --insecure: TLS certificate verification is DISABLED for this run")
        if args.discover:
            await discover_models(context, rp, args)
            await browser.close()
            return

        sem = asyncio.Semaphore(args.concurrency)

        for model, search_url in MODELS.items():
            nav = await context.new_page()
            listings = await collect_listing_urls(
                nav, model, search_url, args.max_pages, args.delay, rp
            )
            await nav.close()

            todo = [u for u in listings if (model, listing_id_from(u)) not in done]
            if audit is not None:
                todo = listings[:args.audit]      # audit re-inspects, ignoring resume state
            print(f"[{model}] {len(todo)} new listings to scrape "
                  f"({len(listings) - len(todo)} already done)")

            async def bounded(u):
                async with sem:
                    return await scrape_one_listing(context, model, u, args.delay, rp, audit)

            results = await asyncio.gather(*(bounded(u) for u in todo))
            rows = [r for batch in results for r in batch]
            if rows:
                append_manifest(rows)
            stored = sum(1 for r in rows if r.get("local_path"))
            dups = sum(1 for r in rows if r.get("duplicate_of"))
            print(f"[{model}] saved {stored} images"
                  + (f", skipped {dups} byte-identical duplicates" if dups else "") + "\n")

        await browser.close()

    if audit is not None:
        tot = sum(a["kept"] for a in audit)
        drop = sum(a["dropped_foreign"] for a in audit)
        rpt = OUT_DIR / "audit_report.csv"
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        with rpt.open("w", newline="", encoding="utf-8") as f:
            cols = ["model", "listing_id", "listing_url", "found", "kept",
                    "dropped_foreign", "groups", "reason"]
            w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            w.writerows(audit)
        print(f"\n[audit] {len(audit)} listings · {tot} photos would be kept · "
              f"{drop} foreign photos dropped")
        print(f"[audit] wrote {rpt}")
        print("\nOpen a couple of those listing_urls in a browser and count the photos in "
              "the gallery.\nIf 'kept' matches the gallery count, the scoping is correct on "
              "the live site.\nIf it does not, send me the audit_report.csv row and the "
              "listing URL.")
        return
    print("Done. Review data/raw/manifest.csv and the per-model folders.")


def main():
    # Force UTF-8 stdout so logging a message that contains non-cp1252 characters
    # (e.g. the "->" arrow in Playwright error text) doesn't crash on Windows consoles.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="Polite avtoelon.uz car-image collector")
    ap.add_argument("--max-pages", type=int, default=10, help="listing pages per model")
    ap.add_argument("--concurrency", type=int, default=3, help="parallel listing tabs (keep small)")
    ap.add_argument("--delay", type=float, default=1.0, help="base seconds between actions")
    ap.add_argument("--show", action="store_true", help="run headful to watch the browser")
    ap.add_argument("--channel", default=None,
                    help="use an installed browser channel (e.g. 'chrome', 'msedge') instead "
                         "of Playwright's bundled Chromium")
    ap.add_argument("--discover", type=int, default=0, metavar="N",
                    help="rank the site's model catalogue by listing count and print the top "
                         "N as a ready-to-paste MODELS dict. Use this to choose the class "
                         "list from evidence instead of guessing.")
    ap.add_argument("--audit", type=int, default=0, metavar="N",
                    help="dry run: inspect N listings per model, print what WOULD be kept vs "
                         "dropped and why, download nothing. Use this to verify the scraper "
                         "against the live site before a real collection run.")
    ap.add_argument("--insecure", action="store_true",
                    help="opt in to ignore_https_errors — ONLY for networks that intercept TLS; "
                         "disables certificate verification for this run")
    asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    main()
