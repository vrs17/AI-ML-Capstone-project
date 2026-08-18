"""Runtime configuration. Everything overridable by environment variable."""
from __future__ import annotations
import os, json
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default):
    v = os.getenv(name)
    if v is None:
        return default
    if isinstance(default, bool):
        return v.lower() in ("1", "true", "yes", "on")
    if isinstance(default, int):
        return int(v)
    if isinstance(default, float):
        return float(v)
    return v


@dataclass
class Settings:
    # ── artifacts ────────────────────────────────────────────────────────
    artifacts_dir: Path = Path(_env("ARTIFACTS_DIR", "artifacts"))
    detector_weights: str = _env("DETECTOR_WEIGHTS", "yolo11s.pt")

    # ── device / performance (tuned for an 8 GB VRAM card) ───────────────
    device: str = _env("DEVICE", "auto")          # auto | cuda | cpu
    fp16: bool = _env("FP16", True)               # half precision on GPU
    channels_last: bool = _env("CHANNELS_LAST", True)
    max_batch: int = _env("MAX_BATCH", 8)         # micro-batch cap
    batch_wait_ms: int = _env("BATCH_WAIT_MS", 8) # how long to gather a batch
    max_concurrent: int = _env("MAX_CONCURRENT", 16)
    compile_model: bool = _env("COMPILE", False)  # torch.compile (slow first call)

    # ── detector (must mirror the Data Gate) ─────────────────────────────
    vehicle_classes: tuple = (2, 5, 7)            # COCO car / bus / truck
    det_conf: float = _env("DET_CONF", 0.25)
    det_pad: float = _env("DET_PAD", 0.08)        # 8% padding around the box
    det_area_floor: float = _env("DET_AREA_FLOOR", 0.03)
    det_imgsz: int = _env("DET_IMGSZ", 640)

    # ── limits ───────────────────────────────────────────────────────────
    max_upload_mb: int = _env("MAX_UPLOAD_MB", 12)
    max_batch_files: int = _env("MAX_BATCH_FILES", 32)

    # ── filled from artifacts/config.json at load time ───────────────────
    classes: list = field(default_factory=list)
    img_size: int = 384
    resize: int = 438
    mean: tuple = (0.485, 0.456, 0.406)
    std: tuple = (0.229, 0.224, 0.225)
    temperature: float = 1.0
    abstain_threshold: float | None = None
    reject_class: str = "others"
    model_id: str = "convnext_tiny.fb_in22k_ft_in1k"
    head: str = "linear"
    arc: dict = field(default_factory=dict)

    def load_model_config(self) -> "Settings":
        p = self.artifacts_dir / "config.json"
        if not p.exists():
            raise FileNotFoundError(
                f"{p} not found. Copy artifacts/ (model.pt + config.json) from "
                "modelgate_v2_artifacts.zip into the service directory."
            )
        c = json.loads(p.read_text())
        self.classes = c["classes"]
        self.img_size = int(c.get("img_size", 384))
        # MUST match model_gate_v2's transforms.Resize(438)
        self.resize = 438 if self.img_size == 384 else int(round(self.img_size / 0.875))
        self.mean = tuple(c.get("mean", self.mean))
        self.std = tuple(c.get("std", self.std))
        self.model_id = c.get("model_id", self.model_id)
        self.head = c.get("head", "linear")
        self.arc = c.get("arc", {}) or {}
        # trust layer (present once trust_layer.ipynb has run)
        self.temperature = float(c.get("temperature", 1.0))
        thr = c.get("abstain_threshold", None)
        self.abstain_threshold = float(thr) if thr is not None else None
        self.reject_class = c.get("reject_class", "others")
        return self


PRETTY = {
    "cobalt": "Chevrolet Cobalt",
    "damas": "Chevrolet Damas",
    "gentra": "Chevrolet Gentra",
    "nexia3": "Chevrolet Nexia 3",
    "spark": "Chevrolet Spark",
    "others": "Not one of the five",
}

settings = Settings()
