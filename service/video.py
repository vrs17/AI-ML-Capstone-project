"""Real-time video pipeline: detect+track every car, classify each track, vote over time.

Why tracking changes the problem
--------------------------------
A photo gives one look at a car. A video gives many. We attach a persistent track id to
each car (ByteTrack), classify that track on several frames, and **average the calibrated
probabilities** across those looks before applying the trust-layer threshold. Averaging
cuts the variance of a single noisy frame, so a track decision is markedly more reliable
than any one frame — and it is also cheaper, because once a track has settled we stop
classifying it.

The business output is a count of *unique cars* (one per track), not per-frame hits.
"""
from __future__ import annotations

import time, threading, logging
from dataclasses import dataclass, field

import cv2
import numpy as np
from PIL import Image

from config import settings, PRETTY
from pipeline import recognizer

log = logging.getLogger("video")

# colours are BGR for OpenCV
COLOR = {"answer": (150, 240, 105), "reject": (107, 200, 255),
         "abstain": (255, 124, 154), "pending": (200, 200, 200)}


@dataclass
class Track:
    """Accumulated evidence for one physical car.

    Aggregation note: we do NOT average raw probabilities. The abstain threshold was
    calibrated on *single-frame* confidences, and an arithmetic mean lets one bad frame
    (a motion-blurred or half-occluded look) drag an otherwise certain track below it.
    Instead every look is judged with the calibrated threshold — exactly the decision the
    trust layer was measured for — and the track takes the **majority vote of the looks
    that passed**. A weak frame simply does not vote, rather than poisoning the average.
    """
    prob_sum: np.ndarray                                   # kept for display only
    votes: int = 0                                         # total classifications
    label_votes: dict = field(default_factory=dict)        # label -> confident looks
    conf_sum: dict = field(default_factory=dict)           # label -> summed confidence
    last_seen: int = 0
    last_classified: int = -999
    decision: str = "pending"
    label: str | None = None
    confidence: float = 0.0
    counted: bool = False

    @property
    def mean_probs(self) -> np.ndarray:
        return self.prob_sum / max(self.votes, 1)


class VideoProcessor:
    """Runs the full pipeline over a video source and yields annotated JPEG frames."""

    def __init__(self, source, frame_stride: int | None = None,
                 classify_every: int = 5, max_votes: int = 7, max_width: int = 1280):
        self.source = source
        self.classify_every = classify_every      # frames between re-classifying a track
        self.max_votes = max_votes                # stop classifying a track after this many
        self.max_width = max_width
        gpu = recognizer.device is not None and recognizer.device.type == "cuda"
        # CPU cannot keep up frame-for-frame at 384px; skipping frames is the honest fix.
        self.frame_stride = frame_stride if frame_stride else (1 if gpu else 3)
        self.tracks: dict[int, Track] = {}
        self.frame_idx = 0
        self.processed = 0
        self.t_start = time.time()
        self.fps = 0.0
        self.done = False
        self._lock = threading.Lock()

    # ---------- counting ----------
    def summary(self) -> dict:
        counts, unknown, unsure, pending = {}, 0, 0, 0
        for t in self.tracks.values():
            if t.decision == "answer":
                counts[t.label] = counts.get(t.label, 0) + 1
            elif t.decision == "reject":
                unknown += 1
            elif t.decision == "abstain":
                unsure += 1
            else:
                pending += 1
        return {
            "unique_vehicles": len(self.tracks),
            "identified": counts,
            "unknown_vehicles": unknown,
            "not_confident": unsure,
            "still_deciding": pending,
            "frames_read": self.frame_idx,
            "frames_processed": self.processed,
            "fps": round(self.fps, 1),
            "frame_stride": self.frame_stride,
            "done": self.done,
        }

    # ---------- per-frame work ----------
    def _classify_tracks(self, frame_rgb, boxes: dict[int, tuple]):
        """Classify the tracks that are due, in ONE batch."""
        due = []
        for tid, box in boxes.items():
            t = self.tracks.get(tid)
            if t is None:
                s = settings
                self.tracks[tid] = t = Track(prob_sum=np.zeros(len(s.classes), dtype=np.float64))
            t.last_seen = self.frame_idx
            if t.votes >= self.max_votes:
                continue                                    # settled — stop spending compute
            if self.processed - t.last_classified < self.classify_every:
                continue
            due.append((tid, box))

        if not due:
            return
        crops = []
        for tid, (x1, y1, x2, y2) in due:
            crop = frame_rgb[y1:y2, x1:x2]
            if crop.size == 0:
                continue
            crops.append((tid, Image.fromarray(crop)))
        if not crops:
            return

        s = settings
        for i in range(0, len(crops), s.max_batch):          # respect the VRAM cap
            chunk = crops[i:i + s.max_batch]
            results = recognizer.classify([c for _, c in chunk])
            for (tid, _), (_lab, _conf, probs) in zip(chunk, results):
                t = self.tracks[tid]
                pv = np.array([probs[c] for c in s.classes], dtype=np.float64)
                t.prob_sum += pv
                t.votes += 1
                t.last_classified = self.processed
                self._cast_vote(t, pv)
                self._decide(t)

    def _cast_vote(self, t: Track, probs: np.ndarray):
        """Judge ONE look with the calibrated threshold, then let it vote."""
        s = settings
        i = int(probs.argmax())
        label, conf = s.classes[i], float(probs[i])
        thr = s.abstain_threshold
        if thr is not None and conf < thr:
            return                                          # this look is not confident enough
        t.label_votes[label] = t.label_votes.get(label, 0) + 1
        t.conf_sum[label] = t.conf_sum.get(label, 0.0) + conf

    def _decide(self, t: Track):
        """Track decision = majority of the looks that cleared the threshold."""
        s = settings
        if not t.label_votes:
            t.decision = "abstain"
            i = int(t.mean_probs.argmax())
            t.label, t.confidence = s.classes[i], float(t.mean_probs[i])
            return
        label = max(t.label_votes, key=lambda k: t.label_votes[k])
        t.label = label
        t.confidence = t.conf_sum[label] / t.label_votes[label]
        t.decision = "reject" if label == s.reject_class else "answer"

    def _draw(self, frame, boxes: dict[int, tuple]):
        for tid, (x1, y1, x2, y2) in boxes.items():
            t = self.tracks.get(tid)
            dec = t.decision if t else "pending"
            col = COLOR.get(dec, COLOR["pending"])
            cv2.rectangle(frame, (x1, y1), (x2, y2), col, 2)
            if t and t.votes:
                name = PRETTY.get(t.label, t.label) if dec != "abstain" else "Not sure"
                if dec == "reject":
                    name = "Unknown vehicle"
                txt = f"#{tid} {name} {t.confidence:.0%} ({t.votes})"
            else:
                txt = f"#{tid} …"
            (tw, th), _ = cv2.getTextSize(txt, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 2)
            cv2.rectangle(frame, (x1, max(0, y1 - th - 9)), (x1 + tw + 8, y1), col, -1)
            cv2.putText(frame, txt, (x1 + 4, max(12, y1 - 5)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (12, 18, 30), 2)

        s = self.summary()
        hud = (f"cars {s['unique_vehicles']}  |  " +
               "  ".join(f"{k} {v}" for k, v in s["identified"].items()) +
               (f"  |  unknown {s['unknown_vehicles']}" if s["unknown_vehicles"] else "") +
               f"  |  {s['fps']:.1f} fps")
        cv2.rectangle(frame, (0, 0), (frame.shape[1], 34), (16, 24, 44), -1)
        cv2.putText(frame, hud, (12, 23), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (245, 248, 255), 1)
        return frame

    # ---------- main loop ----------
    def frames(self):
        cap = cv2.VideoCapture(self.source)
        if not cap.isOpened():
            raise RuntimeError(f"cannot open video source: {self.source}")
        s = settings
        try:
            while True:
                ok, frame = cap.read()
                if not ok:
                    break
                self.frame_idx += 1
                if self.frame_idx % self.frame_stride:
                    continue

                if frame.shape[1] > self.max_width:
                    sc = self.max_width / frame.shape[1]
                    frame = cv2.resize(frame, (self.max_width, int(frame.shape[0] * sc)))

                boxes: dict[int, tuple] = {}
                if recognizer.detector is not None:
                    r = recognizer.detector.track(
                        frame, persist=True, classes=list(s.vehicle_classes),
                        conf=s.det_conf, imgsz=s.det_imgsz, tracker="bytetrack.yaml",
                        verbose=False)[0]
                    if r.boxes is not None and r.boxes.id is not None:
                        H, W = frame.shape[:2]
                        for xyxy, tid in zip(r.boxes.xyxy.cpu().numpy(),
                                             r.boxes.id.cpu().numpy().astype(int)):
                            x1, y1, x2, y2 = xyxy
                            if (x2 - x1) * (y2 - y1) / float(W * H) < s.det_area_floor:
                                continue                     # too small to identify reliably
                            pw, ph = (x2 - x1) * s.det_pad, (y2 - y1) * s.det_pad
                            boxes[int(tid)] = (max(0, int(x1 - pw)), max(0, int(y1 - ph)),
                                               min(W, int(x2 + pw)), min(H, int(y2 + ph)))

                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                with self._lock:
                    self._classify_tracks(rgb, boxes)
                self.processed += 1
                self.fps = self.processed / max(time.time() - self.t_start, 1e-6)

                frame = self._draw(frame, boxes)
                ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
                if ok:
                    yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"
                           + buf.tobytes() + b"\r\n")
        finally:
            cap.release()
            self.done = True
            log.info("video finished: %s", self.summary())


jobs: dict[str, VideoProcessor] = {}
