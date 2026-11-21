"""Clipping campaigns: the rules a campaign sets (tags, mentions, streamers, length, platforms), which accounts are
enrolled, and what the posts earned (views x rate per 1,000, capped by the budget)."""
from __future__ import annotations

import re
import time

from . import db


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def active() -> list[dict]:
    now = time.time()
    return [c for c in db.all("SELECT * FROM campaigns WHERE status='active' ORDER BY id")
            if not c.get("ends_at") or c["ends_at"] > now]


def for_post(clip: dict, account: dict) -> dict | None:
    """The campaign this clip counts toward on this account, if any."""
    for c in active():
        if account["id"] not in (c.get("accounts") or []):
            continue
        if account["platform"] not in (c.get("platforms") or ["youtube", "instagram"]):
            continue
        allowed = [_norm(s) for s in c.get("streamers") or [] if s]
        if allowed and _norm(clip.get("streamer")) not in allowed:
            continue
        return c
    return None


def matching(clip: dict) -> list[dict]:
    """Active campaigns that name this clip's streamer: its clips go to their accounts only."""
    name = _norm(clip.get("streamer"))
    return [c for c in active() if name and name in [_norm(s) for s in c.get("streamers") or []]]


def dedicated_ids() -> set[int]:
    """Accounts reserved for a campaign: they post that campaign's clips and nothing else."""
    return {aid for c in active() if c.get("dedicated", 1) for aid in c.get("accounts") or []}


def fits_length(c: dict, duration: float) -> bool:
    lo, hi = float(c.get("min_seconds") or 0), float(c.get("max_seconds") or 0)
    return (not lo or duration >= lo) and (not hi or duration <= hi)


def stats(c: dict) -> dict:
    posts = db.all("SELECT p.id, p.url, p.views, p.likes, p.comments, p.status, p.submit_status, a.name account, "
                   "a.platform FROM posts p JOIN accounts a ON a.id=p.account_id WHERE p.campaign_id=? AND p.status IN "
                   "('published','scheduled','queued','uploading')", (c["id"],))
    views = sum(p["views"] or 0 for p in posts)
    earned = views / 1000 * float(c.get("rate") or 0)
    if c.get("budget"):
        earned = min(earned, float(c["budget"]))
    return {"posts": len(posts), "published": sum(p["status"] == "published" for p in posts), "views": views,
            "likes": sum(p["likes"] or 0 for p in posts), "comments": sum(p["comments"] or 0 for p in posts),
            "earned": round(earned, 2), "links": [p["url"] for p in posts if p["url"]],
            "to_submit": sum(p["submit_status"] == "pending" for p in posts),
            "submitted": sum(p["submit_status"] in ("submitted", "tracked") for p in posts)}


def submissions(cid: int) -> list[dict]:
    return db.all("SELECT p.id, p.url, p.title, p.views, p.likes, p.status, p.submit_status, p.submitted_at, "
                  "p.scheduled_at, a.name account, a.platform, c.thumb FROM posts p JOIN accounts a ON a.id=p.account_id "
                  "JOIN clips c ON c.id=p.clip_id WHERE p.campaign_id=? AND p.status!='cancelled' "
                  "ORDER BY p.scheduled_at DESC", (cid,))
