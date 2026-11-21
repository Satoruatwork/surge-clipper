"""Clip jobs: raw window -> accurate transcript -> smart boundaries -> AI copy -> render -> ready.
Shared by live monitors and VOD runs; runs N jobs in parallel."""
from __future__ import annotations

import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

import numpy as np

from . import bus, config, db, facecam, fx, llm, moment, render, tools, transcribe
from .config import CLIPS

_pool: ThreadPoolExecutor | None = None
_pool_lock = threading.Lock()
_cam_lock = threading.Lock()
_on_ready: list[Callable[[int], None]] = []

# Footage each clip owns, per recording session: {session: {clip_id: (start, end)}} in session seconds.
_claims: dict[str, dict[int, tuple[float, float]]] = {}
_claims_lock = threading.Lock()

# Raw maker: returns (raw_path, raw_start_abs) for a clip; raw covers the window plus margins.
RawMaker = Callable[[Path], tuple[Path, float]]


def pool() -> ThreadPoolExecutor:
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = ThreadPoolExecutor(max_workers=max(1, int(config.get("render_workers") or 3)),
                                       thread_name_prefix="clip")
        return _pool


def on_ready(fn: Callable[[int], None]) -> None:
    _on_ready.append(fn)


def set_status(clip_id: int, status: str, **extra) -> None:
    db.update("clips", clip_id, {"status": status, **extra})
    bus.publish("clip", {"id": clip_id, "status": status, **{k: v for k, v in extra.items()
                                                            if k in ("score", "title", "hook", "thumb", "error")}})


def clip_dir(clip_id: int) -> Path:
    d = CLIPS / f"{clip_id:06d}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def refine(words: list[dict], peak: float, pre: float, post: float, lo: float, hi: float,
           total: float, settle: float | None = None) -> tuple[float, float]:
    """`peak` is the exact start of the moment. Open on the sentence that sets it up (6 s up to `pre` before),
    close once the reaction has died down (`settle`), finishing the sentence in progress, never mid-word."""
    setup_min = min(6.0, pre)
    sents = transcribe.sentences(words)
    target = peak - max(setup_min, pre * 0.75)
    starts = [s for s, e, _ in sents if peak - pre - 3 <= s <= peak - setup_min]
    start = (min(starts, key=lambda s: abs(s - target)) - 0.2) if starts else max(0.0, peak - pre)
    inside = [w for w in words if w["s"] <= start < w["e"]]
    if inside:  # never open mid-word
        start = inside[0]["s"] - 0.15
    first = next((w for w in words if w["s"] >= start), None)  # dead air at the top costs retention
    if first and first["s"] - start > 1.5 and peak - (first["s"] - 0.4) >= setup_min:
        start = first["s"] - 0.4
    want = settle if settle is not None else peak + post
    want = min(max(want, peak + 2.5), peak + max(post * 1.8, 10.0))
    running = [e for s, e, _ in sents if s <= want <= e + 0.25]
    end = (running[0] + 0.35) if running else want + 0.3
    inside = [w for w in words if w["s"] < end < w["e"]]
    if inside:  # never close mid-word
        end = inside[-1]["e"] + 0.2
    start, end = max(0.0, start), min(total, end)

    def safe_end(t: float) -> float:  # step out of a word the cut would land in
        hit = [w for w in words if w["s"] < t < w["e"]]
        return min(total, hit[-1]["e"] + 0.2) if hit else t

    def safe_start(t: float) -> float:
        hit = [w for w in words if w["s"] < t < w["e"]]
        return max(0.0, hit[0]["s"] - 0.15) if hit else t

    if end - start < lo:
        # too short: keep what happens next (the aftermath), ending where a sentence ends
        later = sorted(e + 0.35 for _s, e, _t in sents
                       if end < e + 0.35 <= total and lo <= e + 0.35 - start <= hi)
        end = later[0] if later else safe_end(min(total, start + lo))
        if end - start < lo:  # no more footage after the moment: open earlier, on a sentence start
            earlier = [s0 - 0.2 for s0, _e, _t in sents if s0 - 0.2 < start and lo <= end - (s0 - 0.2) <= hi]
            start = max(earlier) if earlier else safe_start(max(0.0, end - lo))
    if end - start > hi:
        # too long: open later on a sentence start, keeping at least 4 s of setup before the moment
        later_starts = [s0 - 0.2 for s0, _e, _t in sents if start < s0 - 0.2 <= peak - 4 and end - (s0 - 0.2) <= hi]
        if later_starts:
            start = min(later_starts)
        if end - start > hi:
            cut = safe_end(start + hi)
            end = cut if cut - start <= hi + 0.6 else start + hi
    return round(max(0.0, start), 3), round(min(total, end), 3)


def resolve_cam(source: dict, raw: Path, at: float, size: tuple[int, int]) -> tuple[list[int] | None, str, float | None]:
    """Returns (cam box, how it was found, focus override). A manual box wins; otherwise every clip is checked,
    because streams switch scenes: gameplay with a webcam overlay, then a full-camera chat segment."""
    st = source.get("settings") or {}
    if st.get("cam_mode") == "none":
        return None, "none", None
    if st.get("cam"):
        return st["cam"], "manual", None
    aspect = render.W / render.seam_y()
    # every overlay this source has shown cleanly (streamers move the cam between and during streams), the last
    # one, and a box you drew for this streamer elsewhere: facecam keeps whichever fits this clip's footage
    known = [b for b in [*(st.get("cam_seen") or []), st.get("cam_auto"), saved_cam(source, size)] if b]
    found = facecam.find(raw, [at + d for d in (-8, -4, 0, 3, 6, 9)], size, aspect, known)
    if found["kind"] == "fullcam" and found["face"]:
        x, _, w, _ = found["face"]
        return None, "fullcam", min(1.0, max(0.0, (x + w / 2) / size[0]))
    with _cam_lock:
        fresh = db.get("sources", source["id"]) or source
        st = fresh.get("settings") or {}
        if found["kind"] == "cam":
            clean = found.get("how") in ("border", "known") and found.get("fit", 0) >= 0.9
            if clean and (st.get("cam_auto") != found["box"] or found["box"] not in (st.get("cam_seen") or [])):
                first = not st.get("cam_auto")
                st["cam_auto"] = found["box"]
                st["cam_seen"] = [b for b in (st.get("cam_seen") or []) if b != found["box"]][-5:] + [found["box"]]
                st["cam_kind"] = "cam"
                db.update("sources", source["id"], {"settings": st})
                bus.publish("source", {"id": source["id"], "settings": st})
                if first:
                    bus.note(f"Facecam found for {fresh.get('channel')}", source_id=source["id"])
            return found["box"], "auto" if found.get("how") == "border" else found.get("how", "auto"), None
        if st.get("cam_auto"):  # face turned away this clip: the overlay is still where it was
            return st["cam_auto"], "cached", None
    return None, "none", None


def saved_cam(source: dict, size: tuple[int, int]) -> list[int] | None:
    """A facecam box you set for this streamer on another row (their live row, an earlier broadcast) at the same
    frame size: a candidate when the overlay's border can't be traced in a clip (facecam checks it still fits)."""
    chan = (source.get("channel") or "").lower()
    if not chan:
        return None
    for other in db.all("SELECT * FROM sources WHERE id != ? AND lower(channel) = ? ORDER BY updated_at DESC",
                        (source.get("id") or 0, chan)):
        st = other.get("settings") or {}
        frame = st.get("frame")
        if st.get("cam") and st.get("cam_mode") != "none" and (not frame or tuple(frame) == tuple(size)):
            return st["cam"]
    return None


def pick_layout(source: dict, cam: list[int] | None, how: str = "") -> str:
    want = (source.get("settings") or {}).get("layout") or config.get("layout")
    if want != "auto":
        return want if (want not in ("split", "pip") or cam) else ("full" if how == "fullcam" else "blur")
    if cam:
        return "split"
    return "full" if how == "fullcam" else "blur"


def owner_of(session: str, t: float, pad: float = 2.0) -> int | None:
    """The clip that already holds second `t` of this session, if any."""
    with _claims_lock:
        for cid, (a, b) in _claims.get(session, {}).items():
            if a - pad <= t <= b + pad:
                return cid
    return None


def claim(session: str, clip_id: int, a: float, b: float) -> None:
    with _claims_lock:
        _claims.setdefault(session, {})[clip_id] = (a, b)


def release(session: str | None, clip_id: int) -> None:
    if session:
        with _claims_lock:
            _claims.get(session, {}).pop(clip_id, None)


def settle(session: str, clip_id: int, a: float, b: float, peak: float, min_len: float) -> tuple[float, float] | int:
    """Final window that shares no footage with any other clip of the session: trimmed against neighbours.
    Returns the window, or the id of the clip that already owns this moment."""
    with _claims_lock:
        others = {c: w for c, w in _claims.get(session, {}).items() if c != clip_id}
        for cid, (oa, ob) in others.items():
            if oa <= peak <= ob:
                return cid
            if ob <= peak and ob > a:
                a = ob + 0.05
            if oa >= peak and oa < b:
                b = oa - 0.05
        if b - a < min_len and others:
            return min(others, key=lambda c: min(abs(others[c][0] - peak), abs(others[c][1] - peak)))
        _claims.setdefault(session, {})[clip_id] = (a, b)
        return a, b


def black_until(raw: Path, a: float, limit: float, step: float = 0.5) -> float:
    """First second from `a` where the gameplay isn't a black loading or transition screen, or `limit` if it stays
    black. Only the middle of the frame counts: that's game, wherever the facecam sits on the edges. ffmpeg reads
    a tiny grey copy (the installed app ships OpenCV without its video reader)."""
    if limit <= a:
        return a
    w, h = 64, 36
    p = tools.run([tools.ffmpeg(), "-hide_banner", "-v", "error", "-ss", f"{a:.2f}", "-i", str(raw),
                   "-t", f"{limit - a:.2f}", "-an", "-vf",
                   f"fps={1 / step:g},crop=iw/2:ih*2/3:iw/4:ih/6,scale={w}:{h},format=gray",
                   "-f", "rawvideo", "-"], timeout=120, check=False)
    pix = np.frombuffer(p.stdout or b"", np.uint8)
    n = len(pix) // (w * h)
    if p.returncode != 0 or n == 0:
        return a  # can't tell: leave the cut alone
    lit = np.nonzero(pix[:n * w * h].reshape(n, w * h).mean(axis=1) > 16)[0]
    return a + float(lit[0]) * step if len(lit) else limit


def viral_gate(clip: dict, env, words: list[dict], start: float, end: float) -> str | None:
    """Already-viral moments are other people's picks: drop the ones that can't work as a captioned Short."""
    if not (clip.get("signals") or {}).get("viral"):
        return None
    if env is not None and len(env):
        run = longest = 0
        for quiet in env[int(start / moment.HOP):int(end / moment.HOP)] < -55:
            run = run + 1 if quiet else 0
            longest = max(longest, run)
        if longest * moment.HOP >= 6:
            return "Twitch muted the audio in this part of the broadcast"
    if sum(1 for w in words if start <= w["s"] and w["e"] <= end) < 15:
        return "almost nothing is said, so there's nothing to caption"
    return None


def submit(clip_id: int, make_raw: RawMaker) -> None:
    pool().submit(_run, clip_id, make_raw)


def _run(clip_id: int, make_raw: RawMaker) -> None:
    t0 = time.time()
    try:
        clip = db.get("clips", clip_id)
        source = db.get("sources", clip["source_id"]) or {"id": 0, "settings": {}}
        s = config.load()
        d = clip_dir(clip_id)
        set_status(clip_id, "cutting")
        raw, raw_start = make_raw(d)
        info = tools.probe(raw)
        total = info["duration"]
        peak = clip["peak"] - raw_start

        set_status(clip_id, "transcribing")
        prompt = f"{clip.get('streamer') or ''} playing {clip.get('game') or 'GTA 6'}."
        words = transcribe.transcribe(raw, offset=0.0, prompt=prompt) if info["has_audio"] else []

        env = None
        if info["has_audio"]:  # land on the real moment, not the 1-second chat/loudness estimate
            env = moment.envelope(transcribe.decode(raw))
            sig = dict(clip.get("signals") or {})
            coarse_abs = float(sig.setdefault("coarse_peak", clip["peak"]))  # always search from the first estimate
            peak = moment.onset(env, coarse_abs - raw_start)
            settled_at = moment.decay(env, peak, 2.5, max(15.0, float(s["post_roll"]) * 1.8))
            db.update("clips", clip_id, {"peak": raw_start + peak, "signals": sig})
            clip["peak"] = raw_start + peak
            clip["signals"] = sig
        else:
            settled_at = None
        start, end = refine(words, peak, float(s["pre_roll"]), float(s["post_roll"]),
                            float(s["clip_min"]), float(s["clip_max"]), total, settled_at)
        problem = viral_gate(clip, env, words, start, end)
        if problem:
            release((clip.get("signals") or {}).get("session"), clip_id)
            set_status(clip_id, "rejected", error=problem)
            bus.note(f"Clip #{clip_id} skipped: {problem}", source_id=source.get("id"))
            return
        clear = black_until(raw, start, peak + 1.0)
        if clear >= peak - 1.0:  # the reaction happened over a loading screen: nothing to show
            release((clip.get("signals") or {}).get("session"), clip_id)
            set_status(clip_id, "rejected", error="the game screen is black at the moment")
            bus.note(f"Clip #{clip_id} skipped: the game screen is black at the moment", source_id=source.get("id"))
            return
        if clear > start + 0.4:  # never open on a black loading screen: it loses people in the first second
            nxt = next((w["s"] for w in words if w["s"] >= clear), None)
            start = nxt - 0.15 if nxt is not None and nxt - clear < 1.5 else clear
            if end - start < float(s["clip_min"]):
                end = min(total, start + float(s["clip_min"]))
                hit = [w for w in words if w["s"] < end < w["e"]]
                end = min(total, hit[-1]["e"] + 0.2) if hit else end
        set_status(clip_id, "writing")
        rel = [w for w in words if start - 0.05 <= w["s"] and w["e"] <= end + 0.3]
        meta = llm.write_clip({
            "viewer_titles": ((clip.get("signals") or {}).get("viral") or {}).get("titles") or [],
            "streamer": clip.get("streamer"), "platform": clip.get("platform"), "game": clip.get("game"),
            "duration": end - start, "chat_class": clip.get("chat_class"), "chat": clip.get("chat") or [],
            "strength": round((clip.get("signals") or {}).get("score", 0), 1), "peak": clip["peak"],
            "peak_rel": peak - start, "fx_vocab": fx.vocabulary(),
            "fx_budget": fx.LEVEL_CUES.get(s.get("fx_level") or "medium", 4),
            "words": [{**w, "s": w["s"] - start, "e": w["e"] - start} for w in rel]})
        ts, te = float(meta.get("trim_start") or 0), float(meta.get("trim_end") or 0)
        lo_len = float(s["clip_min"])
        if 0 < ts < (end - start) * 0.4 and start + ts < peak - 2 and end - start - ts >= lo_len:
            start += ts
        if 0 < te < (end - start) * 0.4 and end - te > peak + 1.5 and end - start - te >= lo_len:
            end -= te
        if end - start < float(s["clip_min"]) * 0.8:
            start, end = refine(words, peak, float(s["pre_roll"]), float(s["post_roll"]),
                                float(s["clip_min"]), float(s["clip_max"]), total, settled_at)
        session = (clip.get("signals") or {}).get("session")
        if session:
            got = settle(session, clip_id, raw_start + start, raw_start + end, clip["peak"],
                         float(s["clip_min"]) * 0.75)
            if isinstance(got, int):
                release(session, clip_id)
                set_status(clip_id, "rejected", error=f"Same moment as clip #{got}")
                bus.note(f"Clip #{clip_id} skipped: same moment as clip #{got}", source_id=source.get("id"))
                return
            start, end = got[0] - raw_start, got[1] - raw_start
        rel = [{**w, "s": round(w["s"] - start, 3), "e": round(w["e"] - start, 3)}
               for w in words if start - 0.05 <= w["s"] and w["e"] <= end + 0.3]

        cam, cam_src, face_focus = resolve_cam(source, raw, peak, (info["width"], info["height"]))
        layout = clip.get("layout") or pick_layout(source, cam, cam_src)
        focus = face_focus if face_focus is not None else float((source.get("settings") or {}).get("focus", 0.5))
        tag = tag_for(clip)
        dur = end - start
        plan = None
        if fx.is_on(s.get("fx_level")):
            plan = fx.clean({**meta["fx"], "source": "ai"}, dur) if isinstance(meta.get("fx"), dict) else None
            if not plan or not plan["cues"]:
                plan = fx.heuristic(rel, dur, peak - start, clip.get("chat_class"), transcribe.text_of(rel),
                                    salt=clip_id)
        plan = fx.apply_ratio(plan, clip_id)
        res = fx.resolve(plan, rel, dur, peak - start, clip_id) if plan else None
        if plan and res:
            plan["used_sounds"] = [x["name"] for x in res.get("sounds") or []]
        if plan and res and res.get("music"):
            plan["credit"] = res["music"].get("credit")
        set_status(clip_id, "rendering", hook=meta["hook"], title=meta["title"], score=meta.get("score", 0))
        out = d / "clip.mp4"
        shift = float((source.get("settings") or {}).get("audio_shift_ms") or 0) / 1000
        render.render(raw, start, dur, out, rel, layout, cam, hook=meta["hook"], tag=tag,
                      style=clip.get("caption_style") or s["caption_style"], focus=focus, has_audio=info["has_audio"],
                      fx_res=res, audio_shift=shift)
        thumb = render.thumbnail(out, min(end - start - 0.5, max(0.5, peak - start)), d / "thumb.jpg")
        db.update("clips", clip_id, {
            "raw_path": str(raw), "path": str(out), "thumb": str(thumb), "start": raw_start + start,
            "end": raw_start + end, "duration": round(end - start, 2), "words": rel,
            "transcript": transcribe.text_of(rel), "hook": meta["hook"], "title": meta["title"],
            "description": meta.get("description", ""), "hashtags": meta.get("hashtags", []),
            "variants": meta.get("variants", []), "kind": meta.get("kind"), "spoiler": int(bool(meta.get("spoiler"))),
            "score": float(meta.get("score") or 0), "layout": layout, "cam": cam,
            "caption_style": clip.get("caption_style") or s["caption_style"],
            "signals": {**(clip.get("signals") or {}), "raw_start": raw_start, "focus": round(focus, 3)},
            "fx": plan or {}})
        # viewers already proved an already-viral moment (often a reaction to a story scene), so it's never auto-dropped
        keep = bool((clip.get("signals") or {}).get("viral")) or (
            meta.get("keep", True) and meta.get("kind") not in ("cutscene", "in-game-tv"))
        set_status(clip_id, "ready" if keep else "rejected", score=float(meta.get("score") or 0),
                   thumb=str(thumb), title=meta["title"], hook=meta["hook"])
        bus.note(f"Clip #{clip_id} ready in {time.time() - t0:.0f}s · {meta['title'][:70]} "
                 f"(score {meta.get('score')}, {meta.get('source')}, cam {cam_src})", source_id=source.get("id"))
        for fn in _on_ready:
            try:
                fn(clip_id)
            except Exception:
                traceback.print_exc()
    except Exception as e:
        traceback.print_exc()
        try:
            release(((db.get("clips", clip_id) or {}).get("signals") or {}).get("session"), clip_id)
        except Exception:
            pass
        set_status(clip_id, "failed", error=str(e)[:500])
        bus.note(f"Clip #{clip_id} failed: {str(e)[:200]}", "error")


def rerender(clip_id: int, changes: dict) -> None:
    """Re-render an existing clip after edits (words, hook, layout, style, trims) without re-transcribing."""
    def job():
        try:
            clip = db.get("clips", clip_id)
            source = db.get("sources", clip["source_id"]) or {"settings": {}}
            raw = Path(clip["raw_path"])
            info = tools.probe(raw)
            raw_start = float((clip.get("signals") or {}).get("raw_start", 0.0))
            start = clip["start"] - raw_start + float(changes.get("trim_start", 0))
            end = clip["end"] - raw_start - float(changes.get("trim_end", 0))
            words = changes.get("words") or clip["words"]
            shift = float(changes.get("trim_start", 0))
            words = [{**w, "s": round(w["s"] - shift, 3), "e": round(w["e"] - shift, 3)} for w in words
                     if w["s"] - shift >= -0.05 and w["e"] - shift <= end - start + 0.3]
            layout = changes.get("layout") or clip["layout"]
            cam = changes.get("cam", clip.get("cam"))
            style = changes.get("caption_style") or clip["caption_style"]
            hook = changes.get("hook", clip["hook"])
            dur = end - start
            peak_rel = clip["peak"] - (raw_start + start)
            plan = fx.clean(changes["fx"], dur) if "fx" in changes else fx.clean(clip.get("fx") or None, dur)
            if plan and shift:
                plan["cues"] = [{**c, "t": round(c["t"] - shift, 2)} for c in plan["cues"] if c["t"] - shift >= 0]
            res = fx.resolve(plan, words, dur, peak_rel, clip_id) if plan else None
            if plan:
                plan["credit"] = (res or {}).get("music", {}).get("credit") if (res or {}).get("music") else None
            set_status(clip_id, "rendering")
            d = clip_dir(clip_id)
            out = d / f"clip_v{int(time.time())}.mp4"
            tag = tag_for(clip)
            render.render(raw, start, end - start, out, words, layout, cam, hook=hook, tag=tag, style=style, fx_res=res,
                          audio_shift=float(changes.get("audio_shift_ms", (source.get("settings") or {}).get(
                              "audio_shift_ms") or 0)) / 1000,
                          focus=float(changes.get("focus", (clip.get("signals") or {}).get(
                              "focus", (source.get("settings") or {}).get("focus", 0.5)))), has_audio=info["has_audio"])
            old = clip.get("path")
            thumb = render.thumbnail(out, min(end - start - 0.5, 3.0), d / f"thumb_{int(time.time())}.jpg")
            db.update("clips", clip_id, {"path": str(out), "thumb": str(thumb), "words": words, "hook": hook,
                                         "layout": layout, "cam": cam, "caption_style": style,
                                         "start": raw_start + start, "end": raw_start + end,
                                         "duration": round(end - start, 2), "transcript": transcribe.text_of(words),
                                         "fx": plan or {}})
            if old and Path(old).exists() and Path(old) != out:
                Path(old).unlink(missing_ok=True)
            set_status(clip_id, "ready", thumb=str(thumb), hook=hook)
        except Exception as e:
            traceback.print_exc()
            set_status(clip_id, "failed", error=str(e)[:500])
    pool().submit(job)


def tag_for(clip: dict) -> str:
    name = clip.get("streamer")
    if not name:
        return ""
    return {"twitch": f"twitch.tv/{name}", "kick": f"kick.com/{name}",
            "youtube": f"youtube.com/{name if name.startswith('@') else '@' + name}"}.get(clip.get("platform") or "", name)
