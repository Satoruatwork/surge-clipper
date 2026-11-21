"""Paths and user settings. Settings live in data/settings.json; secret fields are DPAPI-encrypted at rest."""
from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path

from . import vault

FROZEN = bool(getattr(sys, "frozen", False))
if FROZEN:
    # Installed build (PyInstaller): assets and the UI ship inside the bundle; user data lives in a writable folder.
    ROOT = Path(getattr(sys, "_MEIPASS", None) or Path(sys.executable).parent)
    _DEFAULT_DATA = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "Surge"
else:
    ROOT = Path(__file__).resolve().parents[2]
    _DEFAULT_DATA = ROOT / "data"
DATA = Path(os.environ.get("SURGE_DATA", _DEFAULT_DATA))
CLIPS = DATA / "clips"
RUNS = DATA / "runs"
MODELS = DATA / "models"
TOKENS = DATA / "tokens"
SFX = DATA / "sfx"  # meme-sound pack, downloaded on first run (fetch_sounds); never shipped or committed
ASSETS = ROOT / "assets"
FONTS = ASSETS / "fonts"
UI_DIST = ROOT / "ui" / "dist"

for _p in (DATA, CLIPS, RUNS, MODELS, TOKENS, FONTS):
    _p.mkdir(parents=True, exist_ok=True)

SECRET_KEYS = {"anthropic_api_key", "meta_app_secret"}

DEFAULTS: dict = {
    # transcription
    "whisper_live_model": "base.en",
    "whisper_final_model": "large-v3-turbo",
    "language": "en",
    "hotwords": "GTA, GTA 6, Leonida, Vice City, Lucia, Jason, cops, wanted, heist, NPC",
    # engine
    "render_workers": 3,
    "encoder": "auto",
    "fps": 30,
    # clip rules
    "clip_min": 30,
    "clip_max": 50,
    "pre_roll": 22,
    "post_roll": 14,
    "chat_lag": 5.0,
    "sensitivity": 2.4,
    "cooldown": 40,
    "target_per_hour": 45,
    "vod_target": 25,
    # look
    "layout": "auto",
    "caption_style": "punch",
    "caption_font": "Montserrat-Black",
    "caption_words": 3,
    "hook_seconds": 3.0,
    "profanity": "mask",
    "safe_zone": "all",
    "credit_template": "🎮 {streamer} on {platform}",
    # edit layer
    "fx_level": "medium",
    "music_mode": "fits",
    "music_volume": -22,
    "sfx_volume": -4,
    "sfx_ratio": 70,
    "stickers": True,
    # AI
    "anthropic_api_key": "",
    "llm_model": "claude-haiku-5-5",
    "llm_rank_model": "claude-sonnet-5-5",
    # platforms
    "google_client_secret": "",
    "meta_app_id": "",
    "meta_app_secret": "",
    "meta_redirect_uri": "https://localhost:8765/ig-callback",
    "ig_graph_version": "v21.0",
    # distribution
    "autopilot": False,
    "auto_approve_score": 8.0,
    "max_accounts_per_clip": 3,
    "min_gap_minutes": 75,
    "slot_jitter_minutes": 8,
    "campaign_hashtags": "",
    "campaign_mentions": "",
    "banned_words": "",
    # accounts + automation
    "yt_daily_uploads": 100,
    "max_live": 3,
    "watch_category": "Grand Theft Auto VI",
}

_lock = threading.Lock()
_FILE = DATA / "settings.json"
_cache: dict | None = None


def load() -> dict:
    global _cache
    with _lock:
        if _cache is None:
            raw = {}
            if _FILE.exists():
                try:
                    raw = json.loads(_FILE.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    raw = {}
            for k in SECRET_KEYS:
                if isinstance(raw.get(k), str) and raw[k].startswith("dpapi:"):
                    raw[k] = vault.unseal(raw[k])
            _cache = {**DEFAULTS, **raw}
        return dict(_cache)


def get(key: str):
    return load().get(key, DEFAULTS.get(key))


def save(update: dict) -> dict:
    global _cache
    cur = load()
    for k, v in update.items():
        if k not in DEFAULTS:
            continue
        if k in SECRET_KEYS and isinstance(v, str) and set(v) == {"•"}:
            continue  # masked value echoed back from the UI: keep the stored secret
        cur[k] = v
    disk = dict(cur)
    for k in SECRET_KEYS:
        if disk.get(k):
            disk[k] = vault.seal(disk[k])
    with _lock:
        _FILE.write_text(json.dumps(disk, indent=2), encoding="utf-8")
        _cache = cur
    return public()


def public() -> dict:
    s = load()
    for k in SECRET_KEYS:
        if s.get(k):
            s[k] = "•" * 12
    return s
