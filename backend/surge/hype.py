"""Chat + audio + voice hype signals, shared by live monitoring and VOD analysis.

Every signal is judged against the channel's own baseline (median + MAD), so a burst of 15 messages in a
small chat counts like 400 in a big one. Chat counts distinct chatters too, so one spammer can't fake a spike.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

import numpy as np

# reaction classes: what kind of moment chat thinks it was
LEXICON: dict[str, dict[str, float]] = {
    "funny": {"kekw": 2, "lul": 1.5, "lol": 1, "lmao": 2, "lmfao": 2.5, "omegalul": 2.5, "icant": 2, "xd": 1,
              "kek": 1.5, "lulw": 2, "pepelaugh": 2, "dead": 1, "crying": 1.5, "😂": 1.5, "🤣": 2, "😭": 1.5,
              "💀": 1.5, "hahaha": 1.5, "haha": 1, "bruh": 1},
    "hype": {"pog": 1.5, "poggers": 2, "pogchamp": 2, "pogu": 2, "w": 1, "dub": 1, "goated": 2, "insane": 2,
             "clean": 1.5, "sheesh": 1.5, "letsgo": 2, "lets": 0.5, "🔥": 1, "ez": 0.5, "gigachad": 2,
             "massive": 1, "huge": 1, "cracked": 2},
    "shock": {"wtf": 2, "omg": 1.5, "holy": 1.5, "monkas": 2, "monkaw": 2, "what": 1, "nah": 1, "nahh": 1.5,
              "bro": 0.5, "😱": 1.5, "😳": 1.5, "wait": 1, "hold": 0.5, "yo": 1, "omfg": 2.5},
    "fail": {"rip": 1.5, "f": 1, "l": 1, "wasted": 2, "busted": 2, "unlucky": 1.5, "sadge": 1.5, "oof": 1.5,
             "copium": 1.5, "throw": 1.5, "threw": 1.5, "fail": 1.5, "noooo": 2},
    "clip": {"clip": 3, "clipped": 3, "clipit": 3},
}
PHRASES: dict[str, tuple[str, float]] = {
    "clip it": ("clip", 3), "clip that": ("clip", 3), "+clip": ("clip", 3), "no way": ("shock", 2),
    "let's go": ("hype", 2), "lets go": ("hype", 2), "i can't": ("funny", 2), "i cant": ("funny", 2),
    "????": ("shock", 1.5), "!!!": ("hype", 1), "oh my god": ("shock", 2), "skill issue": ("fail", 1.5),
}
_TOKEN = re.compile(r"[\w']+|[^\w\s]", re.UNICODE)
_REPEAT = re.compile(r"(.)\1{2,}")

# what the streamer shouting means (from the live transcript)
VOICE = {"no way": 2.5, "let's go": 2.5, "lets go": 2.5, "oh my god": 2.5, "what the": 2, "bro": 1, "dude": 1,
         "run": 1, "go go": 2, "holy": 2, "yo": 1, "what": 0.8, "no no": 2, "wait": 1, "clip": 2, "dude what": 2.5,
         "are you serious": 2, "chat": 0.6, "how": 0.6, "insane": 1.5, "crazy": 1.2}


def message_weight(text: str) -> tuple[float, str | None]:
    """1.0 for a plain message, up to 4.0 for a pure reaction. Returns (weight, dominant class)."""
    low = _REPEAT.sub(r"\1\1", text.lower())
    by_class: dict[str, float] = defaultdict(float)
    for tok in _TOKEN.findall(low):
        for cls, words in LEXICON.items():
            if (w := words.get(tok)) is not None:
                by_class[cls] += w
    for phrase, (cls, w) in PHRASES.items():
        if phrase in low:
            by_class[cls] += w
    letters = [c for c in text if c.isalpha()]
    if len(letters) >= 4 and sum(c.isupper() for c in letters) / len(letters) > 0.7:
        by_class["shock" if not by_class else max(by_class, key=by_class.get)] += 0.75
    extra = sum(by_class.values())
    cls = max(by_class, key=by_class.get) if by_class else None
    return 1.0 + min(3.0, extra), cls


def voice_weight(text: str) -> float:
    low = text.lower()
    return sum(w for p, w in VOICE.items() if p in low)


def normalize_msg(text: str) -> str:
    return _REPEAT.sub(r"\1\1", text.strip().lower())[:40]


def robust_z(x: np.ndarray, past: np.ndarray) -> float:
    if len(past) < 8:
        return 0.0
    med = float(np.median(past))
    mad = float(np.median(np.abs(past - med))) * 1.4826
    return float((x - med) / (mad + 1e-3 + 0.05 * abs(med)))


def robust_z_series(x: np.ndarray, window: int = 240, warmup: int = 20) -> np.ndarray:
    """Trailing-baseline z for every bin (live-style: only the past informs each bin)."""
    x = np.asarray(x, np.float32)
    z = np.zeros(len(x), np.float32)
    for i in range(warmup, len(x)):
        z[i] = robust_z(x[i], x[max(0, i - window):i])
    return np.clip(z, -3, 12)


def centered_z(x: np.ndarray, window: int = 600) -> np.ndarray:
    """VOD-style z against a centered rolling median (the whole stream is known)."""
    x = np.asarray(x, np.float32)
    n = len(x)
    if n == 0:
        return x
    stride = 15
    idx = np.arange(0, n, stride)
    half = window // 2
    med = np.array([np.median(x[max(0, i - half):i + half + 1]) for i in idx], np.float32)
    mad = np.array([np.median(np.abs(x[max(0, i - half):i + half + 1] - m)) for i, m in zip(idx, med)],
                   np.float32) * 1.4826
    med_i = np.interp(np.arange(n), idx, med)
    mad_i = np.interp(np.arange(n), idx, mad)
    return np.clip((x - med_i) / (mad_i + 1e-3 + 0.05 * np.abs(med_i)), -3, 12).astype(np.float32)


def smooth(x: np.ndarray, k: int) -> np.ndarray:
    if k <= 1 or len(x) == 0:
        return np.asarray(x, np.float32)
    return np.convolve(x, np.ones(k, np.float32) / k, mode="same").astype(np.float32)


def fuse(z_chat: np.ndarray | float, z_people: np.ndarray | float, z_audio: np.ndarray | float,
         z_voice: np.ndarray | float, has_chat: bool) -> np.ndarray | float:
    """Combined excitement. Positive evidence only: a quiet chat doesn't cancel a scream."""
    pos = lambda v: np.maximum(v, 0)  # noqa: E731
    if has_chat:
        chat = 0.55 * pos(z_chat) + 0.45 * pos(z_people)
        return 0.50 * chat + 0.32 * pos(z_audio) + 0.18 * pos(z_voice) + 0.15 * np.minimum(chat, pos(z_audio))
    return 0.70 * pos(z_audio) + 0.30 * pos(z_voice)


@dataclass
class ChatWindow:
    """Rolling chat stats for one channel, bucketed per second of media time."""
    weighted: dict[int, float] = field(default_factory=lambda: defaultdict(float))
    count: dict[int, int] = field(default_factory=lambda: defaultdict(int))
    users: dict[int, set] = field(default_factory=lambda: defaultdict(set))
    classes: dict[int, Counter] = field(default_factory=lambda: defaultdict(Counter))
    texts: dict[int, list] = field(default_factory=lambda: defaultdict(list))
    total: int = 0

    def add(self, sec: int, user: str, text: str, boost: float = 1.0) -> None:
        w, cls = message_weight(text)
        self.weighted[sec] += w * boost
        self.count[sec] += 1
        self.users[sec].add(user)
        if cls:
            self.classes[sec][cls] += 1
        self.texts[sec].append(text)
        self.total += 1

    def series(self, a: int, b: int) -> tuple[np.ndarray, np.ndarray]:
        w = np.array([self.weighted.get(s, 0.0) for s in range(a, b)], np.float32)
        u = np.array([len(self.users.get(s, ())) for s in range(a, b)], np.float32)
        return w, u

    def top(self, a: float, b: float, k: int = 8) -> list[list]:
        c: Counter = Counter()
        for s in range(int(a), int(b) + 1):
            for t in self.texts.get(s, ()):
                if n := normalize_msg(t):
                    c[n] += 1
        return [[m, n] for m, n in c.most_common(k)]

    def dominant_class(self, a: float, b: float) -> str | None:
        c: Counter = Counter()
        for s in range(int(a), int(b) + 1):
            c.update(self.classes.get(s, {}))
        return c.most_common(1)[0][0] if c else None

    def rate(self, a: int, b: int) -> float:
        return sum(self.count.get(s, 0) for s in range(a, b)) * 60.0 / max(1, b - a)

    def chatters(self, a: int, b: int) -> int:
        seen: set = set()
        for s in range(a, b):
            seen |= self.users.get(s, set())
        return len(seen)

    def prune(self, before: int) -> None:
        for d in (self.weighted, self.count, self.users, self.classes, self.texts):
            for k in [k for k in d if k < before]:
                del d[k]


def loudness_db(pcm: np.ndarray, sr: int = 16000) -> np.ndarray:
    """Per-second RMS loudness in dBFS from int16 mono PCM."""
    n = len(pcm) // sr
    if n == 0:
        return np.zeros(0, np.float32)
    x = pcm[: n * sr].astype(np.float32).reshape(n, sr) / 32768.0
    rms = np.sqrt(np.mean(x * x, axis=1) + 1e-10)
    return (20 * np.log10(rms)).astype(np.float32)


def pick_peaks(score: np.ndarray, n: int, min_gap: int, min_score: float = 0.0,
               skip_start: int = 0, skip_end: int = 0) -> list[int]:
    hi = len(score) - skip_end
    picked: list[int] = []
    for i in map(int, np.argsort(-score, kind="stable")):
        if score[i] <= min_score or len(picked) >= n:
            break
        if skip_start <= i < hi and all(abs(i - p) >= min_gap for p in picked):
            picked.append(i)
    return sorted(picked)
