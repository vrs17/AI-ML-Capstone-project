#!/usr/bin/env python3
"""curate_models.py — turn data/raw/discovered_models.csv into the scraper's class list.

Encodes the class-selection policy so it is reviewable and re-runnable, instead of a 3 a.m.
judgment call:

  1. Volume floor (--min-listings, default 400): a 2,000-image target needs roughly 300
     listings at ~7 kept photos each; below ~400 listed cars a model cannot reach a useful
     sample and would be silently under-represented. Dropped models are printed with counts.
  2. Brand-twin pruning: several models are sold under both Daewoo and Chevrolet badges with
     the SAME body (Damas, Matiz, Labo, Lacetti/Gentra ancestry). Two labels for one visual
     class would put near-identical cars on both sides of a class boundary; keep the
     higher-volume brand variant, drop the twin (also printed).
  3. Keyword guard: every kept model gets title keywords - Latin plus common Cyrillic
     spellings - which scrape_one_listing uses to reject promoted listings for other cars.
     Digit-bearing models (Nexia 2/3, Malibu 2, VAZ 21xx) match with and without the space.

Usage:
    python scripts/curate_models.py [--min-listings 400] [--top 40] [--write-config]
Prints ready-to-paste MODELS and MODEL_KEYWORDS dicts and writes
data/raw/curated_models.csv with the keep/drop decision per row.
With --write-config it also writes data/raw/models_config.py, which the scraper imports
in preference to its built-in five-model default — no hand-edit needed between discovery
and collection.
"""
import argparse
import csv
import sys
from pathlib import Path

DISCOVERED = Path("data/raw/discovered_models.csv")
OUT = Path("data/raw/curated_models.csv")

# Latin base -> extra Cyrillic/variant spellings seen in listing titles. The Latin model
# token itself is always included automatically (with a spaced variant for digit models).
CYRILLIC = {
    "cobalt": ["кобальт"], "gentra": ["джентра", "гентра"], "spark": ["спарк"],
    "damas": ["дамас"], "matiz": ["матиз"], "nexia": ["нексия"],
    # digit-suffixed single-token slugs: the slug alone ("nexia3") never appears in real
    # titles ("Chevrolet Nexia 3"), so spell out the spaced Latin + Cyrillic forms.
    "nexia2": ["nexia 2", "нексия 2", "нексия"],
    "nexia3": ["nexia 3", "нексия 3", "нексия"],
    "malibu2": ["malibu 2", "malibu", "малибу"],
    "vesta": ["веста"], "lada-r90": ["largus", "ларгус", "r90"],
    "song-plus-dm-i-champion": ["song plus", "song", "сонг"],
    "song-plus-ev-champion": ["song plus", "song", "сонг"],
    "lacetti": ["лачетти", "ласетти"], "labo": ["лабо"], "captiva": ["каптива"],
    "malibu": ["малибу"], "onix": ["оникс"], "epica": ["эпика"],
    "equinox": ["эквинокс"], "monza": ["монза"], "orlando": ["орландо"],
    "tracker": ["трекер", "трэкер"], "traverse": ["траверс"], "tico": ["тико"],
    "tahoe": ["тахо"], "trailblazer": ["трейлблейзер"],
    "sonata": ["соната"], "tucson": ["туксон", "тюксон"], "santa-fe": ["санта фе", "санта-фе"],
    "santa": ["санта"], "elantra": ["элантра"], "sonet": ["сонет"], "seltos": ["селтос"],
    "sorento": ["соренто"], "sportage": ["спортейдж", "спортаж"], "carnival": ["карнивал"],
    "k-5": ["k5", "к5", "к-5"], "k5": ["к5"],
    "jolion": ["джолион"], "chazor": ["чазор"], "song": ["сонг"],
    "tiggo": ["тигго"], "arrizo": ["арризо", "аризо"],
    "jetour": ["джетур"], "monjaro": ["монжаро"], "coolray": ["кулрей"],
    "atlas": ["атлас"], "azkarra": ["азкарра"], "emgrand": ["эмгранд"],
}

# (brand, model) pairs that are the same body sold under two badges; key = the visual class.
BRAND_TWINS = [
    ({"chevrolet", "daewoo"}, "damas"),
    ({"chevrolet", "daewoo"}, "matiz"),
    ({"chevrolet", "daewoo"}, "labo"),
    ({"chevrolet", "daewoo"}, "lacetti"),
    ({"chevrolet", "daewoo"}, "matiz-best"),
]

# Catalogue slugs the SITE aliases to one listing pool — verified live 2026-08-27 by
# comparing result counts and first-page listing IDs: /avto/chevrolet/gentra/ and
# /avto/chevrolet/lacetti/ return the identical 4,452 listings in identical order, and the
# matiz / matiz-best pools match the same way. Collecting an alias would duplicate a class.
SLUG_ALIASES = {"lacetti": "gentra", "matiz-best": "matiz"}


def keywords_for(model: str) -> tuple:
    """Latin token(s) + Cyrillic variants for a catalogue model slug like 'nexia-3'."""
    toks = model.lower().split("-")
    spaced = " ".join(toks)                       # "nexia 3"
    joined = "".join(toks)                        # "nexia3"
    kws = {spaced}
    if joined != spaced:
        kws.add(joined)
    base = toks[0]
    for extra in CYRILLIC.get(model.lower(), CYRILLIC.get(base, [])):
        kws.add(extra)
        # digit models: also pair the Cyrillic base with the digit ("нексия 3")
        if len(toks) > 1 and toks[-1].isdigit() and extra.isalpha():
            kws.add(f"{extra} {toks[-1]}")
    return tuple(sorted(kws))


def main() -> None:
    # Same guard as the scraper: Windows consoles default to cp1252, which cannot
    # print the Cyrillic keywords this script exists to emit.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-listings", type=int, default=400)
    ap.add_argument("--top", type=int, default=40)
    ap.add_argument("--write-config", action="store_true",
                    help="also write data/raw/models_config.py for the scraper to import")
    args = ap.parse_args()

    if not DISCOVERED.exists():
        sys.exit(f"run the scraper with --discover first ({DISCOVERED} not found)")
    rows = list(csv.DictReader(DISCOVERED.open(encoding="utf-8")))
    if not rows:
        sys.exit(f"{DISCOVERED} is empty")
    for r in rows:
        r["listings"] = int(r["listings"] or 0)
    rows.sort(key=lambda r: -r["listings"])

    decisions = []
    kept, seen_twin = [], {}
    for r in rows:
        brand, model, n = r["brand"].lower(), r["model"].lower(), r["listings"]
        why = ""
        if n < args.min_listings:
            why = f"below volume floor ({n} < {args.min_listings})"
        elif model in SLUG_ALIASES and any(k["model"].lower() == SLUG_ALIASES[model]
                                           for k in kept):
            why = (f"same listing pool as {SLUG_ALIASES[model]} "
                   f"(site aliases the family; verified by identical count + first-page IDs)")
        else:
            twin_key = next((t for bs, t in BRAND_TWINS if model == t and brand in bs), None)
            if twin_key:
                if twin_key in seen_twin:
                    why = (f"brand twin of {seen_twin[twin_key]} "
                           f"(same body, lower volume: {n})")
                else:
                    seen_twin[twin_key] = f"{brand}/{model} ({n})"
        if not why and len(kept) >= args.top:
            why = f"over --top {args.top} cap"
        decisions.append({**r, "decision": "drop: " + why if why else "keep"})
        if not why:
            kept.append(r)

    print(f"# kept {len(kept)} of {len(rows)} catalogue models "
          f"(floor {args.min_listings}, cap {args.top})\n")
    print("MODELS = {")
    for r in kept:
        label = f'{r["brand"]}_{r["model"]}'.replace("-", "_").lower()
        print(f'    "{label}":{" " * max(1, 26 - len(label))}"{r["url"]}",'
              f'   # {r["listings"]:,} listings')
    print("}\n")
    print("MODEL_KEYWORDS = {")
    for r in kept:
        label = f'{r["brand"]}_{r["model"]}'.replace("-", "_").lower()
        kws = keywords_for(r["model"])
        print(f'    "{label}":{" " * max(1, 26 - len(label))}{kws!r},')
    print("}\n")

    dropped = [d for d in decisions if d["decision"] != "keep"]
    if dropped:
        print(f"# dropped {len(dropped)}:")
        for d in dropped:
            print(f"#   {d['brand']}/{d['model']}: {d['decision']} ({d['listings']:,})")

    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(decisions[0].keys()))
        w.writeheader()
        w.writerows(decisions)
    print(f"\n# decisions -> {OUT}")

    if args.write_config:
        cfg = Path("data/raw/models_config.py")
        with cfg.open("w", encoding="utf-8") as f:
            f.write("# Generated by scripts/curate_models.py — the scraper imports this in\n"
                    "# preference to its built-in default. Regenerate rather than hand-edit.\n"
                    "MODELS = {\n")
            for r in kept:
                label = f'{r["brand"]}_{r["model"]}'.replace("-", "_").lower()
                f.write(f'    "{label}": "{r["url"]}",  # {r["listings"]:,} listings\n')
            f.write("}\n\nMODEL_KEYWORDS = {\n")
            for r in kept:
                label = f'{r["brand"]}_{r["model"]}'.replace("-", "_").lower()
                f.write(f'    "{label}": {keywords_for(r["model"])!r},\n')
            f.write("}\n")
        print(f"# scraper config -> {cfg}")


if __name__ == "__main__":
    main()
