"""Sound library: the meme sounds Shorts edits run on (fetched from Pixabay under its free licence, see
fetch_sounds.py), CC-BY music, and the user's own sounds and songs. User files are auto-tagged from their names
(vine boom, bruh, airhorn, phonk, ...). Picks rotate so no sound repeats clip after clip."""
from __future__ import annotations

import json
import re
import threading
import uuid
from collections import deque
from pathlib import Path

import numpy as np

from . import tools
from .config import ASSETS, DATA, SFX

SFX_DIR = SFX
MUSIC_DIR = ASSETS / "music"
STICKER_DIR = ASSETS / "stickers"
USER_DIR = DATA / "library"
INDEX = USER_DIR / "library.json"
CATALOG = ASSETS / "music_catalog.json"
AUDIO_EXT = {".mp3", ".wav", ".ogg", ".m4a", ".aac", ".flac", ".opus", ".webm"}
_lock = threading.Lock()
_recent: deque = deque(maxlen=10)   # last sounds used, newest last: rotation keeps edits from sounding samey

# famous meme sounds and what they mean, matched against file names
SOUND_WORDS: list[tuple[str, list[str], str]] = [
    (r"vine.?boom|bass.?boost", ["impact", "shock", "funny", "reveal"], "hit"),
    (r"bruh", ["fail", "funny", "awkward"], "hit"),
    (r"\boof\b|roblox", ["fail", "funny"], "hit"),
    (r"air.?horn|mlg", ["hype", "win", "funny"], "hit"),
    (r"sad.?violin|violin", ["sad", "fail"], "tail"),
    (r"scratch", ["fail", "stop", "awkward", "funny"], "hit"),
    (r"metal.?pipe|\bpipe\b", ["impact", "funny", "fail"], "hit"),
    (r"emotional.?damage", ["fail", "funny"], "hit"),
    (r"oh.?no", ["fail", "funny"], "lead"),
    (r"\bwow\b|anime.?wow", ["shock", "win", "funny"], "hit"),
    (r"to.?be.?continued|roundabout", ["fail", "ending"], "tail"),
    (r"dun.?dun|dramatic|suspense", ["shock", "drama", "reveal"], "hit"),
    (r"fart", ["funny", "goofy"], "hit"),
    (r"slap|punch|\bhit\b|bonk", ["impact", "fight", "funny"], "hit"),
    (r"wilhelm|scream|yell", ["fail", "shock"], "hit"),
    (r"cricket", ["awkward", "silence", "fail"], "tail"),
    (r"laugh|giggle|haha|hehe", ["funny"], "tail"),
    (r"applause|clap|cheer", ["win", "hype"], "tail"),
    (r"\bboo\b|booing", ["fail"], "tail"),
    (r"\bding\b|bell|correct", ["win", "idea"], "hit"),
    (r"error|windows|wrong|buzzer", ["fail", "wrong"], "hit"),
    (r"discord|notif|\bping\b", ["funny", "attention"], "hit"),
    (r"\bfbi\b|open.?up|siren|police|cop", ["shock", "police"], "hit"),
    (r"nani|omae", ["shock", "funny"], "hit"),
    (r"rizz|sigma|phonk", ["hype"], "hit"),
    (r"goofy|cartoon|boing|spring", ["funny", "goofy"], "hit"),
    (r"whoosh|swoosh|swish|woosh", ["transition", "fast"], "lead"),
    (r"riser|\brise\b|build.?up", ["suspense", "buildup"], "lead"),
    (r"camera|shutter|flash", ["highlight", "freeze"], "hit"),
    (r"heart.?beat", ["tension", "suspense"], "lead"),
    (r"explo|kaboom|\bboom\b", ["impact", "hype", "shock"], "hit"),
    (r"among.?us|\bsus\b", ["shock", "funny"], "hit"),
    (r"taco.?bell|\bbong\b", ["impact", "funny"], "hit"),
    (r"censor|bleep|\bbeep\b", ["funny"], "hit"),
    (r"drum.?roll", ["buildup", "reveal"], "lead"),
    (r"hello.?there|hello", ["funny"], "hit"),
    (r"gasp|what\b", ["shock"], "hit"),
]

SONG_WORDS: list[tuple[str, list[str]]] = [
    (r"phonk|metamorphosis|murder.?in.?my.?mind|close.?eyes|sahara|\brave\b|live.?another|aura|slay|kordhell|interworld",
     ["hype", "phonk"]),
    (r"running.?in.?the.?90|deja.?vu|gas.?gas|eurobeat|initial.?d", ["chase", "funny"]),
    (r"yakety|benny.?hill|grass.?skirt", ["chase", "funny"]),
    (r"roundabout|to.?be.?continued", ["fail", "ending"]),
    (r"curb|frolic", ["awkward", "fail", "ending"]),
    (r"mission.?impossible|pink.?panther|sneak", ["sneaky", "tension"]),
    (r"sad|another.?love|violin|piano", ["sad"]),
    (r"astronomia|coffin", ["fail", "funny"]),
    (r"megalovania", ["hype", "chaos"]),
    (r"careless.?whisper|\bsax\b", ["funny", "romance"]),
    (r"wii|shop|elevator|lofi|chill", ["chill", "funny"]),
    (r"after.?dark|little.?dark|gangsta|sweater", ["dark"]),
]


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def auto_sound_tags(name: str) -> tuple[list[str], str]:
    low = _norm(name)
    tags: list[str] = []
    cue = "hit"
    for pat, t, c in SOUND_WORDS:
        if re.search(pat, low):
            tags += [x for x in t if x not in tags]
            cue = c if cue == "hit" else cue
    return (tags or ["funny"]), cue


def catalog() -> list[dict]:
    try:
        return json.loads(CATALOG.read_text(encoding="utf-8"))["songs"]
    except Exception:
        return []


def auto_song_meta(name: str) -> dict:
    low = _norm(name)
    for song in catalog():
        if _norm(song["title"]) and _norm(song["title"]) in low:
            return {"title": song["title"], "artist": song["artist"], "moods": song["moods"], "catalog": True}
    moods: list[str] = []
    for pat, m in SONG_WORDS:
        if re.search(pat, low):
            moods += [x for x in m if x not in moods]
    return {"title": Path(name).stem, "artist": "", "moods": moods or ["hype"], "catalog": False}


def _load_index() -> dict:
    if INDEX.exists():
        try:
            return json.loads(INDEX.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"items": {}}


def _save_index(idx: dict) -> None:
    USER_DIR.mkdir(parents=True, exist_ok=True)
    INDEX.write_text(json.dumps(idx, indent=2, ensure_ascii=False), encoding="utf-8")


def find_drop(path: Path, limit: float = 75.0) -> float:
    """Where the beat drops: the biggest jump in loudness against the previous few seconds."""
    try:
        p = tools.run([tools.ffmpeg(), "-v", "error", "-t", str(limit), "-i", str(path), "-ac", "1", "-ar", "8000",
                       "-f", "s16le", "-"], timeout=120)
    except Exception:
        return 0.0
    x = np.frombuffer(p.stdout, np.int16).astype(np.float32) / 32768
    hop = 800  # 0.1 s
    n = len(x) // hop
    if n < 40:
        return 0.0
    rms = np.sqrt(np.mean(x[: n * hop].reshape(n, hop) ** 2, axis=1) + 1e-9)
    db = 20 * np.log10(rms)
    best, at = 0.0, 0.0
    for i in range(30, n - 10):
        jump = np.mean(db[i:i + 10]) - np.mean(db[i - 30:i])
        if jump > best:
            best, at = jump, i / 10
    return round(at, 1) if best > 4 else 0.0


def _builtin() -> list[dict]:
    out = []
    pack = SFX_DIR / "pack.json"
    if pack.exists():
        for m in json.loads(pack.read_text(encoding="utf-8")):
            if not (SFX_DIR / m["file"]).exists():
                continue
            out.append({"id": f"b:sfx:{m['name']}", "kind": "sound", "name": m["name"], "file": str(SFX_DIR / m["file"]),
                        "tags": m["tags"], "gain": m["gain"], "cue": m["cue"], "duration": m["duration"],
                        "rank": m.get("rank", 5), "source": m.get("source"), "license": m.get("license"),
                        "builtin": True, "credit": None})
    meta = MUSIC_DIR / "music.json"
    if meta.exists():
        cache = _load_index().setdefault("drops", {})
        for t in json.loads(meta.read_text(encoding="utf-8"))["tracks"]:
            f = MUSIC_DIR / t["file"]
            if not f.exists():
                continue
            out.append({"id": f"b:music:{t['title']}", "kind": "song", "name": t["title"], "artist": t["artist"],
                        "file": str(f), "moods": t["moods"], "gain": 0, "drop": cache.get(t["file"]),
                        "builtin": True,
                        "credit": f"Music: \"{t['title']}\" by {t['artist']} (incompetech.com), "
                                  f"licensed under CC BY 4.0"})
    return out


def items(kind: str | None = None) -> list[dict]:
    idx = _load_index()
    user = [dict(v, id=k) for k, v in idx["items"].items() if Path(v["file"]).exists()]
    all_items = _builtin() + user
    return [i for i in all_items if kind is None or i["kind"] == kind]


def get(item_id: str) -> dict | None:
    return next((i for i in items() if i["id"] == item_id), None)


def add_file(src_name: str, data: bytes, kind: str) -> dict:
    ext = Path(src_name).suffix.lower()
    if ext not in AUDIO_EXT:
        raise ValueError(f"{src_name}: not an audio file")
    folder = USER_DIR / ("sounds" if kind == "sound" else "music")
    folder.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^\w\-. ]+", "_", Path(src_name).stem)[:80]
    dest = folder / f"{safe}{ext}"
    i = 2
    while dest.exists():
        dest = folder / f"{safe} ({i}){ext}"
        i += 1
    dest.write_bytes(data)
    try:
        dur = tools.probe(dest)["duration"]
    except Exception:
        dest.unlink(missing_ok=True)
        raise ValueError(f"{src_name}: couldn't read this audio file")
    if kind == "sound":
        tags, cue = auto_sound_tags(src_name)
        item = {"kind": "sound", "name": safe, "file": str(dest), "tags": tags, "cue": cue, "gain": -4,
                "duration": round(dur, 2), "builtin": False, "credit": None}
    else:
        meta = auto_song_meta(src_name)
        item = {"kind": "song", "name": meta["title"], "artist": meta["artist"], "file": str(dest),
                "moods": meta["moods"], "gain": 0, "drop": find_drop(dest), "duration": round(dur, 1),
                "builtin": False, "credit": None, "catalog": meta["catalog"]}
    with _lock:
        idx = _load_index()
        iid = f"u:{uuid.uuid4().hex[:10]}"
        idx["items"][iid] = item
        _save_index(idx)
    return dict(item, id=iid)


def update(item_id: str, patch: dict) -> dict:
    with _lock:
        idx = _load_index()
        if item_id not in idx["items"]:
            raise KeyError(item_id)
        allowed = {k: v for k, v in patch.items() if k in ("name", "tags", "moods", "gain", "drop", "cue", "artist",
                                                            "credit")}
        idx["items"][item_id].update(allowed)
        _save_index(idx)
        return dict(idx["items"][item_id], id=item_id)


def remove(item_id: str) -> None:
    with _lock:
        idx = _load_index()
        item = idx["items"].pop(item_id, None)
        _save_index(idx)
    if item:
        Path(item["file"]).unlink(missing_ok=True)


def warm_drops() -> None:
    """Measure the drop of built-in songs once (cached)."""
    meta = MUSIC_DIR / "music.json"
    if not meta.exists():
        return
    with _lock:
        idx = _load_index()
        drops = idx.setdefault("drops", {})
        changed = False
        for t in json.loads(meta.read_text(encoding="utf-8"))["tracks"]:
            f = MUSIC_DIR / t["file"]
            if f.exists() and t["file"] not in drops:
                drops[t["file"]] = find_drop(f)
                changed = True
        if changed:
            _save_index(idx)


def stickers() -> dict[str, str]:
    return {p.stem: str(p) for p in sorted(STICKER_DIR.glob("*.png"))}


def pick_sound(tags: list[str], salt: int, exclude: set[str] | None = None) -> dict | None:
    """Sound for these tags, picked in proportion to fit and to how well it works on Shorts (rank), with your
    own files ahead of the pack. Rotation: never the same sound twice in a row, and each recent use lowers its
    odds, so the top sounds come up most often without every clip sounding the same."""
    import math
    import random
    exclude = exclude or set()
    recent = list(_recent)
    cands: list[tuple[float, dict]] = []
    for s in items("sound"):
        if s["id"] in exclude or s["name"] in exclude:
            continue
        overlap = len(set(tags) & set(s.get("tags") or []))
        if not overlap:
            continue
        rank = float(s.get("rank", 6 if not s.get("builtin") else 5))
        score = overlap * 1.5 + rank * 0.45 + (1.5 if not s.get("builtin") else 0) - 1.0 * recent.count(s["id"])
        cands.append((score, s))
    if not cands:
        return None
    if len(cands) > 1 and recent:
        cands = [c for c in cands if c[1]["id"] != recent[-1]] or cands
    top = max(c[0] for c in cands)
    rng = random.Random(salt * 7919 + len(recent))
    pick = rng.choices([c[1] for c in cands], weights=[math.exp(1.3 * (c[0] - top)) for c in cands])[0]
    _recent.append(pick["id"])
    return pick


def pick_song(mood: str, salt: int) -> dict | None:
    songs = [s for s in items("song") if mood in (s.get("moods") or [])]
    if not songs:
        return None
    songs.sort(key=lambda s: (0 if not s.get("builtin") else 1, hash((s["id"], salt)) % 1000))
    return songs[0]
