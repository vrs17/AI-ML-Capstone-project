# Recognizer Service

Production backend for the Uzbek Car Model Recognizer: **YOLO crop → ConvNeXt+ArcFace @384 →
calibrated trust layer**, behind a FastAPI HTTP API with a built-in **operations console**.

Sized for a **single 8 GB VRAM GPU**. In fp16 the classifier weights are ~56 MB and YOLO11s is
smaller still — the real consumer is activation memory, which is why `MAX_BATCH` is capped.
Typical steady-state usage is **well under 2 GB**, leaving room for other work on the machine.

---

## 1. Put the model in place

**Already done for you** — `artifacts/model.pt` and `artifacts/config.json` are committed
to this repository, so a fresh clone runs immediately. Skip to §2 unless you have
retrained.

> **⚠️ Temporary.** Committing weights is normally wrong: they are binaries, they bloat
> history permanently, and each new version adds another 56 MB that cannot be removed
> without rewriting history. They are here **deliberately and temporarily** for quick
> testing. See *Temporary decisions* in [`../SETUP.md`](../SETUP.md) for how to move them
> to a GitHub Release instead.
>
> They are stored in **fp16 (~56 MB)** because the fp32 checkpoint is ~114 MB and GitHub
> rejects any file over 100 MB. Measured cost: none — identical top-1, max logit
> difference 0.0008, and the service already computes in fp16 on GPU.

After retraining, repack before committing:

```bash
python ../scripts/pack_weights.py --src /path/to/modelgate_v2_artifacts
```

<details><summary>Placing them by hand</summary>

Copy the two files out of `modelgate_v2_artifacts.zip` (produced by `notebooks/model_gate_v2.ipynb`,
then updated by `notebooks/trust_layer.ipynb`):

```
service/artifacts/
├── model.pt        # trained weights
└── config.json     # classes, img_size, mean/std, head, temperature, abstain_threshold
```

</details>

The service reads the **temperature** and **abstain threshold** straight from `config.json`, so the
API enforces exactly the operating point you measured (T = 2.894, threshold = 0.857 →
99.0% precision at 83.5% coverage). If `abstain_threshold` is absent, abstention is disabled and
the service says so in `/health`.

## 2. Run it

**Docker (recommended)**
```bash
docker compose up --build          # needs the NVIDIA container toolkit for GPU
```

**Local**
```bash
python -m venv .venv && source .venv/bin/activate
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121
pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 8000
```

Open <http://localhost:8000> for the **operations console**, `/photo` for the snapshot
inspector, or `/docs` for OpenAPI.

## 3. API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/` | **operations console** (live video, counters, decision log) |
| `GET` | `/photo` | snapshot inspector (one image, with the intermediate numbers) |
| `GET` | `/health` | readiness, device, VRAM, active thresholds |
| `GET` | `/metrics` | request counters + realised coverage |
| `POST` | `/predict` | one image (multipart `file`) |
| `POST` | `/predict/batch` | up to 32 images (multipart `files`) |
| `GET` | `/video` | alias of `/` (kept for older bookmarks) |
| `POST` | `/video/upload` | start a tracking job from a video file |
| `POST` | `/video/camera?source=0` | start from a webcam (`0`) or an RTSP/HTTP URL |
| `GET` | `/video/stream/{id}` | annotated MJPEG stream (`?raw=1` for no overlay) |
| `GET` | `/video/summary/{id}` | live tally, throughput, auto-handled share, **signal quality** |
| `GET` | `/video/events/{id}` | ordered decision log (`?after=<seq>` for the tail) |

```bash
curl -F "file=@car.jpg" http://localhost:8000/predict
```

```json
{
  "decision": "answer",
  "model": "nexia3",
  "display": "Chevrolet Nexia 3",
  "confidence": 0.974,
  "probabilities": {"cobalt":0.011,"damas":0.001,"gentra":0.009,"nexia3":0.974,"others":0.003,"spark":0.002},
  "detection": {"found": true, "box": [88,140,912,690], "score": 0.93, "ms": 11.4},
  "timing_ms": {"detect": 11.4, "classify": 23.7, "total": 38.2},
  "reason": "Chevrolet Nexia 3 at 97.4% confidence."
}
```

### The four decisions

| `decision` | Meaning | Caller should |
|---|---|---|
| `answer` | One of the five known models, above threshold | count it |
| `reject` | A vehicle outside the catalogue (`others`) | log as "other vehicle" |
| `abstain` | Below the confidence threshold | send to a human |
| `no_car` | The detector found no car | discard the frame |

Only `answer` carries the 99%-precision guarantee — that is the whole point of the trust layer.

## 3b. Video mode

`/video` runs the same pipeline over a video source, with two additions:

- **Tracking (ByteTrack).** Every car gets a persistent track id, so the system counts
  **unique vehicles**, not per-frame hits — which is what a gas-station operator actually wants.
- **Temporal voting.** Each track is classified on several frames. Every look is judged with the
  *calibrated* threshold, and the track takes the **majority of the looks that passed**. We
  deliberately do **not** average raw probabilities: the threshold was calibrated on single-frame
  confidences, and one blurred or half-occluded frame would otherwise drag a certain track below
  it. A weak look simply does not vote.

Once a track has enough confident looks it stops being re-classified — cheaper *and* steadier.

On CPU the classifier cannot keep up frame-for-frame at 384 px, so the processor skips frames
(`frame_stride`, default 3 on CPU / 1 on GPU). This is reported in `/video/summary` rather than
hidden.

## 3c. The two screens

**`/` — Operations console.** What a site operator (and a stakeholder) looks at. Live annotated
feed, unique-vehicle count and throughput, how every vehicle was handled, fleet mix, a
**signal-quality** readout, and a rolling **decision log**. It reports *business* events ("14:32:07 · Chevrolet Cobalt · vehicle #14")
rather than tensors. `F` toggles presenter fullscreen.

The **value model** panel is deliberately a calculator, not a projection: both inputs are on
screen and editable, and the outputs are arithmetic over those inputs and the throughput actually
observed in the current session. The **measured reliability** panel quotes the sealed-test numbers
and labels them as such.

Both screens accept a **drop anywhere on the window** — video on the console, images on the
inspector — with a full-window target rather than a small dashed box. That matters for more than
convenience: without a document-level handler, a miss makes the browser navigate to the dropped
file, which silently kills a running video session. A wrong file type is named and refused instead
of being uploaded, and the inspector also accepts a **pasted** image from the clipboard.

**`/photo` — Snapshot inspector.** The diagnostic view of a single frame: the detector's box
animated onto the photo, the verdict, the calibrated per-class probabilities, and stage timings.
Open this when you want to know *why*.

### Why the detection graphics are drawn server-side

`overlay.py` burns the brackets, scan sweep, lock-on animation and label chip **into the frame**,
inside the same pass that produced the box. The tempting alternative — stream clean frames and
draw boxes in a `<canvas>` — decouples geometry from pixels, so on moving cars the box visibly
trails the vehicle by one network round-trip. Drawing in-frame makes the graphics pixel-locked by
construction. Chrome that does *not* need to track a car (counters, feed, panels) stays in HTML.

Label text is rendered with PIL into a small cached RGBA patch and alpha-composited, so the type
is anti-aliased rather than OpenCV's Hershey strokes; a settled track re-uses the same cached
patch every frame.

`?raw=1` on the stream returns the un-annotated feed — useful for showing the before/after.

## 3d. Why a photo recognised at 99% can still fail on camera

This is the single most common surprise, and the console now measures it directly.

The classifier is fed `Resize(438) → CenterCrop(384)`. A **listing photo** has the car filling
the frame, so the crop is ~1000 px wide and gets *downsampled* into the network — sharp. A
**camera** sees the same car far smaller; a crop narrower than 438 px is *upscaled*, inventing
pixels that were never captured. The grille, lamp and badge detail that separates Cobalt from
Gentra from Nexia 3 simply is not in the image, so confidence collapses and the trust layer —
correctly — abstains rather than guessing.

`GET /video/summary/{id}` reports this as `signal`:

```json
"signal": {"median_crop_px": 196, "good_crop_px": 438, "quality": "poor",
           "advice": "That is upscaled 2.2x into the network, …"}
```

and the console shows it as a **Signal quality** panel. It reports the **closest** look at each
car, not the average, because that is the look the decision is made on — and it names which of two
different problems you have: `limit: "camera"` (even a car filling the frame is too small — the
feed's resolution is the ceiling) or `limit: "placement"` (the camera can resolve enough, cars just
never come close enough).

### "So can we just work at 163 px?"

Partly, and the size of "partly" is measurable rather than arguable. Three levers, cheapest first:

1. **Judge the closest look (already done, costs nothing).** A car crossing a forecourt is sampled
   many times and grows as it approaches. Those looks are not equal evidence, so a look below
   `MIN_VOTE_PX` (default 200) does not vote at all, and the rest are weighted by crop size — the
   close look decides the track. Far looks still draw a box and still get counted; they just do not
   get to name the car. A track that never comes close abstains as **"Too far to identify"**, which
   is a different statement from "Needs a check".
2. **Move or zoom the camera, or raise the feed's resolution.** At 1080p, 438 px is a car filling
   ~23% of frame width; 163 px is ~8.5%. That is a placement number, not a model number.
3. **Retrain at low resolution.** Genuinely helps — fine-tune with aggressive downscale
   augmentation so the model learns the coarse cues (silhouette, proportion, lamp-cluster shape)
   that survive. But it costs accuracy on the near-identical sedans, and there is a floor below
   which Cobalt, Gentra and Nexia 3 are not distinguishable by anything.

**Measure your own floor before choosing.** `scripts/resolution_sweep.py` shrinks the sealed test
set to each candidate size, pushes it through the unchanged production pipeline, and reports
accuracy, macro-F1, precision and coverage at each:

```bash
python scripts/resolution_sweep.py --artifacts service/artifacts --data data/dataset/test
```

It ends by printing the smallest capture size that still holds 99% and 95% precision — your
operating envelope, from your data. Note that lowering `abstain_threshold` is *not* on this list:
it would convert abstentions into wrong answers and forfeit the precision the product is sold on.

Two related defects were fixed at the same time:

- The video path used to **downscale the frame to 1280 px wide before cropping**, throwing away a
  third of the linear resolution on a 1080p source. Detection and cropping now run at the source
  resolution; only the frame that is *displayed* is shrunk.
- `detect_and_crop()` passed a **PIL RGB array to ultralytics, which expects BGR**, so the photo
  path was detecting on channel-swapped input. Both paths now feed BGR.

Video also uses a finer detector grid and a lower area floor than the photo path, since a camera
sees cars far smaller: `VIDEO_DET_IMGSZ` (default 960) and `VIDEO_AREA_FLOOR` (default 0.010).

## 4. Tuning for your box

| Env var | Default | Notes |
|---|---|---|
| `DEVICE` | `auto` | `cuda` / `cpu` |
| `FP16` | `true` | halves VRAM and speeds up ~2× on GPU |
| `MAX_BATCH` | `8` | raise to 16 if VRAM allows; lower if you see OOM |
| `MAX_CONCURRENT` | `16` | in-flight requests |
| `CHANNELS_LAST` | `true` | better conv throughput on NVIDIA |
| `COMPILE` | `false` | `torch.compile`; faster steady-state, slow first call |
| `DET_CONF` | `0.25` | detector confidence floor |
| `MAX_UPLOAD_MB` | `12` | per-file upload cap |
| `VIDEO_DET_IMGSZ` | `960` | detector grid for video — cameras see smaller cars than photos do |
| `VIDEO_AREA_FLOOR` | `0.010` | smallest box worth tracking, as a fraction of frame area |
| `MIN_VOTE_PX` | `200` | smallest crop allowed to *name* a car; smaller ones are still tracked and counted |

GPU access is serialised by a lock, so concurrent HTTP requests are safe; throughput scales with
batch size rather than worker count. Run **one** uvicorn worker per GPU — extra workers would each
load their own copy of the model.

## 5. Notes

- Preprocessing (`Resize(438) → CenterCrop(384) → ImageNet normalize`) mirrors
  `notebooks/model_gate_v2.ipynb` exactly. Changing it invalidates the measured accuracy.
- EXIF orientation is honoured, so phone photos are not silently rotated.
- The model loads with `strict=True`: a checkpoint/architecture mismatch fails loudly at startup
  instead of silently serving a partly-random model.
- If `ultralytics` or the YOLO weights are unavailable the service still starts and classifies the
  **whole image** (no crop), and `/health` reports `detector: false`.
- `static/upload.js` holds the shared drop handling. It counts dragenter/dragleave rather than
  treating them as on/off (they fire per child element, so a naive version flickers), calls
  `preventDefault()` on `dragover` (without it `drop` never fires), and ignores drags that carry
  no files. It is loaded **without `defer`** — a deferred script runs after the inline script at
  the end of `<body>`, which is where `installDrop()` is called.
- The console and inspector share `static/theme.css`; the overlay palette in `overlay.py` and the
  CSS custom properties are kept in sync by hand — change both together.
- The live panel takes its height from the video itself (capped at `--stage-h`, 66vh), so a 4:3 or
  portrait feed is not letterboxed into a 16:9 box.
- `ultralytics` keeps tracker state on the model object, and its models are not thread-safe, so
  detector calls are serialised behind `recognizer._det_lock`.
