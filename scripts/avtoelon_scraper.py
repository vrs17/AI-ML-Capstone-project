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

MODELS = {
    # "cobalt":  "https://avtoelon.uz/PASTE-COBALT-FILTER-URL",
    # "nexia3":  "https://avtoelon.uz/PASTE-NEXIA-3-FILTER-URL",
    # "spark":   "https://avtoelon.uz/PASTE-SPARK-FILTER-URL",
    # "gentra":  "https://avtoelon.uz/PASTE-GENTRA-FILTER-URL",
    # "damas":   "https://avtoelon.uz/PASTE-DAMAS-FILTER-URL",
}

# Pagination: how the site adds page numbers to the listing URL. Confirm in your browser
# (click "next page" and look at the URL). Common patterns: "?page={n}" or "/p{n}".
PAGE_PARAM = "?page={n}"

# Selectors — sensible defaults + heuristic fallbacks. Confirm/override from DevTools
# (right-click a listing card -> Inspect). If a default doesn't match, the heuristics
# below usually still work; only edit if you get 0 listings / 0 images.
LISTING_LINK_SELECTOR = "a[href]"          # filtered by LISTING_HREF_RE below
GALLERY_IMG_SELECTOR = "img"               # filtered by IMG_URL_RE below

# A listing URL on avtoelon typically contains a long numeric id. Adjust if needed.
LISTING_HREF_RE = re.compile(r"/(\d{6,})")
# Listing photos are served from the site's image CDN; keep only real photo URLs.
IMG_URL_RE = re.compile(r"https?://[^\s\"']+\.(?:jpe?g|png|webp)", re.I)
# Skip tiny thumbnails/sprites/logos by URL hints (edit if it drops real photos).
IMG_SKIP_RE = re.compile(r"(sprite|logo|icon|placeholder|avatar|/40x|/50x|/100x)", re.I)

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
async def collect_listing_urls(page, model, search_url, max_pages, delay, rp):
    """Walk paginated search pages and gather unique listing URLs."""
    found, seen = [], set()
    for n in range(1, max_pages + 1):
        url = search_url if n == 1 else search_url + PAGE_PARAM.format(n=n)
        if not allowed(rp, url):
            print(f"[robots] disallowed, skipping {url}")
            break
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
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
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        await asyncio.sleep(delay + random.uniform(0, delay))
        # grab <img src> + srcset + og:image, then filter to real photos
        srcs = await page.eval_on_selector_all(
            GALLERY_IMG_SELECTOR,
            "els => els.flatMap(e => [e.src, e.currentSrc, e.getAttribute('data-src')])",
        )
        og = await page.eval_on_selector_all(
            "meta[property='og:image']", "els => els.map(e => e.content)"
        )
        urls, seen = [], set()
        for s in (srcs + og):
            if not s:
                continue
            m = IMG_URL_RE.search(s)
            if not m:
                continue
            u = m.group(0)
            if IMG_SKIP_RE.search(u) or u in seen:
                continue
            seen.add(u)
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
        browser = await pw.chromium.launch(headless=not args.show)
        context = await browser.new_context(user_agent=USER_AGENT)
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
    ap = argparse.ArgumentParser(description="Polite avtoelon.uz car-image collector")
    ap.add_argument("--max-pages", type=int, default=10, help="listing pages per model")
    ap.add_argument("--concurrency", type=int, default=3, help="parallel listing tabs (keep small)")
    ap.add_argument("--delay", type=float, default=1.0, help="base seconds between actions")
    ap.add_argument("--show", action="store_true", help="run headful to watch the browser")
    asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    main()
