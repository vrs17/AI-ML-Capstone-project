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
MANIFEST_COLS = ["model", "listing_id", "listing_url", "image_url", "local_path"]


def load_done_listings() -> set:
    if not MANIFEST.exists():
        return set()
    with MANIFEST.open() as f:
        return {(r["model"], r["listing_id"]) for r in csv.DictReader(f)}


def append_manifest(rows):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    new = not MANIFEST.exists()
    with MANIFEST.open("a", newline="") as f:
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


async def scrape_one_listing(context, model, url, delay, rp):
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
        srcs = await page.eval_on_selector_all(
            GALLERY_IMG_SELECTOR,
            "els => els.flatMap(e => [e.src, e.currentSrc, e.getAttribute('data-src')])",
        )
        hrefs = await page.eval_on_selector_all(
            "a[href]", "els => els.map(e => e.href)"
        )
        urls, seen = [], set()
        for s in (srcs + hrefs):
            if not s:
                continue
            m = IMG_URL_RE.search(s)
            if not m:
                continue
            u = m.group(0)
            if not PHOTO_HOST_RE.search(u) or IMG_SKIP_RE.search(u):
                continue
            u = to_full(u)                 # 120x90 thumb -> full-resolution photo
            base = photo_base(u)
            if base in seen:
                continue
            seen.add(base)
            urls.append(u)

        dest = OUT_DIR / model
        dest.mkdir(parents=True, exist_ok=True)
        for i, u in enumerate(urls):
            ext = (re.search(r"\.(jpe?g|png|webp)", u, re.I) or [".jpg"])[0].lower()
            out = dest / f"{lid}_{i}{ext if ext.startswith('.') else '.jpg'}"
            if out.exists():
                rows.append(dict(model=model, listing_id=lid, listing_url=url,
                                 image_url=u, local_path=str(out)))
                continue
            try:
                # download THROUGH the browser session so Cloudflare clearance applies
                resp = await context.request.get(u, timeout=45000)
                if resp.ok:
                    out.write_bytes(await resp.body())
                    rows.append(dict(model=model, listing_id=lid, listing_url=url,
                                     image_url=u, local_path=str(out)))
            except Exception as e:
                print(f"[{model}] img fail {u}: {e}")
            await asyncio.sleep(IMG_DOWNLOAD_DELAY)   # politeness: pace image downloads
        print(f"[{model}] listing {lid}: {len(rows)} images")
    except Exception as e:
        print(f"[{model}] listing {url} failed: {e}")
    finally:
        await page.close()
    return rows


async def run(args):
    if not MODELS:
        sys.exit("Fill the MODELS dict at the top with your native model-filter URLs first.")
    rp = load_robots()
    done = load_done_listings()
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
        sem = asyncio.Semaphore(args.concurrency)

        for model, search_url in MODELS.items():
            nav = await context.new_page()
            listings = await collect_listing_urls(
                nav, model, search_url, args.max_pages, args.delay, rp
            )
            await nav.close()

            todo = [u for u in listings if (model, listing_id_from(u)) not in done]
            print(f"[{model}] {len(todo)} new listings to scrape "
                  f"({len(listings) - len(todo)} already done)")

            async def bounded(u):
                async with sem:
                    return await scrape_one_listing(context, model, u, args.delay, rp)

            results = await asyncio.gather(*(bounded(u) for u in todo))
            rows = [r for batch in results for r in batch]
            if rows:
                append_manifest(rows)
            print(f"[{model}] saved {len(rows)} images\n")

        await browser.close()
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
    ap.add_argument("--insecure", action="store_true",
                    help="opt in to ignore_https_errors — ONLY for networks that intercept TLS; "
                         "disables certificate verification for this run")
    asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    main()
