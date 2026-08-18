"""Cinematic detection overlay — the graphics that get burned into the video frame.

Why server-side and not a CSS/canvas layer
------------------------------------------
The obvious way to animate a bounding box in a web page is to stream clean frames
and draw the box in a `<canvas>` on top. That looks great on a still and falls apart
on moving cars: the geometry arrives over a separate HTTP poll, so the box trails the
vehicle by however long the round-trip took. Drawing here, inside the frame the box was
computed from, makes the graphics **pixel-locked by construction** — they cannot drift.

The chrome that does *not* need to track a car (KPI tiles, the event feed, the console
frame) stays in HTML, where it belongs.

Visual language
---------------
    scanning   cyan    corner brackets + a sweeping scan band, while the track gathers looks
    lock-on    colour  brackets snap inward from 1.18x, a flash pops, the label chip fades up
    settled    colour  thin brackets, ground trail, label chip with a confidence bar

Text is rendered with PIL (anti-aliased, real typeface) into a small RGBA patch that is
cached and alpha-composited — OpenCV's Hershey fonts look like a 1990s lab tool, which is
the opposite of the impression this screen exists to make.
"""
from __future__ import annotations

import numpy as np
import cv2
from PIL import Image, ImageDraw, ImageFont

# ── palette (RGB) — matches the web console's CSS variables ──────────────────
PALETTE = {
    "answer":  (0x69, 0xF1, 0xB9),   # identified   — mint
    "reject":  (0xFF, 0xC8, 0x6B),   # unknown car  — amber
    "abstain": (0x9A, 0x7B, 0xFF),   # not confident— violet
    "pending": (0x6C, 0xE5, 0xFF),   # scanning     — cyan
}
INK = (10, 16, 28)                   # chip background
LOCK_FRAMES = 11                     # length of the lock-on animation


def bgr(rgb):
    return (int(rgb[2]), int(rgb[1]), int(rgb[0]))


def _ease_out(k: float) -> float:
    k = min(max(k, 0.0), 1.0)
    return 1.0 - (1.0 - k) ** 3


# ── typography ───────────────────────────────────────────────────────────────
_FONT_CANDIDATES = [
    "C:/Windows/Fonts/segoeuib.ttf", "C:/Windows/Fonts/seguisb.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
]
_FONT_CANDIDATES_REG = [
    "C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
]
_font_cache: dict = {}


def _font(size: int, bold: bool = True):
    key = (size, bold)
    if key in _font_cache:
        return _font_cache[key]
    f = None
    for path in (_FONT_CANDIDATES if bold else _FONT_CANDIDATES_REG):
        try:
            f = ImageFont.truetype(path, size)
            break
        except Exception:
            continue
    if f is None:                       # Pillow >= 10.1 ships a scalable default
        try:
            f = ImageFont.load_default(size=size)
        except TypeError:
            f = ImageFont.load_default()
    _font_cache[key] = f
    return f


def _text_size(draw, text, font):
    l, t, r, b = draw.textbbox((0, 0), text, font=font)
    return r - l, b - t


# ── compositing ──────────────────────────────────────────────────────────────
def blit_rgba(frame, patch, x, y, alpha=1.0):
    """Alpha-composite an RGBA patch onto a BGR frame at (x, y), clipped to bounds."""
    if alpha <= 0.01:
        return
    H, W = frame.shape[:2]
    ph, pw = patch.shape[:2]
    sx, sy = max(0, -x), max(0, -y)
    x, y = max(0, x), max(0, y)
    ex, ey = min(W, x + pw - sx), min(H, y + ph - sy)
    if ex <= x or ey <= y:
        return
    sub = patch[sy:sy + (ey - y), sx:sx + (ex - x)]
    roi = frame[y:ey, x:ex].astype(np.float32)
    a = (sub[:, :, 3:4].astype(np.float32) / 255.0) * float(alpha)
    rgb = sub[:, :, :3][:, :, ::-1].astype(np.float32)          # RGB -> BGR
    frame[y:ey, x:ex] = np.clip(roi * (1 - a) + rgb * a, 0, 255).astype(np.uint8)


# ── the label chip ───────────────────────────────────────────────────────────
_chip_cache: dict = {}


def chip(title: str, sub: str, rgb, scale: float = 1.0, conf: float | None = None):
    """A rounded label card: accent bar, model name, sub-line, confidence bar.

    Cached by content — a settled track re-uses the same patch every frame, so the
    per-frame cost of the prettiest element on screen is one numpy blend.
    """
    key = (title, sub, rgb, round(scale, 2), None if conf is None else round(conf, 2))
    hit = _chip_cache.get(key)
    if hit is not None:
        return hit
    if len(_chip_cache) > 400:
        _chip_cache.clear()

    ft = _font(max(11, int(17 * scale)), True)
    fs = _font(max(9, int(12 * scale)), False)
    pad = max(7, int(10 * scale))
    acc = max(3, int(4 * scale))

    probe = ImageDraw.Draw(Image.new("RGBA", (8, 8)))
    tw, th = _text_size(probe, title, ft)
    sw, sh = _text_size(probe, sub, fs) if sub else (0, 0)

    inner_w = max(tw, sw, int(96 * scale))
    w = acc + pad + inner_w + pad
    h = pad + th + (int(5 * scale) + sh if sub else 0) + pad
    if conf is not None:
        h += int(7 * scale)

    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    r = max(5, int(8 * scale))
    d.rounded_rectangle([0, 0, w - 1, h - 1], radius=r, fill=INK + (231,),
                        outline=rgb + (130,), width=1)
    d.rounded_rectangle([0, r // 2, acc, h - 1 - r // 2], radius=acc // 2, fill=rgb + (255,))

    x0 = acc + pad
    d.text((x0, pad - 1), title, font=ft, fill=(246, 249, 255, 255))
    if sub:
        d.text((x0, pad + th + int(4 * scale)), sub, font=fs, fill=rgb + (205,))
    if conf is not None:
        by = h - pad // 2 - int(4 * scale)
        bw = inner_w
        d.rounded_rectangle([x0, by, x0 + bw, by + max(2, int(3 * scale))],
                            radius=2, fill=(255, 255, 255, 46))
        fill_w = int(bw * min(max(conf, 0.0), 1.0))
        if fill_w > 2:
            d.rounded_rectangle([x0, by, x0 + fill_w, by + max(2, int(3 * scale))],
                                radius=2, fill=rgb + (255,))

    patch = np.array(img, dtype=np.uint8)
    _chip_cache[key] = patch
    return patch


# ── primitives ───────────────────────────────────────────────────────────────
def draw_brackets(frame, box, color, thickness=2, frac=0.24, grow=0):
    """L-shaped corner brackets. `grow` pushes them outward for the lock-on snap."""
    x1, y1, x2, y2 = box
    x1, y1, x2, y2 = x1 - grow, y1 - grow, x2 + grow, y2 + grow
    L = int(min(x2 - x1, y2 - y1) * frac)
    if L < 4:
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, thickness, cv2.LINE_AA)
        return
    for (cx, cy, dx, dy) in ((x1, y1, 1, 1), (x2, y1, -1, 1), (x1, y2, 1, -1), (x2, y2, -1, -1)):
        cv2.line(frame, (cx, cy), (cx + dx * L, cy), color, thickness, cv2.LINE_AA)
        cv2.line(frame, (cx, cy), (cx, cy + dy * L), color, thickness, cv2.LINE_AA)


def draw_scan(frame, box, color, phase, tail=0.30):
    """A sweep band travelling down the box — the visual for 'gathering evidence'."""
    x1, y1, x2, y2 = box
    h, w = y2 - y1, x2 - x1
    if h < 10 or w < 10:
        return
    yc = y1 + int(phase * h)
    top = max(y1, yc - int(h * tail))
    if yc - top < 2:
        return
    roi = frame[top:yc, x1:x2].astype(np.float32)
    n = roi.shape[0]
    wgt = (np.linspace(0.0, 1.0, n) ** 3 * 0.34).astype(np.float32)[:, None, None]
    col = np.array(color, dtype=np.float32)
    frame[top:yc, x1:x2] = np.clip(roi * (1 - wgt) + col * wgt, 0, 255).astype(np.uint8)
    cv2.line(frame, (x1, yc), (x2, yc), color, 1, cv2.LINE_AA)


def draw_trail(frame, pts, color, alpha=0.55):
    """Fading ground track of the vehicle — reads as 'this thing is being followed'."""
    if len(pts) < 3:
        return
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
    m = 6
    x1, y1 = max(0, min(xs) - m), max(0, min(ys) - m)
    x2 = min(frame.shape[1], max(xs) + m); y2 = min(frame.shape[0], max(ys) + m)
    if x2 - x1 < 2 or y2 - y1 < 2:
        return
    roi = frame[y1:y2, x1:x2]
    ov = roi.copy()
    n = len(pts)
    for i in range(1, n):
        f = i / n
        cv2.line(ov, (pts[i - 1][0] - x1, pts[i - 1][1] - y1),
                 (pts[i][0] - x1, pts[i][1] - y1),
                 tuple(int(c * (0.30 + 0.70 * f)) for c in color),
                 max(1, int(1 + 2 * f)), cv2.LINE_AA)
    cv2.addWeighted(ov, alpha, roi, 1 - alpha, 0, roi)


def flash(frame, box, color, alpha):
    """A brief wash of colour inside the box at the instant of lock-on."""
    if alpha <= 0.01:
        return
    x1, y1, x2, y2 = box
    roi = frame[y1:y2, x1:x2]
    if roi.size == 0:
        return
    col = np.full_like(roi, np.array(color, dtype=np.uint8))
    cv2.addWeighted(col, float(alpha), roi, 1 - float(alpha), 0, roi)
