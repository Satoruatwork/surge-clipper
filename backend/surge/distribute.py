"""Autopilot distribution: assign approved clips to accounts, give each account its own copy angle, slot posts
into each account's posting windows, upload/publish on time, refresh tokens and view counts."""
from __future__ import annotations

import random
import re
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from . import bus, campaigns, config, db, growth, instagram, pipeline, youtube

_stop = threading.Event()
_uploads = ThreadPoolExecutor(max_workers=3, thread_name_prefix="upload")
_busy: set[int] = set()
_busy_lock = threading.Lock()
YT_MIN_LEAD = 15 * 60


# ---------------------------------------------------------------- copy

def credit(clip: dict) -> str:
    name = clip.get("streamer") or ""
    link = pipeline.tag_for(clip)
    return f"🎮 {name} — {link}" if name else ""


def _tags(clip: dict, variant_index: int) -> list[str]:
    tags = list(clip.get("hashtags") or [])
    if not tags:
        return []
    fixed = tags[:4]  # keep the campaign + core tags, shuffle the long tail per account
    tail = tags[4:]
    random.Random(variant_index * 7919 + clip["id"]).shuffle(tail)
    return fixed + tail[:8]


def compose(clip: dict, account: dict, variant_index: int, campaign: dict | None = None) -> dict:
    variants = [{"title": clip.get("title"), "hook": clip.get("hook"), "description": clip.get("description")}]
    variants += [v for v in (clip.get("variants") or []) if v.get("title")]
    v = variants[variant_index % len(variants)]
    title = (v.get("title") or clip.get("title") or "").strip()
    desc = (v.get("description") or clip.get("description") or "").strip()
    tags = _tags(clip, variant_index)
    raw_mentions = (config.get("campaign_mentions") or "").replace(",", " ").split()
    if campaign and campaign.get("code"):  # tracking code the campaign uses to find your posts
        desc = f"{desc}\n{campaign['code']}".strip()
    if campaign:  # the campaign's own required tags and mentions come first
        req = ["#" + t.lstrip("#") for t in campaign.get("hashtags") or [] if t]
        tags = req + [t for t in tags if t.lower() not in {r.lower() for r in req}]
        raw_mentions = list(campaign.get("mentions") or []) + raw_mentions
    mentions = " ".join(dict.fromkeys(m if m.startswith("@") else "@" + m for m in raw_mentions if m))
    music_credit = (clip.get("fx") or {}).get("credit")
    if music_credit:
        desc = f"{desc}\n\n{music_credit}".strip()
    if account["platform"] == "youtube":
        if "#shorts" not in (title + " " + " ".join(tags)).lower():
            tags = tags + ["#shorts"]
        body = "\n\n".join(x for x in [desc, credit(clip), mentions, " ".join(tags)] if x)
    else:
        body = "\n\n".join(x for x in [title, desc, credit(clip), mentions, ".\n" + " ".join(tags[:20])] if x)
    return {"title": title[:100], "description": body, "hashtags": tags}


def banned(text: str) -> str | None:
    for w in [x.strip().lower() for x in (config.get("banned_words") or "").split(",") if x.strip()]:
        if re.search(rf"\b{re.escape(w)}\b", text.lower()):
            return w
    return None


# ---------------------------------------------------------------- slots

def _day_posts(account_id: int, day_start: float) -> list[float]:
    rows = db.all("SELECT scheduled_at FROM posts WHERE account_id=? AND status NOT IN ('failed','cancelled') "
                  "AND scheduled_at>=? AND scheduled_at<?", (account_id, day_start - 86400, day_start + 2 * 86400))
    return [r["scheduled_at"] for r in rows]


def next_slot(account: dict, after: float) -> float:
    windows = account.get("windows") or ["10:00", "13:00", "16:00", "19:00", "22:00"]
    cap = growth.effective_cap(account)
    gap = float(config.get("min_gap_minutes") or 60) * 60
    jitter = float(config.get("slot_jitter_minutes") or 0) * 60
    rnd = random.Random(int(after) ^ account["id"])
    base = datetime.fromtimestamp(after).replace(hour=0, minute=0, second=0, microsecond=0)
    for day in range(0, 30):
        d0 = base + timedelta(days=day)
        taken = _day_posts(account["id"], d0.timestamp())
        on_day = [t for t in taken if d0.timestamp() <= t < d0.timestamp() + 86400]
        if len(on_day) >= cap:
            continue
        for w in sorted(windows):
            try:
                hh, mm = (int(x) for x in w.split(":"))
            except ValueError:
                continue
            t = (d0 + timedelta(hours=hh, minutes=mm)).timestamp() + rnd.uniform(-jitter, jitter)
            if t < after:
                continue
            if all(abs(t - o) >= gap for o in taken):
                return t
    return after + 86400


# ---------------------------------------------------------------- assignment

def _eligible(clip: dict) -> list[dict]:
    accounts = db.all("SELECT * FROM accounts WHERE active=1 AND status!='error'")
    keys = {(clip.get("streamer") or "").lower(), (clip.get("game") or "").lower(), "gta6", "gta 6"}
    out = []
    for a in accounts:
        tags = [t.lower().lstrip("#") for t in (a.get("tags") or [])]
        if not tags or "*" in tags or any(t in k or k in t for t in tags for k in keys if k):
            out.append(a)
    return out


def assign(clip_id: int, account_ids: list[int] | None = None, when: float | None = None) -> list[int]:
    clip = db.get("clips", clip_id)
    if not clip or not clip.get("path"):
        raise ValueError("clip has no rendered video yet")
    already = {r["account_id"] for r in db.all("SELECT account_id FROM posts WHERE clip_id=? AND status!='cancelled'",
                                               (clip_id,))}
    routed = campaigns.matching(clip)
    if account_ids:
        accounts = [a for a in (db.get("accounts", i) for i in account_ids) if a]
    else:
        if routed:  # a campaign streamer: only that campaign's accounts get the clip
            ids = {aid for c in routed for aid in c.get("accounts") or []}
            pool = [a for a in db.all("SELECT * FROM accounts WHERE active=1 AND status!='error'")
                    if a["id"] in ids and a["id"] not in already]
        else:  # everything else stays off accounts reserved for a campaign
            reserved = campaigns.dedicated_ids()
            pool = [a for a in _eligible(clip) if a["id"] not in already and a["id"] not in reserved]
        load = {a["id"]: db.scalar("SELECT COUNT(*) FROM posts WHERE account_id=? AND status IN "
                                   "('queued','scheduled','uploading') ", (a["id"],)) for a in pool}
        pool.sort(key=lambda a: (load[a["id"]], random.random()))
        k = int(config.get("max_accounts_per_clip") or 2)
        yt = [a for a in pool if a["platform"] == "youtube"]
        ig = [a for a in pool if a["platform"] == "instagram"]
        accounts = []
        while len(accounts) < k and (yt or ig):  # alternate platforms so a clip spreads across both
            for lst in (yt, ig):
                if lst and len(accounts) < k:
                    accounts.append(lst.pop(0))
    created = []
    after = max(time.time() + 10 * 60, when or 0)
    for i, a in enumerate(accounts):
        if a["id"] in already:
            continue
        variant = len(already) + i
        camp = campaigns.for_post(clip, a)
        if camp and not campaigns.fits_length(camp, float(clip.get("duration") or 0)):
            bus.note(f"Clip #{clip_id} skipped for {a['name']}: {clip.get('duration')}s is outside "
                     f"{camp['name']}'s length rule", "warn")
            continue
        copy = compose(clip, a, variant, camp)
        bad = banned(copy["title"] + " " + copy["description"] + " " + (clip.get("transcript") or ""))
        if bad:
            bus.note(f"Clip #{clip_id} skipped for {a['name']}: contains banned word '{bad}'", "warn")
            continue
        at = when if (when and account_ids) else next_slot(a, after)
        pid = db.insert("posts", {"clip_id": clip_id, "account_id": a["id"], "scheduled_at": at, "status": "queued",
                                  "title": copy["title"], "description": copy["description"],
                                  "hashtags": copy["hashtags"], "campaign_id": camp["id"] if camp else None})
        created.append(pid)
        bus.publish("post", {"id": pid, "status": "queued", "clip_id": clip_id, "account_id": a["id"],
                             "scheduled_at": at})
    if created:
        db.update("clips", clip_id, {"status": "approved"})
        bus.publish("clip", {"id": clip_id, "status": "approved"})
        bus.note(f"Clip #{clip_id} scheduled on {len(created)} account(s)")
    return created


def auto_approve(clip_id: int) -> None:
    s = config.load()
    if not s.get("autopilot"):
        return
    clip = db.get("clips", clip_id)
    if clip and clip["status"] == "ready" and not clip.get("spoiler") and \
            float(clip.get("score") or 0) >= float(s.get("auto_approve_score") or 8):
        try:
            assign(clip_id)
        except Exception as e:
            bus.note(f"Autopilot couldn't schedule clip #{clip_id}: {e}", "warn")


# ---------------------------------------------------------------- dispatcher

def _post_job(post_id: int) -> None:
    try:
        post = db.get("posts", post_id)
        clip = db.get("clips", post["clip_id"])
        acc = db.get("accounts", post["account_id"])
        if not (post and clip and acc) or post["status"] not in ("queued",):
            return
        db.update("posts", post_id, {"status": "uploading", "attempts": post["attempts"] + 1, "error": None})
        bus.publish("post", {"id": post_id, "status": "uploading"})

        def prog(f: float, _last=[0.0]):
            if f - _last[0] >= 0.1 or f >= 1:
                _last[0] = f
                bus.publish("post", {"id": post_id, "status": "uploading", "progress": round(f, 2)})

        if acc["platform"] == "youtube":
            sched = post["scheduled_at"] if post["scheduled_at"] > time.time() + YT_MIN_LEAD else None
            res = youtube.upload(acc, clip["path"], post["title"], post["description"], post.get("hashtags") or [],
                                 sched, prog)
            status = "scheduled" if sched else "published"
            err = ("YouTube locked this video to private: your Google Cloud project has not passed the YouTube API "
                   "audit yet (Settings → Platforms explains how).") if res.get("locked") else None
            db.update("posts", post_id, {"status": status, "external_id": res["id"], "url": res["url"], "error": err})
        else:
            res = instagram.publish(acc, clip["path"], post["description"], prog)
            db.update("posts", post_id, {"status": "published", "external_id": res["id"], "url": res.get("url")})
        db.update("accounts", acc["id"], {"last_post_at": time.time(), "last_ok": time.time()})
        if post.get("campaign_id"):
            camp = db.get("campaigns", post["campaign_id"]) or {}
            db.update("posts", post_id, {"submit_status": "tracked" if camp.get("submit_mode") == "tracked" else "pending"})
        bus.publish("post", {"id": post_id, "status": db.get("posts", post_id)["status"], "url": res.get("url")})
        bus.note(f"{'Scheduled' if acc['platform'] == 'youtube' and post['scheduled_at'] > time.time() else 'Posted'}"
                 f" on {acc['name']}: {post['title'][:60]}")
    except Exception as e:
        traceback.print_exc()
        post = db.get("posts", post_id)
        attempts = post["attempts"] if post else 3
        retry = attempts < 3
        db.update("posts", post_id, {"status": "queued" if retry else "failed", "error": str(e)[:400],
                                     "scheduled_at": time.time() + 600 * attempts if retry else post["scheduled_at"]})
        bus.publish("post", {"id": post_id, "status": "queued" if retry else "failed", "error": str(e)[:200]})
        bus.note(f"Post #{post_id} {'will retry' if retry else 'failed'}: {str(e)[:160]}", "warn" if retry else "error")
    finally:
        with _busy_lock:
            _busy.discard(post_id)


def _dispatch() -> None:
    now = time.time()
    rows = db.all("SELECT p.id, p.scheduled_at, a.platform FROM posts p JOIN accounts a ON a.id=p.account_id "
                  "WHERE p.status='queued' AND a.active=1 ORDER BY p.scheduled_at")
    for r in rows:
        # YouTube: upload early and let YouTube publish on schedule. Instagram: post at the slot time.
        due = r["platform"] == "youtube" or r["scheduled_at"] <= now
        if not due:
            continue
        with _busy_lock:
            if r["id"] in _busy or len(_busy) >= 3:
                continue
            _busy.add(r["id"])
        _uploads.submit(_post_job, r["id"])
    # scheduled YouTube posts whose time has passed are live now
    db.execute("UPDATE posts SET status='published' WHERE status='scheduled' AND scheduled_at<=?", (now,))


def _refresh_tokens() -> None:
    for a in db.all("SELECT * FROM accounts WHERE platform='instagram' AND token_expires IS NOT NULL "
                    "AND token_expires < ?", (time.time() + 10 * 86400,)):
        try:
            instagram.refresh(a)
        except Exception as e:
            db.update("accounts", a["id"], {"status": "error", "error": f"token refresh failed: {e}"})


def refresh_stats() -> None:
    since = time.time() - 21 * 86400
    for a in db.all("SELECT * FROM accounts WHERE active=1"):
        posts = db.all("SELECT id, external_id FROM posts WHERE account_id=? AND status IN ('published','scheduled') "
                       "AND external_id IS NOT NULL AND scheduled_at>?", (a["id"], since))
        try:
            if a["platform"] == "youtube":
                youtube.refresh_channel(a)
                st = youtube.stats(a, [p["external_id"] for p in posts]) if posts else {}
            else:
                instagram.refresh_profile(a)
                st = instagram.stats(a, [p["external_id"] for p in posts]) if posts else {}
            for p in posts:
                if p["external_id"] in st:
                    x = st[p["external_id"]]
                    db.update("posts", p["id"], {"views": x["views"], "likes": x["likes"], "comments": x["comments"],
                                                 "stats_at": time.time()})
                    growth.snapshot("post", p["id"], views=x["views"], likes=x["likes"])
            fresh = db.get("accounts", a["id"]) or a
            growth.snapshot("account", a["id"], views=int(fresh.get("views_total") or 0),
                            followers=int(fresh.get("followers") or 0))
        except Exception as e:
            db.update("accounts", a["id"], {"error": f"stats: {str(e)[:200]}"})
    bus.publish("stats", {})


def loop() -> None:
    last_tokens = last_stats = 0.0
    while not _stop.wait(10):
        try:
            _dispatch()
            if time.time() - last_tokens > 6 * 3600:
                last_tokens = time.time()
                _refresh_tokens()
            if time.time() - last_stats > 30 * 60:
                last_stats = time.time()
                threading.Thread(target=refresh_stats, daemon=True).start()
        except Exception:
            traceback.print_exc()


def start() -> None:
    pipeline.on_ready(auto_approve)
    threading.Thread(target=loop, daemon=True, name="dispatcher").start()


def stop() -> None:
    _stop.set()
