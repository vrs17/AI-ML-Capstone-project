#!/usr/bin/env python3
"""One-command setup for the recognizer service. Cross-platform, idempotent.

    python scripts/setup.py            # create the venv and install everything
    python scripts/setup.py --check    # verify an existing setup, install nothing
    python scripts/setup.py --cpu      # force CPU torch even if a GPU is present

Every step prints what it is doing and fails loudly with the exact fix rather than
half-succeeding. Re-running is safe: existing venvs and satisfied installs are reused.
"""
from __future__ import annotations

import argparse, os, shutil, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVICE = ROOT / "service"
VENV = SERVICE / ".venv"
MIN_PY, MAX_PY = (3, 10), (3, 13)          # 3.13 still has patchy wheels for this stack

OK, BAD, INFO = "  ok   ", "  FAIL ", "  ..   "


def say(tag, msg):
    print(f"{tag}{msg}", flush=True)


def die(msg, fix=""):
    say(BAD, msg)
    if fix:
        print(f"\n       fix: {fix}\n")
    sys.exit(1)


def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def run(cmd, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=kw.pop("cwd", ROOT), text=True,
                          capture_output=kw.pop("capture", False), **kw)


def has_nvidia_gpu() -> bool:
    if not shutil.which("nvidia-smi"):
        return False
    try:
        return run(["nvidia-smi"], capture=True, timeout=20).returncode == 0
    except Exception:
        return False


def step_python():
    v = sys.version_info
    if not (MIN_PY <= (v.major, v.minor) <= MAX_PY):
        die(f"Python {v.major}.{v.minor} is outside the supported "
            f"{MIN_PY[0]}.{MIN_PY[1]}–{MAX_PY[0]}.{MAX_PY[1]} range",
            "install a supported Python and re-run with it, e.g. python3.12 scripts/setup.py")
    say(OK, f"Python {v.major}.{v.minor}.{v.micro}")


def step_venv(check: bool):
    py = venv_python()
    if py.exists():
        say(OK, f"virtualenv {VENV.relative_to(ROOT)}")
        return py
    if check:
        die(f"no virtualenv at {VENV.relative_to(ROOT)}", "python scripts/setup.py")
    say(INFO, f"creating virtualenv at {VENV.relative_to(ROOT)}")
    if run([sys.executable, "-m", "venv", str(VENV)]).returncode != 0:
        die("could not create the virtualenv",
            "on Debian/Ubuntu: sudo apt install python3-venv")
    say(OK, "virtualenv created")
    return py


def step_torch(py: Path, cpu_only: bool, check: bool):
    probe = run([str(py), "-c",
                 "import torch;print(torch.__version__, torch.cuda.is_available())"],
                capture=True)
    if probe.returncode == 0:
        ver, cuda = probe.stdout.split()
        say(OK, f"torch {ver} · CUDA {'available' if cuda == 'True' else 'not available (CPU)'}")
        if cuda == "False" and not cpu_only and has_nvidia_gpu():
            say(INFO, "an NVIDIA GPU is present but torch is CPU-only — reinstall with:")
            print(f"       {py} -m pip install --force-reinstall torch torchvision "
                  f"--index-url https://download.pytorch.org/whl/cu128")
        return
    if check:
        die("torch is not installed", "python scripts/setup.py")

    gpu = (not cpu_only) and has_nvidia_gpu()
    say(INFO, f"installing torch ({'CUDA 12.8' if gpu else 'CPU'}) — this takes a few minutes")
    cmd = [str(py), "-m", "pip", "install", "-q", "torch", "torchvision"]
    if gpu:
        cmd += ["--index-url", "https://download.pytorch.org/whl/cu128"]
    if run(cmd).returncode != 0:
        die("torch install failed",
            "pick the wheel for your CUDA version at https://pytorch.org and install it manually")
    say(OK, "torch installed")


def step_requirements(py: Path, check: bool):
    req = SERVICE / "requirements.txt"
    probe = run([str(py), "-c", "import fastapi, uvicorn, timm, ultralytics, multipart, PIL, numpy"],
                capture=True)
    if probe.returncode == 0:
        say(OK, "service dependencies")
        return
    if check:
        missing = (probe.stderr or "").strip().splitlines()[-1:] or ["unknown"]
        die(f"service dependencies incomplete — {missing[0]}", "python scripts/setup.py")
    say(INFO, "installing service dependencies")
    if run([str(py), "-m", "pip", "install", "-q", "-r", str(req)]).returncode != 0:
        die("dependency install failed", f"{py} -m pip install -r {req}")
    say(OK, "service dependencies installed")


def step_weights():
    model, cfg = SERVICE / "artifacts" / "model.pt", SERVICE / "artifacts" / "config.json"
    if model.exists() and cfg.exists():
        import json
        c = json.loads(cfg.read_text())
        thr = c.get("abstain_threshold")
        say(OK, f"weights {model.stat().st_size/1e6:.0f} MB · {len(c.get('classes', []))} classes"
                f" · threshold {thr if thr is None else f'{thr:.3f}'}")
        return True
    say(BAD, "model weights are missing from service/artifacts/")
    print("""
       The service cannot start without them. Either:
         a) pull again — they are committed to this repository, so a fresh
            clone should already have them; or
         b) rebuild from your own training run:
              python scripts/pack_weights.py --src /path/to/modelgate_v2_artifacts
    """)
    return False


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="verify only, install nothing")
    ap.add_argument("--cpu", action="store_true", help="force CPU torch")
    ap.add_argument("--port", default="8000")
    a = ap.parse_args()

    print(f"\n{'Checking' if a.check else 'Setting up'} the recognizer service — {ROOT}\n")
    step_python()
    py = step_venv(a.check)
    step_torch(py, a.cpu, a.check)
    step_requirements(py, a.check)
    ready = step_weights()

    print("\nStart it with:\n")
    rel = os.path.relpath(py, ROOT)
    print(f"    cd {SERVICE.relative_to(ROOT)}")
    print(f"    {os.path.join('..', rel) if not os.path.isabs(rel) else rel} "
          f"-m uvicorn app:app --port {a.port}\n")
    print(f"Then open http://localhost:{a.port}\n")
    if not ready:
        sys.exit(2)


if __name__ == "__main__":
    main()
