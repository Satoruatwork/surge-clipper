"""Find the exact moment inside a clip's audio: where the excitement starts (onset) and where it has died down
(decay). Chat and 1-second scoring only say roughly when something happened; the hit sound, the punch zoom and
the cut points need to land on the real frame of the moment."""
from __future__ import annotations

import numpy as np

HOP = 0.02  # 20 ms resolution


def envelope(pcm: np.ndarray, sr: int = 16000) -> np.ndarray:
    """Loudness envelope in dB, one value per 20 ms."""
    hop = int(sr * HOP)
    n = len(pcm) // hop
    if n == 0:
        return np.zeros(0, np.float32)
    x = pcm[: n * hop].astype(np.float32)
    if pcm.dtype == np.int16:
        x /= 32768.0
    rms = np.sqrt(np.mean(x.reshape(n, hop) ** 2, axis=1) + 1e-10)
    db = 20 * np.log10(rms)
    k = 5  # 100 ms smoothing
    return np.convolve(db, np.ones(k) / k, mode="same").astype(np.float32)


def onset(env: np.ndarray, peak: float, before: float = 5.0, after: float = 2.5) -> float:
    """Start of the biggest loudness jump near `peak` (seconds into the audio). Falls back to `peak`."""
    if len(env) < 50:
        return peak
    i0 = max(25, int((peak - before) / HOP))
    i1 = min(len(env) - 1, int((peak + after) / HOP))
    if i1 <= i0:
        return peak
    best, at = 0.0, None
    for i in range(i0, i1):
        floor = float(np.percentile(env[max(0, i - 75):i], 30))   # what "normal" sounded like in the last 1.5 s
        rise = float(np.mean(env[i:i + 10])) - floor               # how much louder the next 200 ms is
        if rise > best:
            best, at = rise, i
    if at is None or best < 6.0:  # no clear jump: trust the coarse peak
        return peak
    floor = float(np.percentile(env[max(0, at - 75):at], 30))
    target = floor + best * 0.5
    j = at
    while j > max(0, at - 15) and env[j - 1] > target:  # walk back to where the rise begins
        j -= 1
    return round(j * HOP, 2)


def decay(env: np.ndarray, start: float, min_after: float = 2.5, max_after: float = 15.0) -> float:
    """When the reaction has died down: loudness back near the pre-moment level for a full second."""
    if len(env) < 50:
        return start + min_after
    i_on = int(start / HOP)
    base = float(np.percentile(env[max(0, i_on - 150):max(1, i_on)], 50))
    hold = int(1.0 / HOP)
    i = int((start + min_after) / HOP)
    stop = min(len(env) - hold, int((start + max_after) / HOP))
    while i < stop:
        if np.all(env[i:i + hold] < base + 3.0):
            return round(i * HOP, 2)
        i += 5
    return round(min(len(env) * HOP, start + max_after), 2)


def best_chat_lag(chat: np.ndarray, audio: np.ndarray, max_lag: int = 15) -> tuple[int, float]:
    """Seconds by which chat trails the stream: the shift that best lines chat bursts up with loud moments."""
    n = min(len(chat), len(audio))
    if n < 120:
        return -1, 0.0
    c = np.asarray(chat[:n], np.float32)
    a = np.asarray(audio[:n], np.float32)
    c = (c - c.mean()) / (c.std() + 1e-6)
    a = (a - a.mean()) / (a.std() + 1e-6)
    best, at = -1.0, -1
    for lag in range(0, max_lag + 1):
        x = a[: n - lag]
        y = c[lag:n]
        r = float(np.dot(x, y) / len(x))
        if r > best:
            best, at = r, lag
    return at, round(best, 3)
