"""Already viral: re-cut the moments viewers clipped most on Twitch (last 24 hours, 7 days or 30 days) in Surge's
format.

Every viewer clip carries the broadcast and the second it came from, and its view count says how hard the moment
landed. Surge groups clips of the same moment, keeps the most-watched moments, reads a little audio around each one
to find the real peak, then cuts only those sections in full quality and runs them through the normal clip pipeline
(exact onset, sentence-boundary cuts, captions, facecam layout, effects)."""
from __future__ import annotations

import json
import math
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from . import bus, chat, config, db, hype, pipeline, tools, vod, watch

PERIODS = {"day": ("LAST_DAY", "last 24 hours"), "week": ("LAST_WEEK", "last 7 days"),
           "month": ("LAST_MONTH", "last 30 days")}
SAME_MOMENT = 45.0  # clips whose centres are closer than one Short are the same moment
VIDEO_FORMAT = "b[height<=1080][fps<=60]/b"  # Twitch renditions carry their own audio: one input, no drift

_runs: dict[int, threading.Event] = {}


def start(source_id: int) -> None:
    if source_id in _runs:
        return
    ev = threading.Event()
    _runs[source_id] = ev
    threading.Thread(target=_run, args=(source_id, ev), daemon=True, name=f"viral-{source_id}").start()


def cancel(source_id: int) -> None:
    ev = _runs.get(source_id)
    if ev:
        ev.set()


def top_clips(login: str, period: str = "LAST_DAY", limit: int = 400) -> tuple[str, list[dict]]:
    """(display name, the channel's most-viewed viewer clips in the period, each with its broadcast and second)."""
    name, out, after = login, [], None
    while len(out) < limit:
        page = f", after: {json.dumps(after)}" if after else ""
        data = watch._gql(
            "query { user(login: %s) { displayName clips(first: 100%s, criteria: {period: %s, sort: VIEWS_DESC}) "
            "{ edges { cursor node { slug title viewCount createdAt durationSeconds videoOffsetSeconds "
            "game { name } video { id } } } pageInfo { hasNextPage } } } }" % (json.dumps(login), page, period))
        user = data.get("user") or {}
        name = user.get("displayName") or name
        conn = user.get("clips") or {}
        edges = conn.get("edges") or []
        for e in edges:
            n = e.get("node") or {}
            out.append({"slug": n.get("slug"), "title": (n.get("title") or "").strip(),
                        "views": int(n.get("viewCount") or 0), "created": n.get("createdAt"),
                        "duration": float(n.get("durationSeconds") or 30),
                        "offset": n.get("videoOffsetSeconds"), "game": (n.get("game") or {}).get("name"),
                        "video": (n.get("video") or {}).get("id")})
        if not edges or not (conn.get("pageInfo") or {}).get("hasNextPage"):
            break
        after = edges[-1].get("cursor")
    return name, out


def moments(clips: list[dict], category: str = "") -> list[dict]:
    """Group clips of the same moment (same broadcast, centres closer than one Short), most-viewed first. The
    most-viewed clip sets the moment's window; the others add views and help locate the peak."""
    want = category.strip().lower()
    out: list[dict] = []
    for c in sorted(clips, key=lambda c: -c["views"]):
        if not c.get("video") or c.get("offset") is None:
            continue  # the broadcast was deleted: nothing to cut from
        if want and want not in (c.get("game") or "").lower():
            continue
        mid = float(c["offset"]) + c["duration"] / 2
        m = next((m for m in out if m["video"] == c["video"] and abs(m["mid"] - mid) < SAME_MOMENT), None)
        if m:
            m["clips"].append(c)
            m["views"] += c["views"]
        else:
            out.append({"video": c["video"], "mid": mid, "a": float(c["offset"]),
                        "b": float(c["offset"]) + c["duration"], "views": c["views"], "clips": [c]})
    out.sort(key=lambda m: (-m["views"], -len(m["clips"])))
    return out


def peak_in(m: dict, loud: np.ndarray, t0: float) -> float:
    """Second of the moment inside the audio that starts at `t0`: loud, covered by the most-viewed clips, and late
    in the top clip (people press Clip right after the moment, so it sits in the back half)."""
    n = len(loud)
    if n < 5:
        return m["a"] + 0.65 * (m["b"] - m["a"])
    t = t0 + np.arange(n) + 0.5
    cover = np.zeros(n, np.float32)
    for c in m["clips"]:
        cover += (math.log1p(c["views"]) + 1) * ((t >= c["offset"]) & (t <= c["offset"] + c["duration"]))
    cover /= max(float(cover.max()), 1e-6)
    med = float(np.median(loud))
    mad = float(np.median(np.abs(loud - med))) + 1.0
    z = np.minimum((hype.smooth(loud, 2) - med) / (1.4826 * mad), 6)
    late = np.clip((t - m["a"]) / max(1.0, m["b"] - m["a"]), 0, 1)
    score = z + 1.5 * cover + 0.8 * late
    inside = (t >= m["a"] + 2) & (t <= m["b"] + 2)
    if inside.any():
        score[~inside] = -1e9
    return float(t[int(np.argmax(score))])


def reaction_class(titles: list[str]) -> str | None:
    """Read the viewers' clip titles like chat: WTF/what?? is shock, 😭/LMAO is funny, W/insane is hype."""
    return hype.message_weight(" ".join(titles))[1] if titles else None


def _pcm(urls: list[str], headers: dict, a: float, dur: float) -> np.ndarray:
    p = tools.run([tools.ffmpeg(), "-hide_banner", "-v", "error", *vod._hdr_args(headers), "-ss", f"{a:.2f}",
                   "-i", urls[0], "-t", f"{dur:.2f}", "-vn", "-ac", "1", "-ar", "16000", "-f", "s16le", "-"],
                  timeout=300)
    return np.frombuffer(p.stdout, np.int16)


def _media(url: str, fmt: str, tries: int = 3) -> tuple[list[str], dict]:
    for i in range(tries):  # Twitch's playlist server drops the odd connection
        try:
            return vod._media_urls(url, fmt)
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(2 + 3 * i)
    raise RuntimeError("unreachable")


def _run(sid: int, cancel_ev: threading.Event) -> None:
    try:
        src = db.get("sources", sid)
        s = config.load()
        st = src.get("settings") or {}
        parsed = chat.parse_source(src["url"])
        login = (parsed.get("channel") or "").lower()
        if parsed["platform"] != "twitch" or not login:
            raise RuntimeError("Already viral needs a Twitch channel link, like twitch.tv/kaicenat.")
        target = max(1, int(st.get("target") or s["vod_target"]))
        period, label = PERIODS.get(st.get("period") or "day", PERIODS["day"])
        category = st["category"] if "category" in st else (config.get("watch_category") or "")
        game = st.get("game") or "GTA 6"
        pre, post = float(s["pre_roll"]), float(s["post_roll"])

        vod._stage(sid, "Finding the most-clipped moments", 0.03)
        streamer, clips = top_clips(login, period)
        found = moments(clips, category)
        if not found:
            where = f" in {category}" if category else ""
            raise RuntimeError(f"No clips of {streamer}{where} from the {label} with a saved broadcast to cut from.")
        # running again tops the source up to the target: clips you kept stay, moments already tried are skipped
        prior = db.all("SELECT * FROM clips WHERE source_id=?", (sid,))
        tried = {(str(v.get("video")), int(v.get("offset") or -1))
                 for c in prior if (v := (c.get("signals") or {}).get("viral"))}
        kept = [c for c in prior if c["status"] in ("ready", "approved") and (c.get("signals") or {}).get("viral")]
        need = target - len(kept)
        fresh = [m for m in found if (m["video"], round(m["a"])) not in tried]
        cands = fresh[: need + max(5, need // 2)] if need > 0 else []  # spares: some turn out muted or silent
        if not cands:
            stage = f"{len(kept)} clips ready" + ("" if need <= 0 else ", no new moments left")
            db.update("sources", sid, {"status": "done", "stage": stage, "progress": 1.0})
            bus.publish("source", {"id": sid, "status": "done", "stage": stage, "progress": 1.0})
            return
        views = sum(m["views"] for m in found)
        db.update("sources", sid, {"title": f"Most-clipped moments, {label}", "channel": streamer,
                                   "stats": {"streamer": streamer, "game": game, "moments": len(found),
                                             "views": views, "clips_seen": len(clips)}})
        bus.publish("source", {"id": sid, "title": f"Most-clipped moments, {label}", "channel": streamer})
        bus.note(f"{len(clips)} viewer clips of {streamer} from the {label}: {len(found)} moments, "
                 f"cutting the top {min(target, len(found))}", source_id=sid)

        media: dict[str, dict] = {}
        lock = threading.Lock()

        def urls_for(vid: str) -> dict:
            with lock:
                if vid not in media:
                    page = f"https://www.twitch.tv/videos/{vid}"
                    au, ah = _media(page, "ba/worstaudio/worst")
                    vu, vh = _media(page, VIDEO_FORMAT)
                    media[vid] = {"audio": au, "audio_hdr": ah, "video": vu, "video_hdr": vh}
                return media[vid]

        done = [0]

        def locate(m: dict) -> None:
            u = urls_for(m["video"])
            t0 = max(0.0, m["a"] - 20)
            pcm = _pcm(u["audio"], u["audio_hdr"], t0, m["b"] + 15 - t0)
            m["peak"] = peak_in(m, hype.loudness_db(pcm), t0)
            done[0] += 1
            vod._stage(sid, f"Finding the exact peak of {len(cands)} moments", 0.06 + 0.24 * done[0] / len(cands))

        vod._stage(sid, f"Finding the exact peak of {len(cands)} moments", 0.06)
        with ThreadPoolExecutor(6) as ex:
            for m, err in zip(cands, ex.map(lambda m: _try(locate, m), cands)):
                if err:
                    bus.note(f"Skipped a moment at {int(m['a'] // 3600)}h{int(m['a'] % 3600 // 60):02d} "
                             f"({err[:80]})", "warn", sid)
        cands = [m for m in cands if "peak" in m]
        if cancel_ev.is_set():
            raise InterruptedError
        if not cands:
            raise RuntimeError("Couldn't read any of the broadcasts. They may be subscriber-only or deleted.")

        stamp = int(time.time())

        def session_of(video: str) -> str:  # one per broadcast: two broadcasts' clocks are unrelated
            return f"viral-{sid}-{stamp}-{video}"

        for c in kept:  # clips from an earlier run keep their footage
            pipeline.claim(session_of(str(c["signals"]["viral"]["video"])), c["id"],
                           float(c.get("start") or c["peak"]), float(c.get("end") or c["peak"]))
        queue, made = list(cands), [c["id"] for c in kept]

        kept_ids = {c["id"] for c in kept}

        def ready_count() -> int:  # a kept clip being re-rendered still counts
            if not made:
                return 0
            rows = db.all(f"SELECT id, status FROM clips WHERE id IN ({','.join('?' * len(made))})", made)
            return sum(r["status"] in ("ready", "approved") or (r["id"] in kept_ids and r["status"] not in
                                                                 ("rejected", "failed")) for r in rows)

        while queue and not cancel_ev.is_set():
            need = target - ready_count()
            if need <= 0:
                break
            wave, queue = queue[:need], queue[need:]
            ids = []
            for m in wave:
                session = session_of(m["video"])
                if pipeline.owner_of(session, m["peak"]) is not None:
                    continue
                u = urls_for(m["video"])
                a, b = max(0.0, m["peak"] - pre - 14), m["peak"] + post + 16
                titles = [c["title"] for c in m["clips"] if c["title"]][:6]
                cid = db.insert("clips", {
                    "source_id": sid, "streamer": streamer, "platform": "twitch", "game": game, "peak": m["peak"],
                    "score": 0, "chat_class": reaction_class(titles), "chat": [], "status": "queued",
                    "signals": {"score": round(min(6.0, math.log10(m["views"] + 1) * 1.6), 2),
                                "viral": {"views": m["views"], "clips": len(m["clips"]), "titles": titles,
                                          "video": m["video"], "offset": round(m["a"]),
                                          "length": round(m["b"] - m["a"]),
                                          "url": f"https://clips.twitch.tv/{m['clips'][0]['slug']}"},
                                "raw_start": a, "coarse_peak": m["peak"], "session": session}})
                pipeline.claim(session, cid, m["peak"] - pre, m["peak"] + post)
                ids.append(cid)
                bus.publish("clip", {"id": cid, "status": "queued", "source_id": sid})
                pipeline.submit(cid, lambda d, u=u, a=a, b=b: (vod.cut_section(u["video"], u["video_hdr"], a, b,
                                                                              d / "raw.mp4"), a))
            made += ids
            while ids and not cancel_ev.is_set():
                rows = db.all(f"SELECT status FROM clips WHERE id IN ({','.join('?' * len(made))})", made)
                finished = sum(r["status"] in ("ready", "rejected", "failed", "approved") for r in rows)
                vod._stage(sid, f"Rendering clips {finished}/{len(made)}", 0.3 + 0.7 * finished / max(target, len(made)),
                           "rendering")
                if finished >= len(made):
                    break
                time.sleep(2)
        if cancel_ev.is_set():
            raise InterruptedError
        ok = ready_count()
        db.update("sources", sid, {"status": "done", "stage": f"{ok} clips ready", "progress": 1.0})
        bus.publish("source", {"id": sid, "status": "done", "stage": f"{ok} clips ready", "progress": 1.0})
        bus.note(f"Already viral: {ok} clips from {streamer}'s most-clipped moments", source_id=sid)
    except InterruptedError:
        db.update("sources", sid, {"status": "stopped", "stage": "cancelled"})
        bus.publish("source", {"id": sid, "status": "stopped", "stage": "cancelled"})
    except Exception as e:
        traceback.print_exc()
        db.update("sources", sid, {"status": "error", "error": str(e)[:400], "stage": "failed"})
        bus.publish("source", {"id": sid, "status": "error", "error": str(e)[:400]})
        bus.note(f"Already viral failed: {str(e)[:200]}", "error", sid)
    finally:
        _runs.pop(sid, None)


def _try(fn, arg) -> str | None:
    try:
        fn(arg)
        return None
    except Exception as e:
        return str(e) or type(e).__name__
