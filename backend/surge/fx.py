"""Edit layer: meme sounds, punch-in zooms, shakes, flashes, emoji stickers and a music bed whose drop lands on
the moment. A plan is a list of cues on the clip's own timeline; Claude writes it when a key is set, otherwise
the built-in director places cues from the chat reaction, the peak and the gaps in speech."""
from __future__ import annotations

import math
import re

from . import config, library

KINDS = ("sound", "zoom", "shake", "flash", "sticker")
LEVEL_CUES = {"off": 0, "subtle": 2, "medium": 4, "max": 7}

STICKERS_FOR = {
    "funny": ["skull", "crying", "laughing", "rofl", "clown"],
    "shock": ["flushed", "mindblown", "scream", "eyes", "exclamation"],
    "hype": ["fire", "hundred", "goat", "trophy", "salute"],
    "fail": ["skull", "clown", "crying", "moai"],
    "clip": ["hundred", "fire", "eyes"],
    None: ["eyes", "moai", "exclamation", "flushed"],
}
WORD_STICKERS = [
    (r"\b(cop|cops|police|wanted|sirens?|swat)\b", ["police", "siren"]),
    (r"\b(money|cash|bank|rich|bands|million)\b", ["money"]),
    (r"\b(cap|lying|liar|lie)\b", ["cap"]),
    (r"\b(goat|best|legend)\b", ["goat"]),
    (r"\b(what|huh|wait)\b", ["question", "eyes"]),
]
MUSIC_FOR = {"funny": "funny", "fail": "awkward", "shock": "tension", "hype": "hype", "clip": "hype"}


def _gaps(words: list[dict], duration: float, min_gap: float = 1.2) -> list[tuple[float, float]]:
    out, last = [], 0.0
    for w in words:
        if w["s"] - last >= min_gap:
            out.append((last, w["s"]))
        last = max(last, w["e"])
    if duration - last >= min_gap:
        out.append((last, duration))
    return out


def _after_speech(words: list[dict], t: float, duration: float) -> float:
    """First pause at or after t (so a tail sound doesn't talk over the streamer)."""
    for a, b in _gaps(words, duration, 0.6):
        if b > t and b - max(a, t) >= 0.6:
            return round(max(a, t) + 0.05, 2)
    return round(min(t, duration - 1.0), 2)


def heuristic(words: list[dict], duration: float, peak: float, cls: str | None, transcript: str = "",
              level: str | None = None, salt: int = 0) -> dict:
    level = level or config.get("fx_level") or "medium"
    budget = LEVEL_CUES.get(level, 4)
    if budget == 0:
        return {"cues": [], "music": None, "level": level, "source": "builtin"}
    p = round(max(0.5, min(peak, duration - 1.0)), 2)
    st = STICKERS_FOR.get(cls, STICKERS_FOR[None])
    pick = lambda lst, k=0: lst[(salt + k) % len(lst)]  # noqa: E731
    cues: list[dict] = []
    if cls == "funny":
        p = round(p, 2)
        cues += [{"t": p, "kind": "zoom", "why": "punch in on the laugh"},
                 {"t": p, "kind": "sound", "tags": ["funny", "impact"], "why": "comic hit"},
                 {"t": round(p + 0.35, 2), "kind": "sticker", "name": pick(st), "why": "reaction"}]
    elif cls == "fail":
        cues += [{"t": p, "kind": "sound", "tags": ["fail", "stop"], "why": "it went wrong"},
                 {"t": p, "kind": "zoom", "why": "punch in on the fail"},
                 {"t": p + 0.35, "kind": "sticker", "name": pick(st), "why": "reaction"},
                 {"t": _after_speech(words, p + 1.2, duration), "kind": "sound", "tags": ["sad", "loss"],
                  "why": "sad tail"}]
    elif cls in ("hype", "clip"):
        cues += [{"t": p, "kind": "zoom", "why": "impact"},
                 {"t": p + 0.05, "kind": "sound", "tags": ["hype", "win"], "why": "hype hit"},
                 {"t": p, "kind": "shake", "d": 0.6, "why": "energy"},
                 {"t": p + 0.35, "kind": "sticker", "name": pick(st), "why": "reaction"}]
    else:  # shock or unknown: build tension, hit hard on the reveal
        cues += [{"t": max(0.0, p - 2.4), "kind": "sound", "tags": ["suspense", "buildup"], "end": p,
                  "why": "build-up into the moment"},
                 {"t": p, "kind": "sound", "tags": ["impact", "shock"], "why": "the reveal"},
                 {"t": p, "kind": "zoom", "why": "punch in"},
                 {"t": p, "kind": "flash", "why": "hit frame"},
                 {"t": p + 0.4, "kind": "sticker", "name": pick(st), "why": "reaction"}]
    for pat, names in WORD_STICKERS:  # stickers that match what's said
        m = next((w for w in words if re.search(pat, w["w"].lower())), None)
        if m and abs(m["s"] - p) > 1.5:
            cues.append({"t": round(m["s"], 2), "kind": "sticker", "name": pick(names), "why": f"said \"{m['w']}\""})
            break
    # keep people watching through the slow part before the payoff
    for a, b in _gaps(words, duration, 1.6):
        mid = round((a + b) / 2, 2)
        if 1.0 < mid < p - 2.5:
            cues.append({"t": mid, "kind": "zoom", "amount": 0.06, "why": "keep the slow part moving"})
            break
    cues = sorted(cues, key=lambda c: (c["kind"] not in ("sound", "zoom"), abs(c["t"] - p)))[: max(budget, 2) + 1]
    music = None
    mood = MUSIC_FOR.get(cls or "")
    mode = config.get("music_mode") or "fits"
    if mode == "always" or (mode == "fits" and cls in ("funny", "fail") or
                            mode == "fits" and re.search(r"\b(cops?|police|chase|run|drive|go go)\b", transcript.lower())):
        if re.search(r"\b(cops?|police|chase|run|drive)\b", transcript.lower()):
            mood = "chase"
        song = library.pick_song(mood or "funny", salt) or library.pick_song("funny", salt)
        if song:
            music = {"song": song["id"], "mood": mood or "funny"}
    return {"cues": sorted(cues, key=lambda c: c["t"]), "music": music, "level": level, "source": "builtin"}


def wants_sounds(clip_id: int) -> bool:
    """Sound ratio: only this share of clips gets meme sounds, so they keep their punch (the rest stay visual)."""
    ratio = max(0, min(100, int(config.get("sfx_ratio") if config.get("sfx_ratio") is not None else 70)))
    return (clip_id * 37 + 11) % 100 < ratio


def apply_ratio(plan: dict | None, clip_id: int) -> dict | None:
    if plan and not wants_sounds(clip_id):
        plan = {**plan, "cues": [c for c in plan["cues"] if c["kind"] != "sound"], "sounds_skipped": True}
    return plan


def vocabulary() -> dict:
    sounds = library.items("sound")
    tags = sorted({t for s in sounds for t in s.get("tags") or []})
    moods = sorted({m for s in library.items("song") for m in s.get("moods") or []})
    sounds = sorted(sounds, key=lambda s: -float(s.get("rank", 5)))
    return {"sound_tags": tags, "sound_names": [s["name"] for s in sounds][:60],
            "stickers": list(library.stickers()), "music_moods": moods}


def clean(plan: dict | None, duration: float, level: str | None = None) -> dict | None:
    """Validate a plan from Claude or the editor: known kinds, names that exist, times inside the clip."""
    if not plan:
        return None
    level = level or plan.get("level") or config.get("fx_level") or "medium"
    stick = library.stickers()
    names = {s["name"]: s for s in library.items("sound")}
    out = []
    for c in plan.get("cues") or []:
        kind = c.get("kind")
        if kind not in KINDS:
            continue
        try:
            t = float(c.get("t", 0))
        except (TypeError, ValueError):
            continue
        if not (0 <= t < duration - 0.1):
            continue
        cue = {"t": round(t, 2), "kind": kind, "why": str(c.get("why") or "")[:80]}
        if kind == "sound":
            if c.get("name") in names:
                cue["name"] = c["name"]
            elif c.get("tags"):
                cue["tags"] = [str(x) for x in c["tags"]][:4]
            else:
                continue
            if c.get("end") is not None:
                cue["end"] = float(c["end"])
        elif kind == "sticker":
            if c.get("name") not in stick:
                continue
            cue["name"] = c["name"]
        elif kind == "shake":
            cue["d"] = min(1.5, max(0.2, float(c.get("d", 0.6))))
        elif kind == "zoom" and c.get("amount"):
            cue["amount"] = min(0.3, max(0.03, float(c["amount"])))
        out.append(cue)
    music = plan.get("music")
    if music and not (music.get("song") and library.get(music["song"])):
        song = library.pick_song(str(music.get("mood") or ""), 0) if music.get("mood") else None
        music = {"song": song["id"], "mood": music.get("mood")} if song else None
    return {"cues": sorted(out, key=lambda c: c["t"])[: LEVEL_CUES.get(level, 4) + 3], "music": music,
            "level": level, "source": plan.get("source", "edited")}


def resolve(plan: dict, words: list[dict], duration: float, peak: float, salt: int) -> dict:
    """Turn tags into concrete files and compute timing (lead sounds end on their mark, tails wait for a pause)."""
    used: set[str] = set()
    sounds = []
    for c in plan.get("cues", []):
        if c["kind"] != "sound":
            continue
        item = None
        if c.get("name"):
            item = next((s for s in library.items("sound") if s["name"] == c["name"]), None)
        if item is None:
            item = library.pick_sound(c.get("tags") or [], salt + len(sounds), used)
        if item is None:
            continue
        used.add(item["id"])
        start = c["t"]
        if item.get("cue") == "lead":
            end = c.get("end", c["t"])
            start = max(0.0, (end if end > c["t"] else c["t"] + item.get("duration", 1.5)) - item.get("duration", 1.5))
            start = max(0.0, min(start, duration - 0.3))
        elif item.get("cue") == "tail":
            start = _after_speech(words, c["t"], duration)
        sounds.append({"file": item["file"], "t": round(start, 2), "gain": float(item.get("gain") or 0),
                       "name": item["name"]})
    music = None
    m = plan.get("music")
    if m and m.get("song"):
        song = library.get(m["song"])
        if song:
            drop = float(song.get("drop") or 0)
            offset, delay = (drop - peak, 0.0) if drop >= peak else (0.0, peak - drop)
            music = {"file": song["file"], "offset": round(max(0.0, offset), 2), "delay": round(delay, 2),
                     "gain": float(song.get("gain") or 0), "credit": song.get("credit"), "name": song["name"]}
    return {"sounds": sounds, "music": music,
            "zooms": [c for c in plan.get("cues", []) if c["kind"] == "zoom"],
            "shakes": [c for c in plan.get("cues", []) if c["kind"] == "shake"],
            "flashes": [c["t"] for c in plan.get("cues", []) if c["kind"] == "flash"],
            "stickers": [c for c in plan.get("cues", []) if c["kind"] == "sticker"]}


# ---------------------------------------------------------------- ffmpeg fragments

def zoompan(zooms: list[dict], shakes: list[dict], fps: int, w: int, h: int) -> str | None:
    if not zooms and not shakes:
        return None
    z_terms = [f"{c.get('amount', 0.13):.3f}*exp(-5.5*(it-{c['t']:.2f}))*gte(it,{c['t']:.2f})*lt(it,{c['t'] + 1.4:.2f})"
               for c in zooms]
    sx, sy = [], []
    for c in shakes:
        a, b = c["t"], c["t"] + c.get("d", 0.6)
        z_terms.append(f"0.06*between(it,{a:.2f},{b:.2f})")
        sx.append(f"22*sin(it*73)*exp(-3*(it-{a:.2f}))*between(it,{a:.2f},{b:.2f})")
        sy.append(f"16*cos(it*91)*exp(-3*(it-{a:.2f}))*between(it,{a:.2f},{b:.2f})")
    z = "1+" + "+".join(z_terms)
    x = "iw/2-(iw/zoom/2)" + "".join(f"+{e}" for e in sx)
    y = "ih/2-(ih/zoom/2)" + "".join(f"+{e}" for e in sy)
    return f"zoompan=z='{z}':x='{x}':y='{y}':d=1:s={w}x{h}:fps={fps}"


def flashes(times: list[float]) -> str | None:
    if not times:
        return None
    cond = "+".join(f"between(t,{t:.2f},{t + 0.07:.2f})" for t in times)
    return f"drawbox=x=0:y=0:w=iw:h=ih:color=white@0.55:t=fill:enable='{cond}'"


def sticker_spots(layout: str, seam: int) -> list[tuple[int, int]]:
    if layout == "split":
        return [(770, seam + 60), (50, seam + 60), (770, seam - 330)]
    return [(770, 520), (50, 520), (770, 1500)]


def is_on(level: str | None = None) -> bool:
    return LEVEL_CUES.get(level or config.get("fx_level") or "medium", 4) > 0


def secs(x: float) -> float:
    return float(f"{x:.3f}") if math.isfinite(x) else 0.0
