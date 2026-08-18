"""Two-stage inference: YOLO crop -> ConvNeXt+ArcFace classify -> trust layer.

The preprocessing here mirrors notebooks/model_gate_v2.ipynb exactly
(Resize(438) -> CenterCrop(384) -> ImageNet normalize). If it drifts, the
model's measured accuracy no longer applies.
"""
from __future__ import annotations

import io, time, threading, logging
from dataclasses import dataclass

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from PIL import Image, ImageOps
from torchvision import transforms

from config import settings, PRETTY

log = logging.getLogger("pipeline")


# ─────────────────────────── model definitions ────────────────────────────
# Copied verbatim from model_gate_v2 so the checkpoint loads with strict=True.
class SubCenterArcFace(nn.Module):
    def __init__(self, in_features, num_classes, K=3, s=30.0, m=0.30):
        super().__init__()
        self.num_classes, self.K, self.s, self.m = num_classes, K, s, m
        self.W = nn.Parameter(torch.empty(num_classes * K, in_features))
        nn.init.xavier_uniform_(self.W)

    def forward(self, feat, labels=None):
        f = F.normalize(feat, dim=1)
        w = F.normalize(self.W, dim=1)
        cos = (f @ w.t()).view(-1, self.num_classes, self.K).amax(dim=2)
        cos = cos.float().clamp(-1 + 1e-6, 1 - 1e-6)
        if labels is None:
            return self.s * cos
        theta = torch.acos(cos)
        margin = torch.zeros_like(cos).scatter_(1, labels.view(-1, 1), self.m)
        return self.s * torch.cos(theta + margin)


class ArcModel(nn.Module):
    def __init__(self, model_id, num_classes, K=3, s=30.0, m=0.30, pretrained=False):
        super().__init__()
        import timm
        self.backbone = timm.create_model(model_id, pretrained=pretrained,
                                          num_classes=0, global_pool="avg")
        self.head = SubCenterArcFace(self.backbone.num_features, num_classes, K, s, m)

    def forward(self, x, labels=None):
        return self.head(self.backbone(x), labels)


class SnapMixNet(nn.Module):
    def __init__(self, model_id, num_classes, pretrained=False):
        super().__init__()
        import timm
        self.backbone = timm.create_model(model_id, pretrained=pretrained,
                                          num_classes=0, global_pool="")
        self.fc = nn.Linear(self.backbone.num_features, num_classes)

    def forward(self, x):
        f = self.backbone.forward_features(x)
        return self.fc(F.adaptive_avg_pool2d(f, 1).flatten(1))


# ────────────────────────────── results ───────────────────────────────────
@dataclass
class Prediction:
    decision: str                 # answer | reject | abstain | no_car
    label: str | None
    display: str | None
    confidence: float | None
    probabilities: dict
    detection: dict
    timing_ms: dict
    reason: str

    def to_dict(self):
        return {
            "decision": self.decision,
            "model": self.label,
            "display": self.display,
            "confidence": self.confidence,
            "probabilities": self.probabilities,
            "detection": self.detection,
            "timing_ms": self.timing_ms,
            "reason": self.reason,
        }


# ───────────────────────────── the pipeline ───────────────────────────────
class CarRecognizer:
    def __init__(self):
        self.ready = False
        self._lock = threading.Lock()          # serialize GPU access
        self.device = None
        self.detector = None
        self.classifier = None
        self.tf = None
        self.stats = {"requests": 0, "answered": 0, "rejected": 0,
                      "abstained": 0, "no_car": 0, "errors": 0}

    # ---------- load ----------
    def load(self):
        s = settings.load_model_config()
        dev = s.device
        if dev == "auto":
            dev = "cuda" if torch.cuda.is_available() else "cpu"
        self.device = torch.device(dev)
        self.use_fp16 = bool(s.fp16 and self.device.type == "cuda")

        # classifier
        n = len(s.classes)
        if s.head == "arcface":
            model = ArcModel(s.model_id, n, pretrained=False, **s.arc)
        elif s.head == "snapmix":
            model = SnapMixNet(s.model_id, n, pretrained=False)
        else:
            import timm
            model = timm.create_model(s.model_id, pretrained=False, num_classes=n)
        state = torch.load(s.artifacts_dir / "model.pt", map_location="cpu")
        model.load_state_dict(state)            # strict: a mismatch must fail loudly
        model.eval().to(self.device)
        if s.channels_last and self.device.type == "cuda":
            model = model.to(memory_format=torch.channels_last)
        if self.use_fp16:
            model = model.half()
        if s.compile_model:
            model = torch.compile(model)
        self.classifier = model

        # detector (optional — the service still works on pre-cropped photos)
        try:
            from ultralytics import YOLO
            self.detector = YOLO(s.detector_weights)
            log.info("detector loaded: %s", s.detector_weights)
        except Exception as e:
            self.detector = None
            log.warning("detector unavailable (%s) — running classify-only", str(e)[:120])

        self.tf = transforms.Compose([
            transforms.Resize(s.resize),
            transforms.CenterCrop(s.img_size),
            transforms.ToTensor(),
            transforms.Normalize(s.mean, s.std),
        ])

        self._warmup()
        self.ready = True
        log.info("ready | device=%s fp16=%s classes=%s T=%.3f thr=%s",
                 self.device, self.use_fp16, s.classes, s.temperature, s.abstain_threshold)

    def _warmup(self):
        s = settings
        x = torch.zeros(1, 3, s.img_size, s.img_size, device=self.device)
        if s.channels_last and self.device.type == "cuda":
            x = x.to(memory_format=torch.channels_last)
        if self.use_fp16:
            x = x.half()
        with torch.inference_mode():
            for _ in range(2):
                self.classifier(x)
        if self.device.type == "cuda":
            torch.cuda.synchronize()

    # ---------- stage 1: detect + crop ----------
    def detect_and_crop(self, img: Image.Image):
        s = settings
        if self.detector is None:
            return img, {"found": None, "note": "detector disabled; using the whole image"}
        t0 = time.perf_counter()
        r = self.detector.predict(np.array(img), classes=list(s.vehicle_classes),
                                  conf=s.det_conf, imgsz=s.det_imgsz, verbose=False)[0]
        ms = (time.perf_counter() - t0) * 1000
        if r.boxes is None or len(r.boxes) == 0:
            return None, {"found": False, "ms": round(ms, 1)}

        xyxy = r.boxes.xyxy.cpu().numpy()
        conf = r.boxes.conf.cpu().numpy()
        areas = (xyxy[:, 2] - xyxy[:, 0]) * (xyxy[:, 3] - xyxy[:, 1])
        i = int(areas.argmax())                       # largest box = the subject car
        W, H = img.size
        if areas[i] / float(W * H) < s.det_area_floor:
            return None, {"found": False, "reason": "car_too_small", "ms": round(ms, 1)}

        x1, y1, x2, y2 = xyxy[i]
        pw, ph = (x2 - x1) * s.det_pad, (y2 - y1) * s.det_pad
        box = (max(0, int(x1 - pw)), max(0, int(y1 - ph)),
               min(W, int(x2 + pw)), min(H, int(y2 + ph)))
        return img.crop(box), {"found": True, "box": [int(v) for v in box],
                               "score": round(float(conf[i]), 3), "ms": round(ms, 1)}

    # ---------- stage 2 + trust layer ----------
    @torch.inference_mode()
    def classify(self, crops: list[Image.Image]) -> list[tuple[str, float, dict]]:
        s = settings
        x = torch.stack([self.tf(c) for c in crops]).to(self.device, non_blocking=True)
        if s.channels_last and self.device.type == "cuda":
            x = x.to(memory_format=torch.channels_last)
        if self.use_fp16:
            x = x.half()
        with self._lock:
            logits = self.classifier(x).float().cpu()
        probs = torch.softmax(logits / max(s.temperature, 1e-6), dim=1).numpy()
        out = []
        for p in probs:
            i = int(p.argmax())
            out.append((s.classes[i], float(p[i]),
                        {c: round(float(v), 4) for c, v in zip(s.classes, p)}))
        return out

    def _decide(self, label, conf, probs, det, timing) -> Prediction:
        s = settings
        thr = s.abstain_threshold
        if thr is not None and conf < thr:
            return Prediction("abstain", None, None, conf, probs, det, timing,
                              f"Best guess {PRETTY.get(label, label)} at {conf:.1%}, below the "
                              f"{thr:.1%} threshold — routed to a human.")
        if label == s.reject_class:
            return Prediction("reject", label, PRETTY.get(label, label), conf, probs, det, timing,
                              f"Recognised as a vehicle outside the catalogue ({conf:.1%}).")
        return Prediction("answer", label, PRETTY.get(label, label), conf, probs, det, timing,
                          f"{PRETTY.get(label, label)} at {conf:.1%} confidence.")

    # ---------- public ----------
    def predict(self, image_bytes: bytes) -> Prediction:
        t0 = time.perf_counter()
        self.stats["requests"] += 1
        img = Image.open(io.BytesIO(image_bytes))
        img = ImageOps.exif_transpose(img).convert("RGB")   # honour EXIF rotation

        crop, det = self.detect_and_crop(img)
        if crop is None:
            self.stats["no_car"] += 1
            total = (time.perf_counter() - t0) * 1000
            return Prediction("no_car", None, None, None, {}, det,
                              {"detect": det.get("ms", 0), "total": round(total, 1)},
                              "No car found in the photo — nothing to classify.")

        t1 = time.perf_counter()
        label, conf, probs = self.classify([crop])[0]
        cls_ms = (time.perf_counter() - t1) * 1000
        timing = {"detect": det.get("ms", 0), "classify": round(cls_ms, 1),
                  "total": round((time.perf_counter() - t0) * 1000, 1)}

        pred = self._decide(label, conf, probs, det, timing)
        self.stats[{"answer": "answered", "reject": "rejected",
                    "abstain": "abstained"}[pred.decision]] += 1
        return pred

    def predict_batch(self, images: list[bytes]) -> list[Prediction]:
        return [self.predict(b) for b in images]

    def health(self) -> dict:
        s = settings
        h = {"ready": self.ready, "device": str(self.device), "fp16": getattr(self, "use_fp16", False),
             "detector": self.detector is not None, "classes": s.classes,
             "temperature": s.temperature, "abstain_threshold": s.abstain_threshold,
             "model_id": s.model_id, "head": s.head, "img_size": s.img_size}
        if self.device is not None and self.device.type == "cuda":
            free, total = torch.cuda.mem_get_info()
            h["vram"] = {"total_gb": round(total / 1e9, 2),
                         "free_gb": round(free / 1e9, 2),
                         "used_by_us_gb": round(torch.cuda.memory_allocated() / 1e9, 2)}
            h["gpu"] = torch.cuda.get_device_name(0)
        return h


recognizer = CarRecognizer()
