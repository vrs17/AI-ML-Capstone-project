# Manual Golden Review — procedure

This is the one pipeline stage with **no executable script**: it is a human
verification pass over the cleaned crops. It is documented here so the step is
**auditable and reproducible** even though it was done by hand, and so the project
can be explained end-to-end with every stage traceable to the repository.

## Why this stage exists

The automated pipeline (scrape → dedup → YOLO → CLIP → split, see
`notebooks/data_gate.ipynb`) produced a clean **5-class** dataset, and the first model
(`notebooks/model_gate.ipynb`) reached 92.3% accuracy / 91.7% macro-F1 on the sealed
test. Error analysis on that model exposed two problems that no automated step had fully
solved (see `data/README.md`, Issue 5):

1. **Residual wrong-model contamination** — a few non-target cars (BYD, Lada, Cruze,
   Malibu) still sat under one of the five labels.
2. **No way to say "unknown"** — a closed-set 5-way softmax *must* force every car into
   one of five, so a foreign make was labelled with high confidence (e.g. a BYD scored
   0.97 as `spark`). Wrong behaviour for a camera in the field.

The fix is data-centric: a human verifies every crop, and cars that are none of the five
are collected into a new **`others`** class (open-set recognition).

## Inputs → Outputs

- **Input:** the 5-class leakage-safe split produced by `data_gate.ipynb`
  (`train/val/test` → `{cobalt, damas, gentra, nexia3, spark}`).
- **Output:** a **6-class** dataset in the identical layout, with an added `others`
  folder in each split — the dataset the 6-class Model Gate trains on.

## The verification rule (applied to every crop)

Each image is judged against the folder it currently sits in:

| Situation | Action |
|---|---|
| Correctly matches its folder | keep it |
| Actually a **different one of the five** | move it to the correct model folder |
| A car but **none of the five** (BYD, Lada, Kia, Cruze, Malibu, Captiva, …) | move it to `others/` |
| Not a usable exterior car photo (interior, engine, document, wheel) | remove it |
| A sedan, but **genuinely cannot tell** Cobalt vs Gentra vs Nexia 3 apart | set aside to `review/`, do **not** guess |

Reference used for the five models:

- `cobalt` = Chevrolet Cobalt (compact sedan)
- `nexia3` = Chevrolet / Ravon Nexia 3 (compact sedan)
- `spark`  = Chevrolet Spark (small hatchback)
- `gentra` = Chevrolet / Daewoo Gentra (compact sedan)
- `damas`  = Chevrolet / Daewoo Damas (tiny boxy microvan)

The conservative handling of the Cobalt/Gentra/Nexia 3 look-alike trio is deliberate:
guessing on an ambiguous sedan would inject the exact label noise this pass is meant to
remove. Ambiguous sedans are set aside, not forced.

## How it was done

A manual eye-run over the split folders (optionally assisted by a vision model as a
second opinion). Every image was viewed and moved per the rule above. Only file moves —
no pixels were altered.

## Leakage safety is preserved by construction

The automated split is grouped **by listing** so no car's photos span `train/val/test`
(verified 0 cross-split listings in `data_gate.ipynb`). The manual pass only moves images
**within their existing split** (correcting the class, or into `others`/`review` in the
same split), so that guarantee is preserved. It can be re-confirmed on the final dataset:

```python
import glob
from pathlib import Path
from collections import defaultdict

def listing_of(p):                       # filenames are <hash>_<LISTINGID>_<n>.jpg
    parts = Path(p).stem.split('_'); return parts[1] if len(parts) >= 3 else parts[0]

seen = defaultdict(set)
for f in glob.glob('dataset/*/*/*.jpg'):
    seen[listing_of(f)].add(Path(f).parts[-3])      # which split this listing lands in
leaked = [l for l, s in seen.items() if len(s) > 1]
print(f"listings spanning >1 split: {len(leaked)}")   # must be 0
```

## What this produces for the next stage

The 6-class dataset (`cobalt, damas, gentra, nexia3, others, spark`) is re-zipped as
`dataset_split.zip` and uploaded to Drive. The Model Gate notebook derives its class list
from the folders, so it becomes a 6-class model automatically — no code change, just the
new data.
