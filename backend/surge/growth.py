"""Account management (groups, warm-up, health checks), daily snapshots, the performance read-out, and storage."""
from __future__ import annotations

import shutil
import time
from collections import defaultdict
from datetime import date
from pathlib import Path

from . import db, instagram, youtube
from .config import CLIPS, DATA, RUNS

WEEK = 7 * 86400


def effective_cap(account: dict) -> int:
    """Posts a day this account can really make. Warm-up: a new account posts 1 a day in week one, 2 in week two, ...
    up to its own cap. day_limit: what YouTube turned out to allow this channel (learned from its refusals)."""
    cap = int(account.get("daily_cap") or 4)
    if account.get("warmup"):
        started = float(account.get("warmup_started") or account.get("created_at") or time.time())
        cap = min(cap, 1 + int((time.time() - started) // WEEK))
    if account.get("day_limit"):
        cap = min(cap, int(account["day_limit"]))
    return max(1, cap)


def check(account: dict) -> dict:
    """Ping the platform with the stored token; marks the account ok or error."""
    try:
        if account["platform"] == "youtube":
            youtube.refresh_channel(account)
        else:
            tok = instagram.token_of(account)
            if tok.get("graph") == "instagram" and tok.get("expires_at") and tok["expires_at"] < time.time() + 10 * 86400:
                instagram.refresh(account)
            instagram.refresh_profile(account)
        db.update("accounts", account["id"], {"status": "ok", "error": None, "last_check": time.time(),
                                              "last_ok": time.time()})
        return {"ok": True}
    except Exception as e:
        msg = str(e)[:300]
        if "invalid_grant" in msg or "expired" in msg.lower() or "revoked" in msg.lower():
            msg = "Sign-in expired or was revoked. Reconnect this account."
        db.update("accounts", account["id"], {"status": "error", "error": msg, "last_check": time.time()})
        return {"ok": False, "error": msg}


def apply_group(group_id: int) -> int:
    g = db.get("groups", group_id)
    if not g:
        return 0
    members = db.all("SELECT id FROM accounts WHERE group_id=?", (group_id,))
    for m in members:
        db.update("accounts", m["id"], {"tags": g.get("tags") or [], "windows": g.get("windows") or [],
                                        "daily_cap": g.get("daily_cap") or 4})
    return len(members)


# ---------------------------------------------------------------- snapshots + performance

def snapshot(kind: str, ref_id: int, views: int = 0, followers: int = 0, likes: int = 0) -> None:
    now = time.time()
    db.execute("INSERT INTO snapshots (kind, ref_id, day, views, followers, likes, created_at, updated_at) "
               "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(kind, ref_id, day) DO UPDATE SET views=excluded.views, "
               "followers=excluded.followers, likes=excluded.likes, updated_at=excluded.updated_at",
               (kind, ref_id, date.today().isoformat(), int(views), int(followers), int(likes), now, now))


def insights(days: int = 14) -> dict:
    posts = db.all("SELECT p.id, p.clip_id, p.account_id, p.views, p.likes, p.status, p.url, p.scheduled_at, "
                   "p.campaign_id, a.name account, a.platform, c.streamer, c.layout, c.chat_class, c.fx_json, "
                   "c.title, c.thumb, c.score FROM posts p JOIN accounts a ON a.id=p.account_id "
                   "JOIN clips c ON c.id=p.clip_id WHERE p.status IN ('published','scheduled')")
    total_views = sum(p["views"] or 0 for p in posts)

    def group(key) -> list[dict]:
        acc: dict = defaultdict(lambda: {"posts": 0, "views": 0})
        for p in posts:
            k = key(p)
            if k is None:
                continue
            for kk in (k if isinstance(k, list) else [k]):
                acc[kk]["posts"] += 1
                acc[kk]["views"] += p["views"] or 0
        rows = [{"name": k, **v, "avg": round(v["views"] / max(1, v["posts"]))} for k, v in acc.items()]
        return sorted(rows, key=lambda r: -r["avg"])[:12]

    def sounds_of(p):
        fx = p.get("fx") or {}
        names = fx.get("used_sounds") or []
        return names or ["no meme sound"]

    # daily views: day-over-day growth of the post snapshots
    snaps = db.all("SELECT day, SUM(views) v FROM snapshots WHERE kind='post' GROUP BY day ORDER BY day DESC LIMIT ?",
                   (days + 1,))
    snaps = list(reversed(snaps))
    daily = [{"day": snaps[i]["day"], "views": max(0, (snaps[i]["v"] or 0) - (snaps[i - 1]["v"] or 0))}
             for i in range(1, len(snaps))]
    top = sorted(posts, key=lambda p: -(p["views"] or 0))[:10]
    return {
        "total_views": total_views, "posts": len(posts),
        "daily": daily,
        "top": [{"title": p["title"], "account": p["account"], "platform": p["platform"], "views": p["views"],
                 "url": p["url"], "thumb": p["thumb"]} for p in top],
        "streamers": group(lambda p: p["streamer"]),
        "accounts": group(lambda p: p["account"]),
        "reactions": group(lambda p: p["chat_class"] or "unknown"),
        "layouts": group(lambda p: p["layout"]),
        "sounds": group(sounds_of),
    }


# ---------------------------------------------------------------- storage

def _size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) if path.exists() else 0


def storage() -> dict:
    raw = sum(f.stat().st_size for f in CLIPS.glob("*/raw.*"))
    final = sum(f.stat().st_size for f in CLIPS.glob("*/clip*.mp4"))
    return {"raw_gb": round(raw / 1e9, 2), "clips_gb": round(final / 1e9, 2), "runs_gb": round(_size(RUNS) / 1e9, 2),
            "total_gb": round(_size(DATA) / 1e9, 2), "free_gb": round(shutil.disk_usage(DATA).free / 1e9, 1)}


def cleanup(raw_days: int = 3) -> dict:
    """Delete source footage of clips older than raw_days that are done (posted, approved or rejected), plus
    leftover recordings of streams that aren't running. Finished clips are kept."""
    from . import live
    cutoff = time.time() - raw_days * 86400
    freed = 0
    for c in db.all("SELECT id, raw_path FROM clips WHERE created_at<? AND status IN ('approved','rejected','failed') "
                    "AND raw_path IS NOT NULL", (cutoff,)):
        p = Path(c["raw_path"])
        if p.exists():
            freed += p.stat().st_size
            p.unlink(missing_ok=True)
    running = set(live.running())
    for d in RUNS.glob("src*"):
        try:
            sid = int(d.name[3:])
        except ValueError:
            continue
        if sid not in running:
            for sub in d.glob("e*"):
                freed += _size(sub)
                shutil.rmtree(sub, ignore_errors=True)
    return {"freed_gb": round(freed / 1e9, 2)}
