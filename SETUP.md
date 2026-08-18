# SETUP — start the project

**For an AI agent:** this is an executable runbook. Read it top to bottom and run the
steps in order. Every step states what to expect; if the output does not match, the fix
is in [Troubleshooting](#troubleshooting) — apply it and continue rather than improvising.

**For a human:** run [step 1](#step-1--automatic-setup) and skip to [step 4](#step-4--use-it).

> **Note on committed model weights.** `service/artifacts/model.pt` is checked into git,
> which is normally wrong — binary weights do not belong in version control. It is there
> **deliberately and temporarily** so this project starts from a clone with nothing else
> to download. See [Temporary decisions](#temporary-decisions) before treating this repo
> as long-lived.

---

## What you are starting

A two-stage vehicle recognition service: a YOLO11s detector crops the car, a fine-tuned
ConvNeXt-Tiny + ArcFace classifier names the model, and a calibrated trust layer decides
whether to answer, reject it as outside the catalogue, or pass it to a human. It serves a
web operations console with real-time video, plus a JSON API.

---

## Preconditions

| Need | Check | If missing |
|---|---|---|
| Python 3.10–3.13 | `python --version` | install from python.org; 3.14 has patchy wheels for this stack |
| git | `git --version` | install git |
| ~4 GB free disk | | torch + CUDA wheels are large |
| Internet, once | | to install packages and fetch `yolo11s.pt` (~19 MB) |

**Optional:** an NVIDIA GPU. `nvidia-smi` tells you. Without one everything still runs on
CPU — slower, and video processes every 3rd frame instead of every frame.

---

## Step 1 — automatic setup

```bash
git clone https://github.com/vrs17/AI-ML-Capstone-project.git
cd AI-ML-Capstone-project
python scripts/setup.py
```

This creates `service/.venv`, detects whether you have an NVIDIA GPU, installs the right
torch build and the service dependencies, and confirms the weights are present. It is
**idempotent** — safe to re-run, and it reuses anything already installed.

Expect, in order:

```
  ok   Python 3.12.x
  ok   virtualenv service/.venv
  ok   torch 2.x.x+cu128 · CUDA available          (or "CUDA not available (CPU)")
  ok   service dependencies
  ok   weights 56 MB · 6 classes · threshold 0.857
```

Then it prints the exact command to start the service. **If any line says `FAIL`, it also
prints the fix — apply that and re-run.** Do not continue past a `FAIL`.

To verify without installing anything: `python scripts/setup.py --check`
To force CPU torch on a machine that has a GPU: `python scripts/setup.py --cpu`

<details>
<summary>Manual equivalent, if you would rather not run the script</summary>

```bash
cd service
python -m venv .venv

# activate:  Windows  .\.venv\Scripts\activate      Linux/macOS  source .venv/bin/activate

# GPU — check your CUDA version at https://pytorch.org first:
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
# CPU only:
# pip install torch torchvision

pip install -r requirements.txt
```
</details>

---

## Step 2 — start it

```bash
cd service
../service/.venv/bin/python -m uvicorn app:app --port 8000     # Linux / macOS
.\.venv\Scripts\python.exe -m uvicorn app:app --port 8000      # Windows
```

Calling the venv's python directly avoids PowerShell's execution-policy prompt that
`activate` triggers.

Wait for this line — it means the model is loaded and warmed up:

```
ready | device=cuda fp16=True classes=['cobalt','damas','gentra','nexia3','others','spark'] T=2.894 thr=0.857...
```

`device=cpu fp16=False` is fine too, just slower.

---

## Step 3 — verify

```bash
curl http://localhost:8000/health
```

Expect `"ready": true` and a `classes` array of six. Anything else means the model did not
load — read the server output, it says why.

Then open **http://localhost:8000** in a browser. You should see the operations console
with a "No source connected" panel. Both of these must work before you call setup done.

---

## Step 4 — use it

| What | Where |
|---|---|
| Live video — drop a clip or connect a camera | http://localhost:8000 |
| One photo, with the intermediate numbers | http://localhost:8000/photo |
| API reference | http://localhost:8000/docs |

Drag a video or image anywhere on the page. For a camera, enter `0` for a local webcam or
an `rtsp://…` URL.

```bash
curl -F "file=@car.jpg" http://localhost:8000/predict
```

**The four possible decisions** — only `answer` carries the 99%-precision guarantee:

| `decision` | Meaning |
|---|---|
| `answer` | one of the five known models, above the confidence threshold |
| `reject` | a vehicle outside the catalogue |
| `abstain` | not confident enough — send to a human |
| `no_car` | the detector found no vehicle |

---

## Troubleshooting

Every entry below is a failure that actually happened, with the exact fix.

**`artifacts/config.json not found`**
The weights are missing. They are committed, so `git pull` should restore them. If you
retrained, rebuild them: `python scripts/pack_weights.py --src /path/to/modelgate_v2_artifacts`

**`[Errno 98] Address already in use` / `error while attempting to bind on address`**
Port 8000 is taken. Use another: `--port 8010`. The URLs above change to match.

**`{"detail":"Not Found"}` on a page that should exist**
Your clone is behind. `git pull`, then **restart the server** — Python loads modules once
at startup, so pulling while it runs changes nothing.

**`ModuleNotFoundError: No module named 'config'` (or `pipeline`, `video`)**
You are in the wrong directory. `uvicorn app:app` must run from inside `service/`.

**`ImportError: Form data requires "python-multipart"`**
`pip install python-multipart` — it is in `requirements.txt`, so this means the install
was incomplete. Re-run `python scripts/setup.py`.

**`torch.cuda.is_available()` is False but you have an NVIDIA GPU**
CPU-only torch got installed. Reinstall with the CUDA index URL:
`pip install --force-reinstall torch torchvision --index-url https://download.pytorch.org/whl/cu128`
Match the CUDA version to your driver — `nvidia-smi` shows it, and https://pytorch.org
lists the right index URL.

**`docker compose up` fails with a pipe/connection error**
Docker Desktop is not running, or you are in the wrong folder. It must run from `service/`,
not `service/artifacts/`.

**PowerShell refuses to run `activate`**
Skip activation. Call the interpreter directly: `.\.venv\Scripts\python.exe -m uvicorn ...`

**Video plays but nothing is identified**
Not a bug — read the **Signal quality** panel on the console. The classifier needs cars
~438 px across; a distant camera gives far less, and upscaling cannot recover detail that
was never captured. Move the camera closer, zoom in, or raise the source resolution.
`python scripts/resolution_sweep.py` measures the exact floor on your own test set.

**`CUDA out of memory`**
Lower the batch cap: set `MAX_BATCH=4` in the environment before starting.

---

## Optional — retrain from scratch

The dataset images are **not** in this repository (public listing photos, not
redistributed). The method to rebuild them is fully documented. Run the notebooks in this
order on a free Colab T4:

1. `notebooks/data_gate.ipynb` — clean, crop, leakage-safe split
2. `notebooks/model_gate_v2.ipynb` — train the 6-class model
3. `notebooks/trust_layer.ipynb` — calibrate and pick the operating point
4. `notebooks/export_onnx.ipynb` — *optional*, for the in-browser demo

Then bring the result back:

```bash
python scripts/pack_weights.py --src /path/to/modelgate_v2_artifacts
```

See [`QUICKSTART.md`](QUICKSTART.md) and [`ROADMAP.md`](ROADMAP.md) for the full method.

---

## Temporary decisions

Things done for convenience that should be undone before this is treated as a long-lived
project:

| What | Why it is like this | How to undo |
|---|---|---|
| **`service/artifacts/model.pt` is committed** (~56 MB, fp16) | So the project runs from a clone with no extra download — for quick testing and demos. Weights are binaries and normally belong out of git; they bloat history permanently, and every future version adds another 56 MB that can never be removed without rewriting history. | Re-add `service/artifacts/` to `.gitignore`, `git rm --cached service/artifacts/model.pt`, and publish weights as a **GitHub Release asset** (2 GB limit, no LFS quota) with a small fetch step in setup. |
| **Weights stored in fp16 rather than fp32** | The fp32 checkpoint is ~114 MB and GitHub rejects any file over 100 MB outright. fp16 is 56 MB and fits. | Not urgent — measured cost is nil (identical top-1, max logit difference 0.0008), and the service already computes in fp16 on GPU. Moving to Releases removes the constraint entirely. |
| **`main` and `claude/aiml-capstone-planning-fnkas1` hold identical content** | The feature branch is still the repository default. | Set `main` as the default branch in Settings → General, then delete the feature branch. |
