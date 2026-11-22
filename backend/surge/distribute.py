"""Autopilot distribution: assign approved clips to accounts, give each account its own copy angle, slot posts
into each account's posting times or spread them through its posting hours, upload/publish on time, refresh tokens
and view counts."""
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
UPLOAD_AHEAD = 24 * 3600  # YouTube: upload a Short at most a day before its slot, so each day's uploads fit the day


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


DEFAULT_WINDOWS = ["10:00", "13:00", "16:00", "19:00", "22:00"]


def _clock(s: str) -> int | None:
    """'19:30' as minutes after midnight; '24:00' is the end of the day."""
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", s or "")
    if not m or int(m[2]) > 59 or int(m[1]) * 60 + int(m[2]) > 1440:
        return None
    return int(m[1]) * 60 + int(m[2])


def day_plan(windows: list | None, cap: int, gap: float, jitter: float) -> dict:
    """One posting day in minutes after the midnight it starts from. Exact times ("19:00") post once each; the rest
    of the day's cap spreads evenly through spans ("08:00-23:30", "00:00-00:00" for all day; a span that ends before
    it starts runs past midnight). The day starts at the earliest span, so a night span stays one posting day.
    Spread posts keep half their spacing apart, so the cap fits the day instead of the global minimum gap."""
    times, spans = [], []
    for w in windows or []:
        a, sep, b = str(w).partition("-")
        start, end = _clock(a), _clock(b) if sep else None
        if start is None or start >= 1440:
            continue
        if not sep:
            times.append(start)
        elif end is not None:
            spans.append((start, end if end > start else end + 1440))
    if not times and not spans:
        times = [_clock(w) for w in DEFAULT_WINDOWS]
    day_start = min(a for a, _ in spans) if spans else 0
    slots = sorted({t if t >= day_start else t + 1440 for t in times})
    spans = sorted((a, min(b, day_start + 1440)) for a, b in spans)
    spread, length = max(0, cap - len(slots)), sum(b - a for a, b in spans)
    if spread and length > 0:
        step = length / spread
        gap, jitter = min(gap, step / 2), min(jitter, step / 4)
        for k in range(spread):  # centred in equal shares of the spans, laid end to end
            pos = (k + 0.5) * step
            for a, b in spans:
                if pos < b - a:
                    slots.append(a + pos)
                    break
                pos -= b - a
        slots.sort()
    return {"start": day_start, "slots": slots, "gap": gap, "jitter": jitter}


def day_bounds(account: dict, at: float) -> tuple[float, float]:
    """The posting day `at` falls in, as [start, end) timestamps."""
    start = day_plan(account.get("windows"), 0, 0, 0)["start"]
    d0 = datetime.fromtimestamp(at).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(minutes=start)
    if d0.timestamp() > at:
        d0 -= timedelta(days=1)
    return d0.timestamp(), (d0 + timedelta(days=1)).timestamp()


def _youtube_posts(start: float, end: float) -> int:
    return db.scalar("SELECT COUNT(*) FROM posts p JOIN accounts a ON a.id=p.account_id WHERE a.platform='youtube' "
                     "AND p.status NOT IN ('failed','cancelled') AND p.scheduled_at>=? AND p.scheduled_at<?",
                     (start, end)) or 0


def next_slot(account: dict, after: float) -> float:
    after = max(after, float(account.get("hold_until") or 0))  # uploads paused: nothing goes out before the pause ends
    cap = growth.effective_cap(account)
    # one Google project uploads for every YouTube channel: a day holds no more posts than it can upload
    project_cap = int(config.get("yt_daily_uploads") or 100) if account.get("platform") == "youtube" else 0
    plan = day_plan(account.get("windows"), cap, float(config.get("min_gap_minutes") or 60),
                    float(config.get("slot_jitter_minutes") or 0))
    gap, jitter = plan["gap"] * 60, plan["jitter"] * 60
    rnd = random.Random(int(after) ^ account["id"])
    midnight = datetime.fromtimestamp(after).replace(hour=0, minute=0, second=0, microsecond=0)
    for day in range(-1, 30):  # from yesterday: a span past midnight still belongs to the day it started
        d0 = midnight + timedelta(days=day)
        start = (d0 + timedelta(minutes=plan["start"])).timestamp()
        end = (d0 + timedelta(days=1, minutes=plan["start"])).timestamp()
        if end <= after:
            continue
        taken = _day_posts(account["id"], start)
        if sum(start <= t < end for t in taken) >= cap:
            continue
        if project_cap and _youtube_posts(start, end) >= project_cap:
            continue
        for m in plan["slots"]:
            t = (d0 + timedelta(minutes=m)).timestamp() + rnd.uniform(-jitter, jitter)
            if t >= after and all(abs(t - o) >= gap for o in taken):
                return t
    return after + 86400


# ---------------------------------------------------------------- YouTube's daily upload limits

def _when(ts: float) -> str:
    at = datetime.fromtimestamp(ts)
    return at.strftime("%H:%M") if at.date() == datetime.now().date() else at.strftime("%a %H:%M")


def reslot(account_id: int, after: float) -> int:
    """Move the account's waiting posts that would go out before `after` to fresh slots after it, in order, so a
    pause ends in the usual spacing instead of a burst."""
    acc = db.get("accounts", account_id)
    ids = [r["id"] for r in db.all("SELECT id FROM posts WHERE account_id=? AND status='queued' AND scheduled_at<? "
                                   "ORDER BY scheduled_at", (account_id, after))]
    if not acc or not ids:
        return 0
    db.execute(f"UPDATE posts SET scheduled_at=0 WHERE id IN ({','.join('?' * len(ids))})", ids)  # out of the count
    for pid in ids:
        at = next_slot(acc, after)
        db.update("posts", pid, {"scheduled_at": at})
        bus.publish("post", {"id": pid, "status": "queued", "scheduled_at": at})
    return len(ids)


def hold(account_ids: list[int], until: float, why: str) -> None:
    """Pause uploads on these accounts until `until` and move their waiting posts after it."""
    for aid in account_ids:
        db.update("accounts", aid, {"hold_until": until, "hold_reason": why})
        reslot(aid, until)
    bus.publish("accounts", {})
    bus.note(f"{why} Uploads resume at {_when(until)}; waiting posts moved to new slots.", "warn")


def _project_full(refused: bool) -> None:
    """The Google project's daily upload quota is used up. Surge's own count waits for the quota day to turn; a
    refusal from YouTube is re-checked every two hours, since YouTube doesn't say when it lifts."""
    limit = int(config.get("yt_daily_uploads") or 100)
    until = min(youtube.quota_reset(), time.time() + 2 * 3600) if refused else youtube.quota_reset()
    why = ("YouTube refused uploads: your Google project's daily upload limit is used up." if refused else
           f"Your Google project made today's {limit} YouTube uploads (the allowance set in Setup).")
    hold([a["id"] for a in db.all("SELECT id FROM accounts WHERE platform='youtube'")], until, why)


def _channel_full(acc: dict, refused: bool) -> None:
    """The channel's own daily upload cap is used up. A refusal teaches Surge the cap: the uploads of the last day."""
    done = youtube.channel_uploads(acc["id"], time.time() - 86400)
    limit = max(1, len(done)) if refused else int(acc.get("day_limit") or len(done) or 1)
    if refused:
        db.update("accounts", acc["id"], {"day_limit": limit})
    until = min(max(time.time() + 1800, done[0] + 86400 if done else 0), time.time() + 86400)
    hold([acc["id"]], until, f"YouTube lets {acc['name']} upload about {limit} videos a day, and that's used up.")


def recover() -> int:
    """Posts that failed only because a YouTube daily upload limit was used up go back in line in fresh slots, and
    each channel learns the cap YouTube showed it. Runs when the engine starts."""
    rows = db.all("SELECT p.id, p.account_id, p.error, p.updated_at FROM posts p JOIN accounts a ON "
                  "a.id=p.account_id WHERE p.status='failed' AND a.platform='youtube' AND p.updated_at>?",
                  (time.time() - 3 * 86400,))
    found: dict[int, dict] = {}
    for r in rows:
        kind = youtube.limit_hit(r["error"] or "")
        if not kind:
            continue
        f = found.setdefault(r["account_id"], {"ids": [], "refused_at": None})
        f["ids"].append(r["id"])
        if kind == "channel":
            f["refused_at"] = min(f["refused_at"] or r["updated_at"], r["updated_at"])
    for aid, f in found.items():
        acc = db.get("accounts", aid)
        if not acc:
            continue
        if f["refused_at"] and not acc.get("day_limit"):
            first = f["refused_at"]
            n = sum(1 for t in youtube.channel_uploads(aid, first - 86400) if t <= first)
            db.update("accounts", aid, {"day_limit": max(1, n)})
        db.execute(f"UPDATE posts SET status='queued', attempts=0, error=NULL WHERE id IN "
                   f"({','.join('?' * len(f['ids']))})", f["ids"])
        acc = db.get("accounts", aid) or acc
        reslot(aid, max(time.time() + 600, float(acc.get("hold_until") or 0)))
    total = sum(len(f["ids"]) for f in found.values())
    if total:
        bus.note(f"{total} posts that hit YouTube's daily upload limit are back in line in new slots.")
    return total


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
            db.update("posts", post_id, {"status": status, "external_id": res["id"], "url": res["url"], "error": err,
                                         "uploaded_at": time.time()})
        else:
            res = instagram.publish(acc, clip["path"], post["description"], prog)
            db.update("posts", post_id, {"status": "published", "external_id": res["id"], "url": res.get("url"),
                                         "uploaded_at": time.time()})
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
        acc = db.get("accounts", post["account_id"]) if post else None
        kind = youtube.limit_hit(str(e)) if acc and acc["platform"] == "youtube" else None
        if kind:  # not this post's fault: it waits for the limit to lift, without using up an attempt
            db.update("posts", post_id, {"status": "queued", "error": None,
                                         "attempts": max(0, int(post["attempts"] or 1) - 1)})
            if kind == "channel":
                _channel_full(acc, refused=True)
            else:
                _project_full(refused=True)
            bus.publish("post", {"id": post_id, "status": "queued"})
            return
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
    rows = db.all("SELECT p.id, p.scheduled_at, p.account_id, a.platform, a.hold_until FROM posts p "
                  "JOIN accounts a ON a.id=p.account_id WHERE p.status='queued' AND a.active=1 ORDER BY p.scheduled_at")
    room = None      # YouTube uploads the Google project has left today
    paused = set()   # accounts held during this pass
    for r in rows:
        if r["account_id"] in paused or float(r["hold_until"] or 0) > now:
            continue
        if r["platform"] == "youtube":
            # upload up to a day early and let YouTube publish on schedule, within the day's upload limits
            if r["scheduled_at"] - now > UPLOAD_AHEAD:
                continue
            if room is None:
                with _busy_lock:
                    busy = len(_busy)
                room = int(config.get("yt_daily_uploads") or 100) - youtube.uploads_today() - busy
            if room <= 0:
                _project_full(refused=False)
                paused |= {a["id"] for a in db.all("SELECT id FROM accounts WHERE platform='youtube'")}
                continue
            acc = db.get("accounts", r["account_id"]) or {}
            if acc.get("day_limit") and \
                    len(youtube.channel_uploads(acc["id"], now - 86400)) >= int(acc["day_limit"]):
                _channel_full(acc, refused=False)
                paused.add(acc["id"])
                continue
        elif r["scheduled_at"] > now:  # Instagram posts from this app at the slot time
            continue
        with _busy_lock:
            if r["id"] in _busy or len(_busy) >= 3:
                continue
            _busy.add(r["id"])
        _uploads.submit(_post_job, r["id"])
        if r["platform"] == "youtube":
            room -= 1
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
    try:
        recover()
    except Exception:
        traceback.print_exc()
    threading.Thread(target=loop, daemon=True, name="dispatcher").start()


def stop() -> None:
    _stop.set()
