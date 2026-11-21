"""Find the streamer's webcam overlay: YuNet faces on several frames, clustered to the stable one, then the box is
snapped to the overlay's real borders (persistent straight edges around the face) so the facecam band never
carries a slice of the game with it."""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from . import tools
from .config import ASSETS

MODEL = ASSETS / "face_detection_yunet_2023mar.onnx"


def grab_frame(src: str | Path, t: float):
    import cv2
    p = tools.run([tools.ffmpeg(), "-v", "error", "-ss", f"{max(0.0, t):.2f}", "-i", str(src), "-frames:v", "1",
                   "-f", "image2pipe", "-vcodec", "png", "-"], timeout=60, check=False)
    if p.returncode != 0 or not p.stdout:
        return None
    return cv2.imdecode(np.frombuffer(p.stdout, np.uint8), cv2.IMREAD_COLOR)


def detect(frames: list) -> list[list[tuple]]:
    import cv2
    det, out = None, []
    for img in frames:
        if img is None:
            continue
        h, w = img.shape[:2]
        if det is None:
            det = cv2.FaceDetectorYN.create(str(MODEL), "", (w, h), 0.75, 0.3, 50)
        det.setInputSize((w, h))
        _, faces = det.detect(img)
        out.append([(float(f[0]), float(f[1]), float(f[2]), float(f[3]), float(f[-1]))
                    for f in (faces if faces is not None else [])])
    return out


def cluster(dets: list[list[tuple]], fw: int, min_share: float = 0.5):
    flat = [(fi, d) for fi, frame in enumerate(dets) for d in frame]
    if not flat:
        return None
    tol = 0.05 * fw
    best, best_frames = None, 0
    for _, d in flat:
        cx, cy = d[0] + d[2] / 2, d[1] + d[3] / 2
        members = [(fi, e) for fi, e in flat if abs(e[0] + e[2] / 2 - cx) < tol and abs(e[1] + e[3] / 2 - cy) < tol]
        frames = len({fi for fi, _ in members})
        if frames > best_frames:
            best_frames, best = frames, [e for _, e in members]
    if best_frames < max(2, math.ceil(min_share * len(dets))):
        return None
    return tuple(float(v) for v in np.median(np.array([b[:4] for b in best], np.float32), axis=0))


def cam_box(face, fw: int, fh: int, aspect: float, scale: float = 2.2) -> list[int]:
    """Fallback when the overlay has no visible border: a head-and-shoulders box around the face, pushed against
    the nearest frame edge (webcams sit in corners)."""
    x, y, w, h = face
    cw = min(fw, int(w * scale))
    ch = min(fh, int(cw / aspect))
    cw = min(fw, int(ch * aspect))
    cx, cy = x + w / 2, y + h / 2
    bx = int(min(max(0, cx - cw / 2), fw - cw))
    by = int(min(max(0, cy - ch * 0.42), fh - ch))
    if bx < fw * 0.04:
        bx = 0
    elif bx + cw > fw * 0.96:
        bx = fw - cw
    if by + ch > fh * 0.96:
        by = fh - ch
    elif by < fh * 0.04:
        by = 0
    return [bx - bx % 2, by - by % 2, cw - cw % 2, ch - ch % 2]


def _line_strength(edge: np.ndarray, vertical: bool, pos: int, a: int, b: int) -> float:
    """Share of the span [a, b) covered by an edge on line `pos` (±1 px)."""
    if b - a < 8:
        return 0.0
    if vertical:
        band = edge[a:b, max(0, pos - 1):pos + 2]
    else:
        band = edge[max(0, pos - 1):pos + 2, a:b].T
    return float(band.max(axis=1).mean()) if band.size else 0.0


def persistent_edges(frames: list, fw: int) -> np.ndarray | None:
    """Edges that stay put while the picture changes: an overlay's border, not the game moving behind it."""
    import cv2
    grays = [cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) for f in frames if f is not None and f.shape[1] == fw]
    if len(grays) < 3:
        return None
    return np.mean([cv2.Canny(cv2.GaussianBlur(g, (3, 3), 0), 40, 120) > 0 for g in grays], axis=0) >= 0.5


def box_fit(edge: np.ndarray, box) -> float:
    """How well a box's four sides sit on long static lines (1 = all of them). A side on the frame's own edge
    counts as a fit, since overlays often sit in a corner. Allows a few pixels of slack for hand-drawn boxes."""
    fh, fw = edge.shape
    x, y, w, h = (int(v) for v in box)
    sides = []
    for vertical, pos, a, b, limit in ((False, y - 2, x, x + w, fh), (False, y + h + 2, x, x + w, fh),
                                       (True, x - 2, y, y + h, fw), (True, x + w + 2, y, y + h, fw)):
        if pos <= 6 or pos >= limit - 7:
            sides.append(1.0)
        else:
            sides.append(max(_line_strength(edge, vertical, p, a + 4, b - 4) for p in range(pos - 8, pos + 9)))
    return float(np.mean(sides))


def border_box(frames: list, face, fw: int, fh: int, edge: np.ndarray | None = None) -> list[int] | None:
    """Snap to the overlay's own frame: the nearest long straight edges around the face that stay put while the
    picture changes. Returns [x, y, w, h] or None when the overlay has no clear border."""
    edge = edge if edge is not None else persistent_edges(frames, fw)
    if edge is None:
        return None
    x, y, w, h = (int(v) for v in face)
    cx, cy = x + w // 2, y + h // 2
    # a border must at least run along the face's own rows/columns
    ya, yb = max(0, y + h // 10), min(fh, y + int(h * 0.9))

    def scan(vertical: bool, start: int, stop: int, step: int, a: int, b: int) -> int | None:
        for pos in range(start, stop, step):
            if _line_strength(edge, vertical, pos, a, b) >= 0.75:
                return pos
        return None

    left = scan(True, x - 3, max(-1, cx - 7 * w), -1, ya, yb)
    right = scan(True, x + w + 3, min(fw, cx + 7 * w), 1, ya, yb)
    left = 0 if left is None and x < 5 * w else left
    right = fw - 1 if right is None and fw - (x + w) < 5 * w else right
    if left is None or right is None:
        return None
    top = scan(False, y - 3, max(-1, cy - 6 * h), -1, left + 4, right - 4)
    bottom = scan(False, y + h + 3, min(fh, cy + 6 * h), 1, left + 4, right - 4)
    top = 0 if top is None and y < 4 * h else top
    bottom = fh - 1 if bottom is None and fh - (y + h) < 5 * h else bottom
    if top is None or bottom is None:
        return None
    bw, bh = right - left - 4, bottom - top - 4
    if bw < w * 1.05 or bh < min(h, fh - y) * 0.8 or bw * bh > 0.36 * fw * fh or not (0.6 <= bw / max(bh, 1) <= 3.2):
        return None
    bx, by = left + 2, top + 2
    return [bx - bx % 2, by - by % 2, bw - bw % 2, bh - bh % 2]


def find(src: str | Path, times: list[float], size: tuple[int, int], aspect: float,
         known: list | tuple = ()) -> dict:
    """Returns {'box': [x,y,w,h] | None, 'face': ..., 'kind': 'cam'|'fullcam'|'none',
    'how': 'border'|'known'|'estimate'}. `known`: boxes seen for this streamer before. When the border can't be
    traced (the streamer stood up, the face is small), the known box whose sides sit on this footage's static
    edges and that holds the face wins over a guess around the face: streamers move their cam between streams."""
    fw, fh = size
    frames = [grab_frame(src, t) for t in times]
    face = cluster(detect(frames), fw)
    if not face:
        return {"box": None, "face": None, "kind": "none"}
    cx, cy = (face[0] + face[2] / 2) / fw, (face[1] + face[3] / 2) / fh
    in_corner = cx < 0.34 or cx > 0.66 or cy > 0.72
    if face[2] > fw * 0.22 or not in_corner:
        # a big or centred face is the scene itself ("just chatting" camera), not a webcam overlay
        return {"box": None, "face": [int(v) for v in face], "kind": "fullcam"}
    edge = persistent_edges(frames, fw)
    cands = []
    traced = border_box(frames, face, fw, fh, edge)
    if traced:
        cands.append((box_fit(edge, traced), "border", traced))
    if edge is not None:  # a static line in the streamer's room can stop the trace short of the real border
        fx, fy = face[0] + face[2] / 2, face[1] + face[3] / 2
        cands += [(box_fit(edge, b), "known", list(b)) for b in known
                  if b and b[0] <= fx <= b[0] + b[2] and b[1] <= fy <= b[1] + b[3]]
    cands = [c for c in cands if c[0] >= 0.6]
    if cands:  # best fit; among near-equal fits the bigger box, which reaches the overlay's real edge
        fit, how, box = max(cands, key=lambda c: (round(c[0], 1), c[2][2] * c[2][3]))
        return {"box": box, "face": [int(v) for v in face], "kind": "cam", "how": how, "fit": round(fit, 3)}
    return {"box": cam_box(face, fw, fh, aspect), "face": [int(v) for v in face], "kind": "cam", "how": "estimate",
            "fit": 0.0}
