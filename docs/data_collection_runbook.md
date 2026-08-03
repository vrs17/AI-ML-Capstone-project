# Runbook — Test the Uzbek Car Image Scraper

**You are a coding agent running on the user's local machine.** Your job is to clone this
project, run a small test of the image scraper, verify it works, and **report back a specific
set of results** (Section 5) so the scraper's two image/link selectors can be finalized.

Do **not** scale up scraping, change concurrency, or remove delays. This is a small test run only.

---

## 0. Precondition (important)

This scraper targets **avtoelon.uz**, which is only reachable from a normal browser/residential
network (e.g., in Uzbekistan). Before doing anything, confirm the site loads:

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://avtoelon.uz/robots.txt
```

- `200` → good, continue.
- `403`/timeout → you are on a network that blocks the site. **Stop** and tell the user the
  scraper must be run from a machine that can open avtoelon.uz in a browser.

---

## 1. Clone

- **GitHub account (owner):** `vrs17`
- **Repository:** `AI-ML-Capstone-project`
- **Branch:** `claude/aiml-capstone-planning-fnkas1`

```bash
git clone --branch claude/aiml-capstone-planning-fnkas1 \
  https://github.com/vrs17/AI-ML-Capstone-project.git
cd AI-ML-Capstone-project
```

> If the repo is private and the clone asks for credentials, authenticate first
> (`gh auth login`, or use a personal access token) and retry.

---

## 2. Set up

```bash
python3 -m venv .venv && source .venv/bin/activate   # optional but recommended
pip install playwright
playwright install chromium
```

---

## 3. Run the test (small, visible browser)

```bash
python scripts/avtoelon_scraper.py --show --max-pages 3 --concurrency 2
```

- `--show` opens a visible browser so you can see what it does.
- Keep `--max-pages 3` and `--concurrency 2` for this test. Do not raise them.
- Let it finish. It saves images to `data/raw/<model>/` and writes `data/raw/manifest.csv`.

---

## 4. Verify

```bash
# per-model image counts
for d in data/raw/*/; do echo "$d: $(ls "$d" 2>/dev/null | wc -l) files"; done

# manifest exists and has rows
wc -l data/raw/manifest.csv
head -n 6 data/raw/manifest.csv

# list a few saved filenames
ls data/raw/cobalt/ 2>/dev/null | head -n 5
```

Then **open 3–4 of the saved images** and judge: are they **exterior photos of whole cars**
(good), or interior/dashboard/engine/document shots and logos (bad)?

---

## 5. Report back EXACTLY this

Copy this template, fill it in, and return it to the user verbatim:

```
SITE REACHABLE: yes/no  (curl robots.txt status = ___)

PER-MODEL COUNTS (images saved):
  cobalt: ___    nexia3: ___    spark: ___    gentra: ___    damas: ___
TOTAL IMAGES: ___
MANIFEST ROWS: ___

SAMPLE FILENAMES (5):
  ...

MANIFEST FIRST 5 ROWS:
  ...

IMAGE QUALITY: are they exterior whole-car photos? yes / mostly / no
  notes: ___

ERRORS / WARNINGS printed in the console (paste any):
  ...

IF 0 LISTINGS FOUND for a model:
  Open that model's search page in the browser, right-click one car card ->
  "Copy link", and paste ONE listing URL here: ___

IF LISTINGS FOUND BUT 0 IMAGES:
  Open one car listing, right-click a main photo -> "Inspect", and paste the
  full <img ...> tag here: ___
  Also paste the image host domain (e.g. https://static.avtoelon.uz/...): ___
```

---

## Rules (do not deviate)

- **Compliance is already built in** (robots.txt gate, whitelisted `?page=` pagination,
  stripped disallowed query params). Do not add headers, proxies, or bypasses.
- **Politeness:** do not increase `--concurrency`, do not lower `--delay`, do not raise
  `--max-pages` beyond 3 for this test.
- You **may** fix an obviously-wrong selector *only if the user asks you to after seeing the
  report*; otherwise just report. Do not commit or push anything unless the user asks.
- If anything is ambiguous, stop and report rather than guessing.
