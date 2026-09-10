"""year_audit.py — read-only: walk a class's category listing pages and record the model year
shown on each listing card. Purpose: measure how many cars in a *generic* class (tracker,
malibu) are actually the newer generation that has its own class (tracker_2, malibu2).
Costs one page load per listing page (~6-8 per class), not one per listing.

Usage: python scripts/year_audit.py --classes chevrolet_tracker,chevrolet_malibu --max-pages 10 --out year_audit.csv
"""
import argparse, asyncio, csv, re, sys
from collections import Counter
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, "scripts")
from avtoelon_scraper import PAGE_PARAM, LISTING_LINK_SELECTOR, LISTING_HREF_RE, MODELS  # noqa: E402
from playwright.async_api import async_playwright  # noqa: E402

YEAR_RE = re.compile(r"(?<!\d)(19[89]\d|20[0-2]\d)(?!\d)")
JS = """els => els.map(e => {
  const c = e.closest('[class*="list-item"], [class*="card"], li, article, [class*="item"]') || e.parentElement.parentElement;
  return {href: e.href, text: (c && c.innerText ? c.innerText : '').replace(/\s+/g, ' ').slice(0, 400)};
})"""

async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--classes", required=True)
    ap.add_argument("--max-pages", type=int, default=10)
    ap.add_argument("--out", default="year_audit.csv")
    ap.add_argument("--delay", type=float, default=4.0)
    args = ap.parse_args()
    classes = [c.strip() for c in args.classes.split(",") if c.strip()]
    rows = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="chrome", headless=True)
        page = await (await browser.new_context()).new_page()
        for cls in classes:
            base = MODELS[cls]; seen = set()
            for n in range(1, args.max_pages + 1):
                url = base if n == 1 else base + PAGE_PARAM.format(n=n)
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                except Exception as e:
                    print(f"[{cls}] page {n} failed: {str(e)[:80]}"); break
                await asyncio.sleep(args.delay)
                cards = await page.eval_on_selector_all(LISTING_LINK_SELECTOR, JS)
                hits = 0
                for c in cards:
                    m = LISTING_HREF_RE.search(c["href"] or "")
                    if not m: continue
                    lid = re.search(r"(\d+)", m.group(0)).group(1)
                    if lid in seen: continue
                    seen.add(lid); hits += 1
                    y = YEAR_RE.search(c["text"]); year = int(y.group(1)) if y else 0
                    rows.append({"class": cls, "listing_id": lid, "year": year, "card_text": c["text"][:200]})
                print(f"[{cls}] page {n}: +{hits} (total {len(seen)})")
                if hits == 0: break
        await browser.close()
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["class", "listing_id", "year", "card_text"]); w.writeheader(); w.writerows(rows)
    for cls in classes:
        hist = Counter(r["year"] for r in rows if r["class"] == cls)
        print(f"\n{cls}: {sum(hist.values())} listings; years: " + ", ".join(f"{y}:{n}" for y, n in sorted(hist.items())))
    print(f"-> {args.out}")

asyncio.run(main())
