# Recognizer Service

Production backend for the Uzbek Car Model Recognizer: **YOLO crop → ConvNeXt+ArcFace @384 →
calibrated trust layer**, behind a FastAPI HTTP API with a built-in web UI.

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

Open <http://localhost:8000> for the UI, or <http://localhost:8000/docs> for OpenAPI.

## 3. API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/` | web UI |
| `GET` | `/health` | readiness, device, VRAM, active thresholds |
| `GET` | `/metrics` | request counters + realised coverage |
| `POST` | `/predict` | one image (multipart `file`) |
| `POST` | `/predict/batch` | up to 32 images (multipart `files`) |

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
