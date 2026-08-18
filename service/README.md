# Recognizer Service

Production backend for the Uzbek Car Model Recognizer: **YOLO crop → ConvNeXt+ArcFace @384 →
calibrated trust layer**, behind a FastAPI HTTP API with a built-in **operations console**.

Sized for a **single 8 GB VRAM GPU**. In fp16 the classifier weights are ~56 MB and YOLO11s is
smaller still — the real consumer is activation memory, which is why `MAX_BATCH` is capped.
Typical steady-state usage is **well under 2 GB**, leaving room for other work on the machine.

---

## 1. Put the model in place

Copy the two files out of `modelgate_v2_artifacts.zip` (produced by `notebooks/model_gate_v2.ipynb`,
then updated by `notebooks/trust_layer.ipynb`):

```
service/artifacts/
├── model.pt        # trained weights
└── config.json     # classes, img_size, mean/std, head, temperature, abstain_threshold
```

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
| `GET` | `/video/summary/{id}` | live tally, throughput, auto-handled share |
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
feed, unique-vehicle count and throughput, how every vehicle was handled, fleet mix, and a
rolling **decision log**. It reports *business* events ("14:32:07 · Chevrolet Cobalt · vehicle #14")
rather than tensors. `F` toggles presenter fullscreen.

The **value model** panel is deliberately a calculator, not a projection: both inputs are on
screen and editable, and the outputs are arithmetic over those inputs and the throughput actually
observed in the current session. The **measured reliability** panel quotes the sealed-test numbers
and labels them as such.

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
- The console and inspector share `static/theme.css`; the overlay palette in `overlay.py` and the
  CSS custom properties are kept in sync by hand — change both together.
