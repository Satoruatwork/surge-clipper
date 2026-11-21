"""Local HTTP + WebSocket API for the desktop UI (bound to 127.0.0.1 only)."""
from __future__ import annotations

import asyncio
import json
import re
import os
import shutil
import threading
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import (__version__, bus, campaigns, captions, chat, config, db, distribute, fx, growth, instagram, library, live,
               llm, pipeline, render, setup, sites, tools, transcribe, updates, vod, watch, youtube)
from .config import ASSETS, DATA, RUNS, UI_DIST

app = FastAPI(title="Surge")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                   allow_methods=["*"], allow_headers=["*"])
app.mount("/media", StaticFiles(directory=str(DATA)), name="media")
app.mount("/lib", StaticFiles(directory=str(ASSETS)), name="lib")


def media_url(path: str | None) -> str | None:
    if not path:
        return None
    p = Path(path).resolve()
    for root, prefix in ((DATA.resolve(), "/media/"), (ASSETS.resolve(), "/lib/")):
        try:
            return prefix + p.relative_to(root).as_posix()
        except ValueError:
            continue
    return None


def clip_out(c: dict) -> dict:
    c = dict(c)
    c["video_url"] = media_url(c.pop("path", None))
    c["thumb_url"] = media_url(c.pop("thumb", None))
    c.pop("raw_path", None)
    return c


def source_out(s: dict) -> dict:
    s = dict(s)
    s["running"] = s["id"] in live.running() or s["status"] in ("analyzing", "rendering")
    thumb = RUNS / f"src{s['id']:04d}" / "live.jpg"
    s["thumb_url"] = media_url(str(thumb)) if thumb.exists() else None
    s["counts"] = {r["status"]: r["n"] for r in db.all(
        "SELECT status, COUNT(*) n FROM clips WHERE source_id=? GROUP BY status", (s["id"],))}
    who = (s.get("stats") or {}).get("streamer") or s.get("channel")
    s["campaigns"] = [c["name"] for c in campaigns.matching({"streamer": who})] if who else []
    return s


# ---------------------------------------------------------------- state

@app.get("/api/state")
def state():
    return {"settings": config.public(), "styles": {k: v["label"] for k, v in captions.STYLES.items()},
            "encoder": tools.detect_encoder(), "version": __version__, "logs": bus.recent_logs()[-80:],
            "has_google_secret": youtube.CLIENT_FILE.exists() or bool(config.get("google_client_secret"))}


@app.get("/api/stats")
def stats():
    day = time.time() - 86400
    return {
        "clips_total": db.scalar("SELECT COUNT(*) FROM clips"),
        "clips_today": db.scalar("SELECT COUNT(*) FROM clips WHERE created_at>?", (day,)),
        "clips_ready": db.scalar("SELECT COUNT(*) FROM clips WHERE status='ready'"),
        "in_flight": db.scalar("SELECT COUNT(*) FROM clips WHERE status IN "
                               "('queued','cutting','transcribing','writing','rendering')"),
        "posts_queued": db.scalar("SELECT COUNT(*) FROM posts WHERE status IN ('queued','uploading')"),
        "posts_scheduled": db.scalar("SELECT COUNT(*) FROM posts WHERE status='scheduled'"),
        "posts_published": db.scalar("SELECT COUNT(*) FROM posts WHERE status='published'"),
        "posts_today": db.scalar("SELECT COUNT(*) FROM posts WHERE status IN ('published','scheduled') "
                                 "AND scheduled_at BETWEEN ? AND ?", (day, time.time() + 86400)),
        "views": db.scalar("SELECT COALESCE(SUM(views),0) FROM posts"),
        "accounts": db.scalar("SELECT COUNT(*) FROM accounts WHERE active=1"),
        "live": len(live.running()),
    }


@app.get("/api/logs")
def logs():
    return bus.recent_logs()


# ---------------------------------------------------------------- settings

@app.get("/api/settings")
def get_settings():
    return config.public()


@app.put("/api/settings")
def put_settings(body: dict):
    return config.save(body)


@app.get("/api/update")
def update_status():
    return updates.status()


@app.post("/api/update/check")
def update_check():
    return updates.check()


@app.post("/api/update/open")
def update_open():
    if not updates.open_download():
        raise HTTPException(404, "No update to download yet. Use Check now first.")
    return {"ok": True}


@app.post("/api/settings/google-secret")
async def google_secret(file: UploadFile):
    data = await file.read()
    try:
        parsed = json.loads(data)
        assert "installed" in parsed or "web" in parsed
    except Exception:
        raise HTTPException(400, "That isn't a Google OAuth client file (client_secret_*.json).")
    youtube.CLIENT_FILE.write_bytes(data)
    config.save({"google_client_secret": ""})
    return {"ok": True}


# ---------------------------------------------------------------- sources

class NewSource(BaseModel):
    url: str
    kind: str | None = None
    target: int | None = None
    game: str | None = None


@app.get("/api/sources")
def sources():
    return [source_out(s) for s in db.all("SELECT * FROM sources ORDER BY id DESC")]


@app.post("/api/sources")
def add_source(body: NewSource):
    url = body.url.strip()
    if not url:
        raise HTTPException(400, "Paste a stream or VOD link.")
    info = chat.parse_source(url)
    if info["platform"] == "other":
        raise HTTPException(400, "Use a Twitch, Kick or YouTube link (or a local video file path).")
    kind = body.kind or info["kind_hint"]
    if kind is None:  # YouTube: ask yt-dlp whether it's live right now
        p = tools.run([tools.ytdlp(), "-J", "--no-warnings", "--no-playlist", url], timeout=60, check=False)
        try:
            meta = json.loads(p.stdout or b"{}")
        except json.JSONDecodeError:
            meta = {}
        kind = "live" if meta.get("is_live") or meta.get("live_status") == "is_live" else "vod"
    settings = {"layout": "auto"}
    if body.target:
        settings["target"] = int(body.target)
    if body.game:
        settings["game"] = body.game
    sid = db.insert("sources", {"url": url, "platform": info["platform"], "channel": info.get("channel"),
                                "kind": kind, "status": "starting", "settings": settings})
    (live.start if kind == "live" else vod.start)(sid)
    return source_out(db.get("sources", sid))


@app.post("/api/sources/{sid}/start")
def start_source(sid: int):
    s = db.get("sources", sid) or _404()
    db.update("sources", sid, {"status": "starting", "error": None})
    (live.start if s["kind"] == "live" else vod.start)(sid)
    return source_out(db.get("sources", sid))


@app.post("/api/sources/{sid}/stop")
def stop_source(sid: int):
    s = db.get("sources", sid) or _404()
    (live.stop if s["kind"] == "live" else vod.cancel)(sid)
    return {"ok": True}


@app.patch("/api/sources/{sid}")
def patch_source(sid: int, body: dict):
    s = db.get("sources", sid) or _404()
    st = {**(s.get("settings") or {}), **(body.get("settings") or {})}
    for k, v in list(st.items()):
        if v is None:
            st.pop(k)
    db.update("sources", sid, {"settings": st})
    return source_out(db.get("sources", sid))


@app.delete("/api/sources/{sid}")
def delete_source(sid: int):
    s = db.get("sources", sid) or _404()
    (live.stop if s["kind"] == "live" else vod.cancel)(sid)
    db.execute("DELETE FROM sources WHERE id=?", (sid,))
    shutil.rmtree(RUNS / f"src{sid:04d}", ignore_errors=True)
    return {"ok": True}


@app.get("/api/sources/{sid}/pulse")
def pulse(sid: int):
    return live.pulse_history(sid)


@app.post("/api/sources/{sid}/snapshot")
def snapshot(sid: int):
    """A full-res-ish frame for the facecam editor (from the latest clip raw or the live buffer)."""
    s = db.get("sources", sid) or _404()
    out = RUNS / f"src{sid:04d}" / "frame.jpg"
    out.parent.mkdir(parents=True, exist_ok=True)
    clip = db.one("SELECT raw_path, peak, signals_json FROM clips WHERE source_id=? AND raw_path IS NOT NULL "
                  "ORDER BY id DESC LIMIT 1", (sid,))
    src = None
    if clip and clip.get("raw_path") and Path(clip["raw_path"]).exists():
        src, at = Path(clip["raw_path"]), 5.0
    else:
        live_jpg = RUNS / f"src{sid:04d}" / "live.jpg"
        if live_jpg.exists():
            shutil.copy(live_jpg, out)
            return {"url": media_url(str(out)) + f"?v={int(time.time())}", "frame": (s.get("settings") or {}).get("frame")}
    if not src:
        raise HTTPException(409, "No footage yet. Wait for the first clip or for the live preview.")
    info = tools.probe(src)
    render.snapshot(src, at, out, width=min(1280, info["width"]))
    st = {**(s.get("settings") or {}), "frame": [info["width"], info["height"]]}
    db.update("sources", sid, {"settings": st})
    return {"url": media_url(str(out)) + f"?v={int(time.time())}", "frame": st["frame"]}


# ---------------------------------------------------------------- clips

@app.get("/api/clips")
def clips(status: str | None = None, source: int | None = None, limit: int = 200):
    sql, args = "SELECT * FROM clips WHERE 1=1", []
    if status:
        parts = status.split(",")
        sql += f" AND status IN ({','.join('?' * len(parts))})"
        args += parts
    if source:
        sql += " AND source_id=?"
        args.append(source)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    rows = [clip_out(c) for c in db.all(sql, args)]
    if rows:
        ids = [r["id"] for r in rows]
        posts = db.all(f"SELECT clip_id, status, COUNT(*) n, SUM(views) v FROM posts WHERE clip_id IN "
                       f"({','.join('?' * len(ids))}) GROUP BY clip_id, status", ids)
        by = {}
        for p in posts:
            by.setdefault(p["clip_id"], {})[p["status"]] = p["n"]
            by[p["clip_id"]]["views"] = by[p["clip_id"]].get("views", 0) + (p["v"] or 0)
        for r in rows:
            r["posts"] = by.get(r["id"], {})
    return rows


@app.get("/api/clips/{cid}")
def clip(cid: int):
    c = db.get("clips", cid) or _404()
    out = clip_out(c)
    out["post_list"] = db.all("SELECT p.*, a.name account_name, a.platform account_platform, a.avatar account_avatar "
                              "FROM posts p JOIN accounts a ON a.id=p.account_id WHERE clip_id=? ORDER BY scheduled_at",
                              (cid,))
    return out


@app.patch("/api/clips/{cid}")
def patch_clip(cid: int, body: dict):
    db.get("clips", cid) or _404()
    allowed = {k: v for k, v in body.items() if k in ("title", "hook", "description", "hashtags", "variants", "fx")}
    db.update("clips", cid, allowed)
    bus.publish("clip", {"id": cid, **{k: v for k, v in allowed.items() if k in ("title", "hook")}})
    return clip_out(db.get("clips", cid))


@app.post("/api/clips/{cid}/rerender")
def rerender(cid: int, body: dict):
    c = db.get("clips", cid) or _404()
    if not c.get("raw_path") or not Path(c["raw_path"]).exists():
        raise HTTPException(409, "The source footage for this clip is gone.")
    pipeline.rerender(cid, body)
    return {"ok": True}


class Approve(BaseModel):
    accounts: list[int] | None = None
    when: float | None = None


@app.post("/api/clips/{cid}/approve")
def approve(cid: int, body: Approve):
    try:
        posts = distribute.assign(cid, body.accounts, body.when)
    except ValueError as e:
        raise HTTPException(409, str(e))
    if not posts:
        db.update("clips", cid, {"status": "approved"})
        bus.publish("clip", {"id": cid, "status": "approved"})
    return {"posts": posts}


# ---------------------------------------------------------------- setup center

@app.get("/api/setup")
def setup_status():
    return setup.status()


@app.post("/api/setup/test/{what}")
def setup_test(what: str):
    fn = {"google": setup.test_google, "meta": setup.test_meta, "ai": setup.test_ai}.get(what)
    if not fn:
        raise HTTPException(404, "unknown test")
    return fn()


@app.get("/ig-callback")
def ig_callback(code: str | None = None, error_description: str | None = None):
    from fastapi.responses import HTMLResponse
    if not code:
        return HTMLResponse(setup.CALLBACK_HTML.format(title="Instagram didn't connect",
                                                       text=error_description or "No code came back. Try again."))
    try:
        ids = instagram.connect_code(code)
        bus.publish("accounts", {"ok": True, "ids": ids})
        names = ", ".join((db.get("accounts", i) or {}).get("handle", "") for i in ids)
        return HTMLResponse(setup.CALLBACK_HTML.format(title=f"Connected {names}",
                                                       text="You can close this tab and go back to Surge."))
    except Exception as e:
        return HTMLResponse(setup.CALLBACK_HTML.format(title="Instagram didn't connect", text=str(e)[:300]))


# ---------------------------------------------------------------- account management

@app.post("/api/accounts/{aid}/check")
def check_account(aid: int):
    a = db.get("accounts", aid) or _404()
    return growth.check(a)


@app.post("/api/accounts/check-all")
def check_all():
    def run():
        for a in db.all("SELECT * FROM accounts"):
            growth.check(a)
        bus.publish("accounts", {})
    threading.Thread(target=run, daemon=True).start()
    return {"ok": True}


class Bulk(BaseModel):
    ids: list[int]
    patch: dict


@app.post("/api/accounts/bulk")
def bulk_accounts(body: Bulk):
    allowed = {k: v for k, v in body.patch.items() if k in ("tags", "windows", "daily_cap", "active", "group_id", "warmup")}
    if "active" in allowed:
        allowed["active"] = int(bool(allowed["active"]))
    for aid in body.ids:
        extra = {}
        if allowed.get("warmup"):
            a = db.get("accounts", aid) or {}
            if not a.get("warmup"):
                extra["warmup_started"] = time.time()
        db.update("accounts", aid, {**allowed, **extra})
    return {"updated": len(body.ids)}


@app.get("/api/groups")
def groups():
    rows = db.all("SELECT * FROM groups ORDER BY name")
    for g in rows:
        g["members"] = db.scalar("SELECT COUNT(*) FROM accounts WHERE group_id=?", (g["id"],))
    return rows


@app.post("/api/groups")
def add_group(body: dict):
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "Give the group a name.")
    gid = db.insert("groups", {"name": name, "color": body.get("color"), "tags": body.get("tags") or [],
                               "windows": body.get("windows") or ["10:00", "13:00", "16:00", "19:00", "22:00"],
                               "daily_cap": int(body.get("daily_cap") or 4)})
    return db.get("groups", gid)


@app.patch("/api/groups/{gid}")
def patch_group(gid: int, body: dict):
    db.get("groups", gid) or _404()
    db.update("groups", gid, {k: v for k, v in body.items() if k in ("name", "color", "tags", "windows", "daily_cap")})
    return db.get("groups", gid)


@app.post("/api/groups/{gid}/apply")
def apply_group(gid: int):
    return {"updated": growth.apply_group(gid)}


@app.delete("/api/groups/{gid}")
def delete_group(gid: int):
    db.execute("UPDATE accounts SET group_id=NULL WHERE group_id=?", (gid,))
    db.execute("DELETE FROM groups WHERE id=?", (gid,))
    return {"ok": True}


# ---------------------------------------------------------------- campaigns

CAMPAIGN_FIELDS = ("name", "url", "rate", "budget", "hashtags", "mentions", "streamers", "accounts", "platforms",
                   "min_seconds", "max_seconds", "status", "ends_at", "notes", "site_id", "campaign_url",
                   "submit_url", "submit_mode", "code", "dedicated")


@app.get("/api/campaign-sources")
def campaign_sources():
    f = ASSETS / "campaign_sources.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {"sources": []}


@app.get("/api/campaigns")
def list_campaigns():
    return [{**c, "stats": campaigns.stats(c)} for c in db.all("SELECT * FROM campaigns ORDER BY status, id DESC")]


@app.post("/api/campaigns")
def add_campaign(body: dict):
    if not (body.get("name") or "").strip():
        raise HTTPException(400, "Give the campaign a name.")
    cid = db.insert("campaigns", {k: v for k, v in body.items() if k in CAMPAIGN_FIELDS})
    c = db.get("campaigns", cid)
    _watch_streamers(c)
    return {**c, "stats": campaigns.stats(c)}


@app.patch("/api/campaigns/{cid}")
def patch_campaign(cid: int, body: dict):
    db.get("campaigns", cid) or _404()
    db.update("campaigns", cid, {k: v for k, v in body.items() if k in CAMPAIGN_FIELDS})
    c = db.get("campaigns", cid)
    _watch_streamers(c)
    return {**c, "stats": campaigns.stats(c)}


# ---------------------------------------------------------------- campaign sites

@app.get("/api/sites")
def list_sites():
    rows = db.all("SELECT * FROM sites ORDER BY custom, id")
    for r in rows:
        r["campaigns"] = db.scalar("SELECT COUNT(*) FROM campaigns WHERE site_id=? AND status='active'", (r["id"],))
    return {"sites": rows, "max": sites.MAX_SITES}


@app.post("/api/sites")
def add_site(body: dict):
    if db.scalar("SELECT COUNT(*) FROM sites") >= sites.MAX_SITES:
        raise HTTPException(409, f"Surge keeps {sites.MAX_SITES} sites at most. Remove one of your own first.")
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "Give the site a name.")
    sid = db.insert("sites", {"key": f"custom-{int(time.time())}", "name": name, "url": (body.get("url") or "").strip(),
                              "notes": body.get("notes") or "", "enabled": 1, "custom": 1, "accounts": []})
    return db.get("sites", sid)


@app.patch("/api/sites/{sid}")
def patch_site(sid: int, body: dict):
    db.get("sites", sid) or _404()
    patch = {k: v for k, v in body.items() if k in ("enabled", "accounts", "url", "notes", "name")}
    if "enabled" in patch:
        patch["enabled"] = int(bool(patch["enabled"]))
    db.update("sites", sid, patch)
    return db.get("sites", sid)


@app.delete("/api/sites/{sid}")
def delete_site(sid: int):
    s = db.get("sites", sid) or _404()
    if not s.get("custom"):
        raise HTTPException(409, "Built-in sites can be switched off, not removed.")
    db.execute("DELETE FROM sites WHERE id=?", (sid,))
    return {"ok": True}


@app.post("/api/open")
def open_page(body: dict):
    url = (body.get("url") or "").strip()
    if not url.startswith(("https://", "http://")):
        raise HTTPException(400, "That page has no web address yet.")
    return {"opened": sites.open_url(url, body.get("title") or "Surge")}


@app.get("/api/campaigns/{cid}/submissions")
def campaign_submissions(cid: int):
    rows = campaigns.submissions(cid)
    for r in rows:
        r["thumb_url"] = media_url(r.pop("thumb", None))
    return rows


class MarkSubmitted(BaseModel):
    ids: list[int]
    status: str = "submitted"


@app.post("/api/campaigns/{cid}/submissions")
def mark_submissions(cid: int, body: MarkSubmitted):
    status = body.status if body.status in ("submitted", "pending", "tracked") else "submitted"
    for pid in body.ids:
        db.execute("UPDATE posts SET submit_status=?, submitted_at=? WHERE id=? AND campaign_id=?",
                   (status, time.time() if status == "submitted" else None, pid, cid))
    return {"updated": len(body.ids)}


def _watch_streamers(c: dict) -> None:
    """A campaign's streamers go on the watchlist, so their streams get clipped the moment they go live."""
    for name in c.get("streamers") or []:
        login = re.sub(r"[^a-z0-9_]", "", (name or "").lower())
        if login and not db.one("SELECT id FROM watch WHERE lower(channel)=?", (login,)):
            try:
                watch.add(f"https://www.twitch.tv/{login}", True, 6)
            except ValueError:
                pass


@app.delete("/api/campaigns/{cid}")
def delete_campaign(cid: int):
    db.execute("UPDATE posts SET campaign_id=NULL WHERE campaign_id=?", (cid,))
    db.execute("DELETE FROM campaigns WHERE id=?", (cid,))
    return {"ok": True}


# ---------------------------------------------------------------- watchlist + discover

@app.get("/api/watch")
def list_watch():
    return db.all("SELECT * FROM watch ORDER BY live DESC, viewers DESC, id")


@app.post("/api/watch")
def add_watch(body: dict):
    try:
        wid = watch.add(body.get("url", ""), bool(body.get("auto", True)), int(body.get("priority", 5)),
                        body.get("category"))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return db.get("watch", wid)


@app.patch("/api/watch/{wid}")
def patch_watch(wid: int, body: dict):
    db.get("watch", wid) or _404()
    patch = {k: v for k, v in body.items() if k in ("auto", "priority", "category")}
    if "auto" in patch:
        patch["auto"] = int(bool(patch["auto"]))
    db.update("watch", wid, patch)
    return db.get("watch", wid)


@app.delete("/api/watch/{wid}")
def delete_watch(wid: int):
    db.execute("DELETE FROM watch WHERE id=?", (wid,))
    return {"ok": True}


@app.post("/api/watch/refresh")
def refresh_watch():
    threading.Thread(target=watch.tick, daemon=True).start()
    return {"ok": True}


@app.get("/api/discover")
def discover(game: str | None = None):
    try:
        return watch.discover(game or config.get("watch_category") or "Grand Theft Auto VI")
    except Exception as e:
        raise HTTPException(502, f"Couldn't load live streams: {str(e)[:200]}")


# ---------------------------------------------------------------- performance + storage

@app.get("/api/insights")
def insights():
    out = growth.insights()
    for t in out["top"]:
        t["thumb_url"] = media_url(t.pop("thumb", None))
    out["campaigns"] = [{"name": c["name"], **campaigns.stats(c)} for c in db.all("SELECT * FROM campaigns")]
    return out


@app.get("/api/storage")
def storage():
    return growth.storage()


@app.post("/api/storage/cleanup")
def cleanup(body: dict):
    return growth.cleanup(int(body.get("raw_days", 3)))


@app.post("/api/clips/{cid}/autofx")
def autofx(cid: int):
    """Place the edit again: Claude when a key is set, otherwise the built-in director with a fresh variation."""
    import random
    c = db.get("clips", cid) or _404()
    words, dur = c.get("words") or [], float(c.get("duration") or 0)
    peak_rel = float(c["peak"]) - float(c.get("start") or c["peak"])
    plan = None
    if config.get("anthropic_api_key"):
        meta = llm.write_clip({"streamer": c.get("streamer"), "platform": c.get("platform"), "game": c.get("game"),
                               "duration": dur, "chat_class": c.get("chat_class"), "chat": c.get("chat") or [],
                               "strength": (c.get("signals") or {}).get("score", 0), "peak": c["peak"],
                               "peak_rel": peak_rel, "fx_vocab": fx.vocabulary(),
                               "fx_budget": fx.LEVEL_CUES.get(config.get("fx_level") or "medium", 4), "words": words})
        if isinstance(meta.get("fx"), dict):
            plan = fx.clean({**meta["fx"], "source": "claude"}, dur)
    if not plan or not plan["cues"]:
        plan = fx.heuristic(words, dur, peak_rel, c.get("chat_class"), transcribe.text_of(words),
                            level=config.get("fx_level") if fx.is_on() else "medium", salt=random.randint(0, 999))
    return plan


@app.get("/api/library")
def get_library():
    def out(i: dict) -> dict:
        return {**{k: v for k, v in i.items() if k != "file"}, "url": media_url(i["file"])}
    sounds = [out(i) for i in library.items("sound")]
    songs = [out(i) for i in library.items("song")]
    have = {library._norm(s["name"]) for s in songs}
    cat = [{**c, "in_library": library._norm(c["title"]) in have} for c in library.catalog()]
    return {"sounds": sounds, "songs": songs, "catalog": cat,
            "stickers": [{"name": n, "url": media_url(p)} for n, p in library.stickers().items()]}


@app.post("/api/library/upload")
async def upload_library(files: list[UploadFile], kind: str = "sound"):
    if kind not in ("sound", "song"):
        raise HTTPException(400, "kind must be sound or song")
    added, errors = [], []
    for f in files:
        try:
            added.append(library.add_file(f.filename or "audio", await f.read(), kind))
        except ValueError as e:
            errors.append(str(e))
    return {"added": [{k: v for k, v in a.items() if k != "file"} for a in added], "errors": errors}


@app.patch("/api/library/{item_id:path}")
def patch_library(item_id: str, body: dict):
    try:
        item = library.update(item_id, body)
    except KeyError:
        raise HTTPException(404, "Built-in sounds can't be edited; add your own copy instead.")
    return {k: v for k, v in item.items() if k != "file"}


@app.delete("/api/library/{item_id:path}")
def delete_library(item_id: str):
    library.remove(item_id)
    return {"ok": True}


@app.post("/api/clips/{cid}/retry")
def retry_clip(cid: int):
    c = db.get("clips", cid) or _404()
    d = pipeline.clip_dir(cid)
    raw = next((p for p in (d / "raw.ts", d / "raw.mp4") if p.exists()), None)
    if not raw:
        raise HTTPException(409, "The footage for this clip is gone, so it can't be retried.")
    sig = c.get("signals") or {}
    raw_start = float(sig["raw_start"]) if "raw_start" in sig else max(0.0, c["peak"] - float(config.get("pre_roll")) - 11)
    db.update("clips", cid, {"status": "queued", "error": None, "layout": None, "cam": None})
    bus.publish("clip", {"id": cid, "status": "queued"})
    pipeline.submit(cid, lambda _d, raw=raw, raw_start=raw_start: (raw, raw_start))
    return {"ok": True}


@app.post("/api/clips/{cid}/reject")
def reject(cid: int):
    db.update("clips", cid, {"status": "rejected"})
    db.execute("UPDATE posts SET status='cancelled' WHERE clip_id=? AND status='queued'", (cid,))
    bus.publish("clip", {"id": cid, "status": "rejected"})
    return {"ok": True}


@app.post("/api/clips/{cid}/restore")
def restore(cid: int):
    db.update("clips", cid, {"status": "ready"})
    bus.publish("clip", {"id": cid, "status": "ready"})
    return {"ok": True}


@app.delete("/api/clips/{cid}")
def delete_clip(cid: int):
    db.execute("UPDATE posts SET status='cancelled' WHERE clip_id=? AND status='queued'", (cid,))
    db.execute("DELETE FROM clips WHERE id=?", (cid,))
    shutil.rmtree(pipeline.clip_dir(cid), ignore_errors=True)
    bus.publish("clip", {"id": cid, "status": "deleted"})
    return {"ok": True}


@app.post("/api/clips/{cid}/reveal")
def reveal(cid: int):
    c = db.get("clips", cid) or _404()
    if c.get("path") and os.name == "nt":
        import subprocess
        subprocess.Popen(["explorer", "/select,", str(Path(c["path"]))])
    return {"ok": True}


# ---------------------------------------------------------------- accounts

@app.get("/api/accounts")
def accounts():
    rows = db.all("SELECT * FROM accounts ORDER BY platform, id")
    day = time.time() - 86400
    for a in rows:
        a.pop("token_file", None)
        a["posts"] = {r["status"]: r["n"] for r in db.all(
            "SELECT status, COUNT(*) n FROM posts WHERE account_id=? GROUP BY status", (a["id"],))}
        a["views"] = db.scalar("SELECT COALESCE(SUM(views),0) FROM posts WHERE account_id=?", (a["id"],))
        a["today"] = db.scalar("SELECT COUNT(*) FROM posts WHERE account_id=? AND scheduled_at>? AND "
                               "status NOT IN ('failed','cancelled')", (a["id"], day))
        nxt = db.scalar("SELECT MIN(scheduled_at) FROM posts WHERE account_id=? AND status IN ('queued','scheduled') "
                        "AND scheduled_at>?", (a["id"], time.time()))
        a["next_post"] = nxt
        a["cap_now"] = growth.effective_cap(a)
        a["failed_24h"] = db.scalar("SELECT COUNT(*) FROM posts WHERE account_id=? AND status='failed' AND "
                                    "updated_at>?", (a["id"], day))
        a["token_days"] = round((a["token_expires"] - time.time()) / 86400, 1) \
            if a["platform"] == "instagram" and a.get("token_expires") else None
        first = db.one("SELECT followers FROM snapshots WHERE kind='account' AND ref_id=? ORDER BY day LIMIT 1", (a["id"],))
        a["followers_gained"] = (a["followers"] or 0) - (first["followers"] if first else (a["followers"] or 0))
    return rows


def _bg(fn, label: str):
    def run():
        try:
            ids = fn()
            bus.publish("accounts", {"ok": True, "ids": ids})
        except Exception as e:
            bus.publish("accounts", {"ok": False, "error": str(e)[:300]})
            bus.note(f"{label} failed: {str(e)[:200]}", "error")
    threading.Thread(target=run, daemon=True).start()


@app.post("/api/accounts/youtube/connect")
def yt_connect():
    if not (youtube.CLIENT_FILE.exists() or config.get("google_client_secret")):
        raise HTTPException(409, "Add your Google OAuth client file in Settings → Platforms first.")
    _bg(youtube.connect, "YouTube sign-in")
    return {"started": True}


class Token(BaseModel):
    token: str


@app.post("/api/accounts/instagram/token")
def ig_token(body: Token):
    try:
        return {"ids": instagram.connect_token(body.token)}
    except Exception as e:
        raise HTTPException(400, str(e))


@app.get("/api/accounts/instagram/auth-url")
def ig_auth():
    try:
        return {"url": instagram.auth_url()}
    except Exception as e:
        raise HTTPException(409, str(e))


class Code(BaseModel):
    url: str


@app.post("/api/accounts/instagram/code")
def ig_code(body: Code):
    try:
        return {"ids": instagram.connect_code(body.url)}
    except Exception as e:
        raise HTTPException(400, str(e))


@app.patch("/api/accounts/{aid}")
def patch_account(aid: int, body: dict):
    db.get("accounts", aid) or _404()
    allowed = {k: v for k, v in body.items() if k in ("tags", "windows", "daily_cap", "active", "name", "group_id",
                                                       "warmup")}
    if allowed.get("warmup") and not (db.get("accounts", aid) or {}).get("warmup"):
        allowed["warmup_started"] = time.time()
    if "active" in allowed:
        allowed["active"] = int(bool(allowed["active"]))
    if "active" in allowed and allowed["active"]:
        allowed["status"] = "ok"
    db.update("accounts", aid, allowed)
    return {"ok": True}


@app.delete("/api/accounts/{aid}")
def delete_account(aid: int):
    a = db.get("accounts", aid) or _404()
    db.execute("UPDATE posts SET status='cancelled' WHERE account_id=? AND status='queued'", (aid,))
    db.execute("DELETE FROM accounts WHERE id=?", (aid,))
    if a.get("token_file"):
        Path(a["token_file"]).unlink(missing_ok=True)
    return {"ok": True}


@app.post("/api/accounts/refresh")
def refresh_accounts():
    threading.Thread(target=distribute.refresh_stats, daemon=True).start()
    return {"ok": True}


# ---------------------------------------------------------------- posts

@app.get("/api/posts")
def posts(start: float | None = None, end: float | None = None):
    start = start or time.time() - 2 * 86400
    end = end or time.time() + 8 * 86400
    rows = db.all("SELECT p.*, c.thumb, c.hook, c.streamer, c.duration, a.name account_name, a.platform, a.avatar "
                  "FROM posts p JOIN clips c ON c.id=p.clip_id JOIN accounts a ON a.id=p.account_id "
                  "WHERE p.scheduled_at BETWEEN ? AND ? AND p.status!='cancelled' ORDER BY p.scheduled_at",
                  (start, end))
    for r in rows:
        r["thumb_url"] = media_url(r.pop("thumb", None))
    return rows


@app.patch("/api/posts/{pid}")
def patch_post(pid: int, body: dict):
    p = db.get("posts", pid) or _404()
    if p["status"] not in ("queued", "failed"):
        raise HTTPException(409, "Only posts that haven't uploaded yet can be edited.")
    allowed = {k: v for k, v in body.items() if k in ("scheduled_at", "title", "description")}
    db.update("posts", pid, allowed)
    return {"ok": True}


@app.post("/api/posts/{pid}/cancel")
def cancel_post(pid: int):
    db.execute("UPDATE posts SET status='cancelled' WHERE id=? AND status IN ('queued','failed')", (pid,))
    bus.publish("post", {"id": pid, "status": "cancelled"})
    return {"ok": True}


@app.post("/api/posts/{pid}/retry")
def retry_post(pid: int):
    db.execute("UPDATE posts SET status='queued', attempts=0, error=NULL, scheduled_at=MAX(scheduled_at, ?) "
               "WHERE id=? AND status='failed'", (time.time() + 60, pid))
    bus.publish("post", {"id": pid, "status": "queued"})
    return {"ok": True}


@app.post("/api/posts/{pid}/now")
def post_now(pid: int):
    db.execute("UPDATE posts SET scheduled_at=? WHERE id=? AND status='queued'", (time.time(), pid))
    return {"ok": True}


# ---------------------------------------------------------------- live updates

@app.websocket("/ws")
async def ws(sock: WebSocket):
    await sock.accept()
    q = bus.subscribe()
    try:
        while True:
            msg = await q.get()
            await sock.send_text(msg)
    except (WebSocketDisconnect, RuntimeError, asyncio.CancelledError):
        pass
    finally:
        bus.unsubscribe(q)


@app.on_event("startup")
async def _startup():
    bus.attach(asyncio.get_running_loop())


def _404():
    raise HTTPException(404, "not found")


# ---------------------------------------------------------------- UI (built SPA)

if UI_DIST.exists():
    app.mount("/assets", StaticFiles(directory=str(UI_DIST / "assets")), name="ui-assets")

    @app.get("/{path:path}")
    def spa(path: str):
        f = UI_DIST / path
        if path and f.is_file():
            return FileResponse(f)
        return FileResponse(UI_DIST / "index.html", headers={"Cache-Control": "no-cache"})
else:
    @app.get("/")
    def no_ui():
        return JSONResponse({"error": "UI not built. Run: cd ui && npm run build"})
