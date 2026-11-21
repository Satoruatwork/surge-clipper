"""Fetch the meme sounds that carry Shorts edits from Pixabay (Pixabay Content License: free for commercial use,
no attribution). Each sound is trimmed to its hit, peak-levelled, and recorded with its source page.
Run: python -m surge.fetch_sounds   (writes <data>/sfx/*.wav + pack.json). The app runs it by itself on first start
when the pack is missing: the sounds are not shipped, each install downloads them under Pixabay's licence."""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

from . import tools
from .config import SFX as OUT

LICENSE = "Pixabay Content License (free for commercial use, no attribution required)"

# name, search query, words the result slug must contain, tags, cue, Shorts rank (10 = used everywhere), max seconds
TARGETS = [
    ("vine-boom", "vine boom", ["vine", "boom"], ["impact", "shock", "funny", "reveal"], "hit", 10, 2.5),
    ("bruh", "bruh", ["bruh"], ["fail", "funny", "awkward"], "hit", 9, 1.5),
    ("bonk", "bonk", ["bonk"], ["impact", "funny", "fail"], "hit", 8, 1.5),
    ("fahhh", "fahhh", ["fah"], ["fail", "funny", "shock"], "hit", 8, 2.5),
    ("huh-cat", "huh cat", ["huh"], ["shock", "funny", "confused"], "hit", 8, 2.0),
    ("metal-pipe", "metal pipe", ["metal", "pipe"], ["impact", "funny", "fail"], "hit", 8, 3.0),
    ("air-horn", "air horn", ["horn"], ["hype", "win", "funny"], "hit", 7, 2.5),
    ("record-scratch", "record scratch", ["scratch"], ["fail", "stop", "awkward", "funny"], "hit", 7, 1.5),
    ("emotional-damage", "emotional damage", ["emotional", "damage"], ["fail", "funny"], "hit", 7, 2.5),
    ("sad-violin", "sad violin", ["violin"], ["sad", "fail", "loss"], "tail", 7, 5.0),
    ("correct-ding", "correct answer", ["correct"], ["win", "correct", "idea"], "hit", 7, 1.5),
    ("cha-ching", "cha ching", ["ching"], ["money", "win"], "hit", 7, 2.0),
    ("rizz", "rizz", ["rizz"], ["funny", "romance", "flex"], "hit", 6, 2.0),
    ("anime-wow", "anime wow", ["wow"], ["shock", "win", "funny"], "hit", 6, 2.0),
    ("oh-no", "oh no", ["oh-no"], ["fail", "funny"], "lead", 6, 3.0),
    ("fart", "fart reverb", ["fart"], ["funny", "goofy"], "hit", 6, 2.5),
    ("wrong-buzzer", "wrong answer buzzer", ["wrong", "buzzer"], ["fail", "wrong"], "hit", 6, 1.5),
    ("dun-dun-dun", "dun dun dun", ["dun"], ["shock", "drama", "reveal"], "hit", 6, 3.5),
    ("police-siren", "police siren", ["siren"], ["police", "shock", "chase"], "hit", 6, 3.0),
    ("punch", "punch", ["punch"], ["impact", "fight", "fail"], "hit", 6, 1.0),
    ("boing", "boing", ["boing"], ["funny", "goofy"], "hit", 6, 1.5),
    ("laugh-track", "sitcom laugh", ["laugh"], ["funny"], "tail", 6, 4.0),
    ("whoosh", "whoosh", ["whoosh"], ["transition", "fast"], "lead", 6, 1.5),
    ("dial-up", "dial up", ["dial"], ["funny", "awkward", "loading"], "lead", 5, 3.0),
    ("crickets", "crickets", ["cricket"], ["awkward", "silence", "fail"], "tail", 5, 3.5),
    ("camera-shutter", "camera shutter", ["shutter"], ["highlight", "freeze"], "hit", 5, 1.0),
    ("suspense", "suspense", ["suspense"], ["suspense", "buildup", "tension"], "lead", 5, 4.0),
    ("riser", "riser", ["riser"], ["buildup", "suspense"], "lead", 5, 3.5),
    ("explosion", "explosion", ["explosion"], ["impact", "hype", "shock"], "hit", 5, 3.0),
    ("applause", "applause", ["applause"], ["win", "hype"], "tail", 4, 4.0),
    ("heartbeat", "heartbeat", ["heartbeat"], ["tension", "suspense"], "lead", 4, 3.0),
    ("glass-break", "glass break", ["glass"], ["impact", "fail"], "hit", 4, 2.0),
]


def session():
    from curl_cffi import requests as creq
    return creq.Session(impersonate="chrome")


def find(s, query: str, must: list[str]) -> tuple[str, str] | None:
    """(page url, mp3 url) of the best result for this sound."""
    r = s.get(f"https://pixabay.com/sound-effects/search/{query.replace(' ', '%20')}/", timeout=30)
    if r.status_code != 200:
        return None
    pages = list(dict.fromkeys(re.findall(r"/sound-effects/([a-z0-9\-]+-\d+)/", r.text)))
    pages = [p for p in pages if not p.startswith("search")]
    good = [p for p in pages if all(m in p for m in must)] or [p for p in pages if any(m in p for m in must)]
    for slug in good[:3]:
        page = f"https://pixabay.com/sound-effects/{slug}/"
        pr = s.get(page, timeout=30)
        mp3 = re.findall(r"https://cdn\.pixabay\.com/download/audio/[^\"\s<>]+?\.mp3", pr.text)
        if mp3:
            return page, mp3[0]
        time.sleep(0.5)
    return None


def finish(src: Path, dest: Path, max_len: float) -> float:
    """Trim leading silence, cap the length, fade the tail, level to -1 dBFS peak, 48 kHz stereo WAV."""
    tools.run([tools.ffmpeg(), "-v", "error", "-y", "-i", str(src),
               "-af", f"silenceremove=start_periods=1:start_threshold=-45dB,atrim=0:{max_len},"
                      f"afade=t=out:st={max(0.05, max_len - 0.25)}:d=0.25,aresample=48000,"
                      "aformat=channel_layouts=stereo,dynaudnorm=p=0.89:m=12",
               "-ar", "48000", "-ac", "2", str(dest)], timeout=60)
    return tools.probe(dest)["duration"]


def build() -> list[dict]:
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = OUT / "_dl"
    tmp.mkdir(exist_ok=True)
    s = session()
    meta = []
    for name, query, must, tags, cue, rank, max_len in TARGETS:
        try:
            got = find(s, query, must)
            if not got:
                print(f"miss  {name}", flush=True)
                continue
            page, mp3 = got
            raw = tmp / f"{name}.mp3"
            r = s.get(mp3, timeout=60, headers={"Referer": page})
            if r.status_code != 200 or len(r.content) < 2000:
                print(f"fail  {name} ({r.status_code})", flush=True)
                continue
            raw.write_bytes(r.content)
            dur = finish(raw, OUT / f"{name}.wav", max_len)
            meta.append({"name": name, "file": f"{name}.wav", "tags": tags, "cue": cue, "rank": rank,
                         "gain": -3 if cue == "hit" else -6, "duration": round(dur, 2),
                         "source": page, "license": LICENSE})
            print(f"ok    {name:18s} {dur:4.1f}s  {page}", flush=True)
        except Exception as e:  # keep going: one bad page shouldn't stop the pack
            print(f"error {name}: {e}", flush=True)
        time.sleep(0.6)
    (OUT / "pack.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    for f in tmp.glob("*"):
        f.unlink()
    tmp.rmdir()
    return meta


def ensure() -> None:
    """First start: fetch the pack in the background when it is missing or empty. Never raises."""
    from . import bus
    pack = OUT / "pack.json"
    try:
        if pack.exists() and json.loads(pack.read_text(encoding="utf-8")):
            return
    except (OSError, ValueError):
        pass
    bus.note("Downloading the meme sound pack from Pixabay (first start, about a minute).")
    try:
        got = build()
    except Exception as e:
        bus.note(f"Sound pack download failed: {e}", "warn")
        return
    if got:
        bus.note(f"Sound pack ready: {len(got)} sounds.")
    else:
        bus.note("Sound pack download failed. Check your connection and restart Surge to retry.", "warn")


if __name__ == "__main__":
    got = build()
    print(f"{len(got)}/{len(TARGETS)} sounds")
    sys.exit(0 if got else 1)
