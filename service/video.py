"""Real-time video pipeline: detect+track every car, classify each track, vote over time.

Why tracking changes the problem
--------------------------------
A photo gives one look at a car. A video gives many. We attach a persistent track id to
each car (ByteTrack), classify that track on several frames, and take a **vote over the
looks that cleared the calibrated threshold**. That is strictly more evidence than any
single frame — and it is cheaper too, because once a track has settled we stop
classifying it.

The business output is a count of *unique cars* (one per track), not per-frame hits, plus
an ordered **event log** — the artefact an operator actually reads.
"""
from __future__ import annotations

import os, time, threading, logging
from dataclasses import dataclass, field

import cv2
import numpy as np
from PIL import Image

import overlay as ov
from config import settings, PRETTY
from pipeline import recognizer

log = logging.getLogger("video")

# Operator-facing wording. The console reads these verbatim — one place to change them.
DECISION_TEXT = {
    "answer":  "identified",
    "reject":  "not in catalogue",
    "abstain": "sent to staff",
    "pending": "analysing",
}


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
    label_votes: dict = field(default_factory=dict)        # label -> summed vote weight
    conf_sum: dict = field(default_factory=dict)           # label -> weighted confidence
    first_frame: int = 0
    last_seen: int = 0
    crop_px: int = 0                                       # short side of the last crop
    best_px: int = 0                                       # largest crop ever classified
    seen_px: int = 0                                       # largest crop ever *offered*
    weight: float = 0.0                                    # summed vote quality
    last_classified: int = -999
    decision: str = "pending"
    label: str | None = None
    confidence: float = 0.0
    decided_frame: int | None = None                       # drives the lock-on animation
    too_far: bool = False                                  # abstained for distance, not doubt
    trail: list = field(default_factory=list)              # ground track, for the overlay

    @property
    def mean_probs(self) -> np.ndarray:
        return self.prob_sum / max(self.votes, 1)


class VideoProcessor:
    """Runs the full pipeline over a video source and yields annotated JPEG frames."""

    TRAIL_LEN = 26
    GOOD_CROP_PX = 438            # the classifier's Resize() target — below this we upscale

    def __init__(self, source, frame_stride: int | None = None, classify_every: int = 5,
                 max_votes: int = 7, max_width: int = 1280, label: str = "camera"):
        self.source = source
        self.label = label
        self.classify_every = classify_every      # frames between re-classifying a track
        self.max_votes = max_votes                # stop classifying a track after this many
        self.max_width = max_width
        gpu = recognizer.device is not None and recognizer.device.type == "cuda"
        # CPU cannot keep up frame-for-frame at 384px; skipping frames is the honest fix.
        self.frame_stride = frame_stride if frame_stride else (1 if gpu else 3)
        # A camera sees cars far smaller than a listing photo does: finer detector grid,
        # lower area floor. Both are env-overridable.
        self.det_imgsz = int(os.getenv("VIDEO_DET_IMGSZ", "960"))
        self.area_floor = float(os.getenv("VIDEO_AREA_FLOOR", "0.010"))
        # smallest crop allowed to *name* a car (it may still be tracked and counted)
        self.min_vote_px = int(os.getenv("MIN_VOTE_PX", "200"))
        self.src_w = self.src_h = 0
        self.tracks: dict[int, Track] = {}
        self.events: list[dict] = []              # ordered operator-facing log
        self.frame_idx = 0
        self.processed = 0
        self.total_frames = 0
        self.t_start = time.time()
        self.started_at = time.time()
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
        settled = sum(counts.values()) + unknown + unsure
        elapsed = max(time.time() - self.started_at, 1e-6)
        return {
            "unique_vehicles": len(self.tracks),
            "identified": counts,
            "identified_total": sum(counts.values()),
            "unknown_vehicles": unknown,
            "not_confident": unsure,
            "still_deciding": pending,
            # share of settled vehicles the system handled without a human
            "auto_handled": round((sum(counts.values()) + unknown) / settled, 4) if settled else None,
            "throughput_per_hour": round(len(self.tracks) / elapsed * 3600, 1),
            "elapsed_s": round(elapsed, 1),
            "signal": self._signal(),
            "frames_read": self.frame_idx,
            "frames_processed": self.processed,
            "total_frames": self.total_frames or None,
            "progress": (round(min(self.frame_idx / self.total_frames, 1.0), 4)
                         if self.total_frames else None),
            "fps": round(self.fps, 1),
            "frame_stride": self.frame_stride,
            "source": self.label,
            "done": self.done,
        }

    def _signal(self) -> dict:
        """How much detail the classifier is actually getting.

        The single most common reason a car recognised at 99% in a photo is only
        "analysing" on camera is that it arrives far smaller. The model is fed
        Resize(438) -> CenterCrop(384); a crop narrower than that is UPSCALED, so the
        detail the fine-grained head needs (grille, lights, badge) was never captured.
        Reporting it beats letting the operator guess.
        """
        px = sorted(t.best_px for t in self.tracks.values() if t.best_px)
        base = {"good_crop_px": self.GOOD_CROP_PX, "min_vote_px": self.min_vote_px,
                "source": f"{self.src_w}x{self.src_h}" if self.src_w else None,
                # the biggest crop this camera could ever produce: a car filling the frame
                "ceiling_px": min(self.src_w, self.src_h) if self.src_w else None}
        if not px:
            return {**base, "median_crop_px": None, "best_crop_px": None,
                    "quality": None, "advice": None, "limit": None}
        med = px[len(px) // 2]
        best = px[-1]
        # Distinguish the two causes. If even the closest car this session was small, the
        # camera itself is the ceiling; if some cars were fine, it is a placement problem.
        limit = ("camera" if best < self.GOOD_CROP_PX and
                 (base["ceiling_px"] or 0) < self.GOOD_CROP_PX * 2 else
                 "distance" if best >= self.GOOD_CROP_PX else "placement")
        if med >= self.GOOD_CROP_PX:
            q, advice = "good", "Full detail is reaching the classifier."
        elif med >= self.min_vote_px:
            q, advice = ("fair", f"Upscaled {self.GOOD_CROP_PX / med:.1f}x, so expect more "
                                 "abstentions than the sealed-test figures. Cars are judged on "
                                 "their closest look, so this mostly costs the ones that never "
                                 "approach.")
        elif limit == "camera":
            q, advice = ("poor", f"This source is only {base['source']}, so even a car filling "
                                 f"the frame gives {base['ceiling_px']}px. No placement change "
                                 "fixes that — it needs a higher-resolution feed.")
        elif best >= self.min_vote_px:
            q, advice = ("fair", f"Most cars are small, but the closest looks reach {best}px — "
                                 "those are the ones being judged. Cars that never approach are "
                                 "abstained rather than guessed.")
        else:
            q, advice = ("poor", f"Nothing this session got past {self.min_vote_px}px, the floor "
                                 "for naming a car. Upscaling cannot recover grille or badge "
                                 "detail the sensor never sampled: move the camera closer, zoom "
                                 "in, or raise the source resolution.")
        return {**base, "median_crop_px": med, "best_crop_px": best,
                "quality": q, "advice": advice, "limit": limit}

    def recent_events(self, after: int = 0, limit: int = 60) -> dict:
        """Events with a monotonic sequence number, so the console can poll for the tail."""
        tail = [e for e in self.events if e["seq"] > after][-limit:]
        return {"events": tail, "seq": self.events[-1]["seq"] if self.events else 0}

    def _emit(self, tid: int, t: Track):
        self.events.append({
            "seq": len(self.events) + 1,
            "t": round(time.time() - self.started_at, 2),
            "wall": time.strftime("%H:%M:%S"),
            "track": tid,
            "decision": t.decision,
            "state": DECISION_TEXT.get(t.decision, t.decision),
            "label": t.label,
            "display": self._display(t),
            "confidence": round(t.confidence, 4),
            "looks": t.votes,
            "crop_px": t.best_px or t.crop_px,
            "too_far": t.too_far,
            "guess": PRETTY.get(t.label, t.label) if t.decision == "abstain" else None,
        })
        if len(self.events) > 800:                 # a long shift should not grow unbounded
            del self.events[:200]

    @staticmethod
    def _display(t: Track) -> str:
        if t.decision == "answer":
            return PRETTY.get(t.label, t.label or "—")
        if t.decision == "reject":
            return "Vehicle outside catalogue"
        if t.decision == "abstain":
            return "Too far to identify" if t.too_far else "Needs a check"
        return "Analysing…"

    # ---------- per-frame work ----------
    def _classify_tracks(self, frame_rgb, boxes: dict[int, tuple]):
        """Classify the tracks that are due, in ONE batch."""
        due = []
        for tid, box in boxes.items():
            t = self.tracks.get(tid)
            if t is None:
                self.tracks[tid] = t = Track(
                    prob_sum=np.zeros(len(settings.classes), dtype=np.float64),
                    first_frame=self.frame_idx)
            t.last_seen = self.frame_idx
            px = min(box[3] - box[1], box[2] - box[0])
            t.seen_px = max(t.seen_px, px)
            # Normally we stop once a track has settled. But if the car has come
            # markedly closer than any look we have judged, that new look is better
            # evidence than everything before it — spend one more classification.
            closer = px > t.best_px * 1.35 and px >= self.min_vote_px
            if t.votes >= self.max_votes and not (closer and t.votes < self.max_votes * 2):
                continue                                    # settled — stop spending compute
            if self.processed - t.last_classified < self.classify_every and not closer:
                continue
            due.append((tid, box))

        if not due:
            return
        crops = []
        for tid, (x1, y1, x2, y2) in due:
            crop = frame_rgb[y1:y2, x1:x2]
            if crop.size == 0:
                continue
            self.tracks[tid].crop_px = min(crop.shape[0], crop.shape[1])
            self.tracks[tid].best_px = max(self.tracks[tid].best_px, self.tracks[tid].crop_px)
            if self.tracks[tid].crop_px < 48:
                continue          # fewer pixels than the model's first conv stride can use
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
                self._decide(tid, t)

    def _cast_vote(self, t: Track, probs: np.ndarray):
        """Judge ONE look with the calibrated threshold, then let it vote — by size.

        A car crossing a forecourt is sampled many times, and the crop grows as it
        approaches. Those looks are not equal evidence: a 150px crop is upscaled 3x into
        the network, so its confidence is asserted about detail the sensor never captured
        — and a fine-grained model can be confidently wrong on it. Two guards:

          * a look below MIN_VOTE_PX does not vote at all, and
          * the rest are weighted by crop size, so the close look decides the track.

        The far looks still show a box on screen; they just do not get to name the car.
        """
        s = settings
        if t.crop_px < self.min_vote_px:
            return                                          # too little signal to assert anything
        i = int(probs.argmax())
        label, conf = s.classes[i], float(probs[i])
        thr = s.abstain_threshold
        if thr is not None and conf < thr:
            return                                          # this look is not confident enough
        w = min(1.0, t.crop_px / self.GOOD_CROP_PX)         # full weight only at full detail
        t.weight += w
        t.label_votes[label] = t.label_votes.get(label, 0.0) + w
        t.conf_sum[label] = t.conf_sum.get(label, 0.0) + conf * w

    def _decide(self, tid: int, t: Track):
        """Track decision = majority of the looks that cleared the threshold."""
        s = settings
        before = (t.decision, t.label)
        if not t.label_votes:
            t.decision = "abstain"
            i = int(t.mean_probs.argmax())
            t.label, t.confidence = s.classes[i], float(t.mean_probs[i])
            t.too_far = t.seen_px < self.min_vote_px        # never came close enough to judge
        else:
            t.too_far = False                               # a close look arrived after all
            label = max(t.label_votes, key=lambda k: t.label_votes[k])
            t.label = label
            t.confidence = t.conf_sum[label] / t.label_votes[label]
            t.decision = "reject" if label == s.reject_class else "answer"
        if (t.decision, t.label) != before:
            t.decided_frame = self.frame_idx        # (re)play the lock-on animation
            self._emit(tid, t)

    # ---------- drawing ----------
    def _draw(self, frame, boxes: dict[int, tuple], scale: float):
        for tid, box in boxes.items():
            t = self.tracks.get(tid)
            if t is None:
                continue
            t.trail.append(((box[0] + box[2]) // 2, box[3]))       # ground point, display coords
            if len(t.trail) > self.TRAIL_LEN:
                del t.trail[0]
            rgb = ov.PALETTE.get(t.decision, ov.PALETTE["pending"])
            col = ov.bgr(rgb)

            if t.decision == "pending":
                # still gathering looks — sweep the box and breathe the brackets
                phase = ((self.frame_idx - t.first_frame) % 26) / 26.0
                ov.draw_scan(frame, box, col, phase)
                ov.draw_brackets(frame, box, col, thickness=2,
                                 grow=int(2 * abs(0.5 - phase) * 4))
                patch = ov.chip(f"Analysing{'.' * (1 + (self.frame_idx // 6) % 3)}",
                                f"track #{tid} · {t.votes} look{'' if t.votes == 1 else 's'}",
                                rgb, scale)
                alpha = 0.9
            else:
                k = ((self.frame_idx - t.decided_frame) / ov.LOCK_FRAMES
                     if t.decided_frame is not None else 1.0)
                e = ov._ease_out(k)
                ov.draw_trail(frame, t.trail, col)
                ov.flash(frame, box, col, 0.22 * max(0.0, 1.0 - k * 2.2))
                ov.draw_brackets(frame, box, col, thickness=2,
                                 grow=int(min(box[2] - box[0], box[3] - box[1]) * 0.16 * (1 - e)))
                if t.decision == "abstain" and t.too_far:
                    sub = f"only {t.seen_px}px · needs {self.min_vote_px}px to name"
                elif t.decision == "abstain":
                    thr = settings.abstain_threshold
                    sub = (f"likely {PRETTY.get(t.label, t.label)} {t.confidence:.0%}"
                           + (f" · below {thr:.0%}" if thr else ""))
                else:
                    sub = f"track #{tid} · {t.confidence:.0%} · {t.votes} looks"
                patch = ov.chip(self._display(t), sub, rgb, scale,
                                conf=t.confidence if t.decision != "abstain" else None)
                alpha = min(1.0, max(k, 0.0) * 2.0)

            # keep the chip fully on screen: a car at the frame edge must still be readable
            ch, cw = patch.shape[:2]
            cx = min(max(box[0], 2), max(2, frame.shape[1] - cw - 2))
            cy = box[1] - ch - int(8 * scale)
            if cy < 2:                                     # no room above — sit inside the box
                cy = box[1] + int(6 * scale)
            ov.blit_rgba(frame, patch, cx, cy, alpha)
        return frame

    # ---------- main loop ----------
    def frames(self, annotate: bool = True):
        cap = cv2.VideoCapture(self.source)
        if not cap.isOpened():
            raise RuntimeError(f"cannot open video source: {self.source}")
        self.total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        self.src_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        self.src_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        self.t_start = self.started_at = time.time()
        s = settings
        try:
            while True:
                ok, frame = cap.read()
                if not ok:
                    break
                self.frame_idx += 1
                if self.frame_idx % self.frame_stride:
                    continue

                # Detect and CROP at the source resolution. Downscaling first (as this
                # used to) threw away a third of the linear resolution on 1080p before the
                # crop was taken — and the classifier then upscaled that crop back to 384,
                # inventing pixels. Only the frame we *display* gets shrunk.
                H, W = frame.shape[:2]
                boxes: dict[int, tuple] = {}
                if recognizer.detector is not None:
                    with recognizer._det_lock:
                        r = recognizer.detector.track(
                            frame, persist=True, classes=list(s.vehicle_classes),
                            conf=s.det_conf, imgsz=self.det_imgsz, tracker="bytetrack.yaml",
                            verbose=False)[0]
                    if r.boxes is not None and r.boxes.id is not None:
                        for xyxy, tid in zip(r.boxes.xyxy.cpu().numpy(),
                                             r.boxes.id.cpu().numpy().astype(int)):
                            x1, y1, x2, y2 = xyxy
                            if (x2 - x1) * (y2 - y1) / float(W * H) < self.area_floor:
                                continue                     # too small to identify reliably
                            pw, ph = (x2 - x1) * s.det_pad, (y2 - y1) * s.det_pad
                            boxes[int(tid)] = (max(0, int(x1 - pw)), max(0, int(y1 - ph)),
                                               min(W, int(x2 + pw)), min(H, int(y2 + ph)))

                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                with self._lock:
                    self._classify_tracks(rgb, boxes)        # full-resolution crops
                self.processed += 1
                self.fps = self.processed / max(time.time() - self.t_start, 1e-6)

                # display copy — shrink here, and scale the boxes to match
                if W > self.max_width:
                    ds = self.max_width / W
                    disp = cv2.resize(frame, (self.max_width, int(H * ds)))
                    dboxes = {t: tuple(int(v * ds) for v in b) for t, b in boxes.items()}
                else:
                    disp, dboxes = frame, boxes
                if annotate:
                    disp = self._draw(disp, dboxes, scale=disp.shape[1] / 1280.0)
                ok, buf = cv2.imencode(".jpg", disp, [cv2.IMWRITE_JPEG_QUALITY, 82])
                if ok:
                    yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"
                           + buf.tobytes() + b"\r\n")
        finally:
            cap.release()
            self.done = True
            log.info("video finished: %s", self.summary())


jobs: dict[str, VideoProcessor] = {}
