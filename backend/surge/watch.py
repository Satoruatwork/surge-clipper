"""Watchlist: streamers Surge keeps an eye on. When one goes live in the right game, a live monitor starts on its
own (up to the concurrency limit, biggest streams first) and stops once they've been offline a while.
Discover lists the biggest live streams in a game right now."""
from __future__ import annotations

import json
import threading
import time
import traceback

import httpx

from . import bus, chat, config, db, live

_GQL = "https://gql.twitch.tv/gql"
_HDR = {"Client-Id": "kimne78kx3ncx6brgo4mv6wki5h1ko", "Content-Type": "text/plain;charset=UTF-8"}
_stop = threading.Event()
OFFLINE_GRACE = 15 * 60


def _gql(query: str) -> dict:
    r = httpx.post(_GQL, headers=_HDR, content=json.dumps({"query": query}), timeout=20)
    r.raise_for_status()
    return r.json().get("data") or {}


def twitch_status(logins: list[str]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for i in range(0, len(logins), 50):
        batch = [l for l in logins[i:i + 50] if l]
        if not batch:
            continue
        names = ",".join(json.dumps(l) for l in batch)
        data = _gql(f"query {{ users(logins: [{names}]) {{ login displayName profileImageURL(width: 70) "
                    f"stream {{ title viewersCount game {{ name }} }} }} }}")
        for u in data.get("users") or []:
            if not u:
                continue
            st = u.get("stream")
            out[u["login"].lower()] = {"name": u.get("displayName"), "avatar": u.get("profileImageURL"),
                                       "live": bool(st), "viewers": (st or {}).get("viewersCount", 0),
                                       "title": (st or {}).get("title"), "game": ((st or {}).get("game") or {}).get("name")}
    return out


def kick_status(slug: str) -> dict:
    info = chat.kick_channel(slug)
    ls = info.get("livestream") or {}
    cats = ls.get("categories") or []
    return {"name": (info.get("user") or {}).get("username") or slug, "avatar": (info.get("user") or {}).get("profile_pic"),
            "live": bool(ls), "viewers": ls.get("viewer_count", 0), "title": ls.get("session_title"),
            "game": cats[0].get("name") if cats else None}


def discover(game: str = "Grand Theft Auto VI", first: int = 30) -> dict:
    data = _gql(f"query {{ game(name: {json.dumps(game)}) {{ name viewersCount streams(first: {int(first)}) "
                f"{{ edges {{ node {{ viewersCount title broadcaster {{ login displayName profileImageURL(width: 70) }} }} }} }} }} }}")
    g = data.get("game") or {}
    watched = {(w.get("channel") or "").lower() for w in db.all("SELECT channel FROM watch")}
    streams = []
    for e in ((g.get("streams") or {}).get("edges") or []):
        n = e.get("node") or {}
        b = n.get("broadcaster") or {}
        streams.append({"login": b.get("login"), "name": b.get("displayName"), "avatar": b.get("profileImageURL"),
                        "viewers": n.get("viewersCount", 0), "title": n.get("title"),
                        "watched": (b.get("login") or "").lower() in watched})
    return {"game": g.get("name") or game, "viewers": g.get("viewersCount", 0), "streams": streams}


def add(url: str, auto: bool = True, priority: int = 5, category: str | None = None) -> int:
    info = chat.parse_source(url)
    if info["platform"] not in ("twitch", "kick", "youtube") or info.get("kind_hint") == "vod":
        raise ValueError("Add a channel link (twitch.tv/name, kick.com/name or a YouTube channel).")
    existing = db.one("SELECT id FROM watch WHERE url=?", (url.strip(),))
    if existing:
        return existing["id"]
    wid = db.insert("watch", {"url": url.strip(), "platform": info["platform"], "channel": info.get("channel"),
                              "name": info.get("channel"), "auto": int(auto), "priority": int(priority),
                              "category": category if category is not None else config.get("watch_category")})
    threading.Thread(target=refresh, daemon=True).start()
    return wid


def refresh() -> list[dict]:
    rows = db.all("SELECT * FROM watch")
    tw = [w["channel"] for w in rows if w["platform"] == "twitch" and w.get("channel")]
    try:
        st = twitch_status(tw) if tw else {}
    except Exception as e:
        bus.note(f"Watchlist: Twitch check failed ({str(e)[:80]})", "warn")
        st = {}
    for w in rows:
        try:
            if w["platform"] == "twitch":
                s = st.get((w.get("channel") or "").lower())
            elif w["platform"] == "kick":
                s = kick_status(w["channel"])
            else:
                s = None  # YouTube: the monitor itself finds out when it connects
            if s is None:
                continue
            patch = {"name": s["name"] or w["name"], "avatar": s.get("avatar") or w.get("avatar"),
                     "live": int(s["live"]), "viewers": int(s.get("viewers") or 0), "title": s.get("title"),
                     "game": s.get("game")}
            if s["live"]:
                patch["last_live"] = time.time()
            db.update("watch", w["id"], patch)
        except Exception:
            continue
    bus.publish("watch", {})
    return db.all("SELECT * FROM watch ORDER BY live DESC, viewers DESC")


def _game_ok(w: dict) -> bool:
    want = (w.get("category") or "").strip().lower()
    return not want or want in (w.get("game") or "").lower()


def _source_for(w: dict) -> int:
    if w.get("source_id") and db.get("sources", w["source_id"]):
        return w["source_id"]
    row = db.one("SELECT id FROM sources WHERE url=? AND kind='live' ORDER BY id DESC LIMIT 1", (w["url"],))
    if row:
        sid = row["id"]
    else:
        sid = db.insert("sources", {"url": w["url"], "platform": w["platform"], "channel": w.get("channel"),
                                    "kind": "live", "status": "idle", "settings": {"layout": "auto"}})
    db.update("watch", w["id"], {"source_id": sid})
    return sid


def tick() -> None:
    rows = refresh()
    limit = int(config.get("max_live") or 3)
    running = live.running()
    # stop monitors whose streamer has been offline for a while (or left the game)
    for w in rows:
        sid = w.get("source_id")
        if not (w.get("auto") and sid and sid in running):
            continue
        gone = not w["live"] and time.time() - float(w.get("last_live") or 0) > OFFLINE_GRACE
        if gone or (w["live"] and not _game_ok(w)):
            live.stop(sid)
            bus.note(f"Watchlist: stopped {w['name']} ({'offline' if gone else 'switched game'})")
    running = live.running()
    wanted = sorted([w for w in rows if w.get("auto") and w["live"] and _game_ok(w)],
                    key=lambda w: (-int(w.get("priority") or 5), -int(w.get("viewers") or 0)))
    for w in wanted:
        if len(running) >= limit:
            break
        sid = _source_for(w)
        if sid in running:
            continue
        db.update("sources", sid, {"status": "starting", "error": None})
        live.start(sid)
        running = live.running()
        bus.note(f"Watchlist: {w['name']} is live in {w.get('game') or 'their game'}, watching now")
    bus.publish("source", {"id": 0})


def loop() -> None:
    while not _stop.wait(5):
        try:
            if db.scalar("SELECT COUNT(*) FROM watch WHERE auto=1"):
                tick()
        except Exception:
            traceback.print_exc()
        if _stop.wait(115):
            break


def start() -> None:
    threading.Thread(target=loop, daemon=True, name="watchlist").start()


def stop() -> None:
    _stop.set()
