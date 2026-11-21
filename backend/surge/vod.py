"""VOD run: audio + chat replay first (tiny downloads), score the whole stream, transcribe only the best windows,
let Claude rank them, then pull just those sections in full quality and render in parallel."""
from __future__ import annotations

import json
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from . import bus, chat, config, db, hype, llm, pipeline, tools, transcribe
from .config import RUNS

_runs: dict[int, threading.Event] = {}


def start(source_id: int) -> None:
    if source_id in _runs:
        return
    ev = threading.Event()
    _runs[source_id] = ev
    threading.Thread(target=_run, args=(source_id, ev), daemon=True, name=f"vod-{source_id}").start()


def cancel(source_id: int) -> None:
    ev = _runs.get(source_id)
    if ev:
        ev.set()


def _stage(sid: int, stage: str, progress: float, status: str = "analyzing") -> None:
    db.update("sources", sid, {"status": status, "stage": stage, "progress": round(progress, 3)})
    bus.publish("source", {"id": sid, "status": status, "stage": stage, "progress": round(progress, 3)})


def _info(url: str) -> dict:
    p = tools.run([tools.ytdlp(), "-J", "--no-warnings", url], timeout=120)
    return json.loads(p.stdout)


def _media_urls(url: str, fmt: str) -> tuple[list[str], dict]:
    p = tools.run([tools.ytdlp(), "-J", "--no-warnings", "-f", fmt, url], timeout=120)
    info = json.loads(p.stdout)
    fmts = info.get("requested_formats") or [info]
    urls = [f["url"] for f in fmts if f.get("url")]
    headers = (fmts[0].get("http_headers") or {}) if fmts else {}
    return urls, headers


def _hdr_args(headers: dict) -> list[str]:
    keep = {k: v for k, v in headers.items() if k.lower() in ("user-agent", "referer", "origin", "cookie")}
    if not keep:
        return []
    return ["-headers", "".join(f"{k}: {v}\r\n" for k, v in keep.items())]


def cut_section(urls: list[str], headers: dict, a: float, b: float, out: Path) -> Path:
    """Cut [a, b] of a broadcast (one combined input, or separate video + audio inputs) into an edit-ready file."""
    inputs = []
    for u in urls:
        inputs += [*_hdr_args(headers), "-ss", f"{a:.2f}", "-i", u]
    maps = ["-map", "0:v:0", "-map", f"{1 if len(urls) > 1 else 0}:a:0?"]
    tools.run([tools.ffmpeg(), "-hide_banner", "-v", "error", "-y", *inputs, *maps, "-t", f"{b - a:.2f}",
               "-c:v", "libx264", "-preset", "ultrafast", "-crf", "16", "-c:a", "aac", "-b:a", "192k", str(out)],
              timeout=900)
    return out


def _run(sid: int, cancel_ev: threading.Event) -> None:
    try:
        src = db.get("sources", sid)
        s = config.load()
        url = src["url"]
        parsed = chat.parse_source(url)
        work = RUNS / f"src{sid:04d}" / "vod"
        work.mkdir(parents=True, exist_ok=True)
        target = int((src.get("settings") or {}).get("target") or s["vod_target"])
        local = parsed["platform"] == "file"

        _stage(sid, "Reading stream info", 0.02)
        if local:
            meta = {"title": Path(url).stem, "uploader": Path(url).stem, "duration": tools.probe(url)["duration"]}
        else:
            meta = _info(url)
        duration = float(meta.get("duration") or 0)
        streamer = meta.get("uploader") or meta.get("channel") or parsed.get("channel") or "streamer"
        game = (src.get("settings") or {}).get("game") or "GTA 6"
        db.update("sources", sid, {"title": meta.get("title"), "channel": streamer, "platform": parsed["platform"],
                                   "stats": {"duration": duration, "streamer": streamer, "game": game}})
        bus.publish("source", {"id": sid, "title": meta.get("title"), "channel": streamer})

        # 1) audio + chat in parallel
        audio_path = work / "audio.pcm"
        msgs: list[tuple] = []

        def get_audio():
            if audio_path.exists() and audio_path.stat().st_size > 1000:
                return
            if local:
                src_in = ["-i", url]
            else:
                # audio-only rendition, 32 fragments at a time: a 7-hour Twitch VOD lands in a couple of minutes
                for old in work.glob("audio_src.*"):
                    old.unlink(missing_ok=True)
                tools.run([tools.ytdlp(), "--no-warnings", "--no-part", "-N", "32", "-f",
                           "ba/worstaudio/worst[height<=360]/worst", "-o", str(work / "audio_src.%(ext)s"), url],
                          timeout=7200)
                got = next(iter(sorted(work.glob("audio_src.*"))), None)
                if not got:
                    raise RuntimeError("Couldn't download the broadcast's audio.")
                src_in = ["-i", str(got)]
            tools.run([tools.ffmpeg(), "-hide_banner", "-v", "error", "-y", *src_in, "-vn", "-ac", "1", "-ar", "16000",
                       "-f", "s16le", str(audio_path)], timeout=7200)

        def get_chat():
            nonlocal msgs
            if parsed["platform"] == "twitch" and parsed.get("video"):
                msgs = chat.twitch_vod_chat(parsed["video"], duration,
                                            lambda f: _stage(sid, "Downloading chat replay + audio", 0.05 + 0.25 * f))
            elif parsed["platform"] == "youtube":
                msgs = chat.youtube_vod_chat(url, work)

        _stage(sid, "Downloading chat replay + audio", 0.05)
        with ThreadPoolExecutor(2) as ex:
            fa, fc = ex.submit(get_audio), ex.submit(get_chat)
            fa.result()
            try:
                fc.result()
            except Exception as e:
                bus.note(f"Chat replay unavailable ({str(e)[:100]}); scoring from audio", "warn", sid)
        if cancel_ev.is_set():
            raise InterruptedError

        # 2) score every second
        _stage(sid, "Scoring every second", 0.32)
        pcm = np.memmap(audio_path, dtype=np.int16, mode="r")
        n = int(len(pcm) // 16000)
        loud = hype.loudness_db(np.asarray(pcm[: n * 16000]))
        n = len(loud)
        lag = float(s["chat_lag"])
        if len(msgs) > 300:  # measure this broadcast's chat delay instead of assuming it
            from . import moment
            counts = np.zeros(n, np.float32)
            for t, *_ in msgs:
                if 0 <= int(t) < n:
                    counts[int(t)] += 1
            got, r = moment.best_chat_lag(counts, loud - np.median(loud), 15)
            if r > 0.08 and 1 <= got <= 14:
                lag = float(got)
                bus.note(f"Chat trails this broadcast by about {got}s (measured)", source_id=sid)
        cw = hype.ChatWindow()
        for t, u, txt, boost in msgs:
            cw.add(int(max(0, t - lag)), u, txt, boost)
        w, ppl = cw.series(0, n)
        z_chat = hype.centered_z(hype.smooth(w, 5))
        z_ppl = hype.centered_z(hype.smooth(ppl, 5))
        z_aud = hype.centered_z(hype.smooth(loud, 2))
        score = hype.fuse(np.minimum(z_chat, 10), np.minimum(z_ppl, 10), np.minimum(z_aud, 8), 0, len(msgs) > 200)
        heat = meta.get("heatmap") if not local else None
        if heat:
            xs = [(h["start_time"] + h["end_time"]) / 2 for h in heat]
            hz = hype.centered_z(np.interp(np.arange(n), xs, [h["value"] for h in heat]).astype(np.float32))
            score = score + 0.6 * np.maximum(hz, 0)
        score = hype.smooth(np.asarray(score, np.float32), 3)
        np.save(work / "score.npy", score)
        n_cand = min(70, max(target * 2, target + 10))
        skip = 90 if duration > 1800 else 0
        window = int(float(s["pre_roll"]) + float(s["post_roll"]) + 8)
        peaks = hype.pick_peaks(score, n_cand, min_gap=max(window, int(s["cooldown"])), skip_start=skip, skip_end=15)
        _publish_timeline(sid, score, peaks)
        if not peaks:
            raise RuntimeError("No moments found (flat audio and no chat).")

        # 3) quick transcript of each candidate window (small model) for ranking
        pre, post = float(s["pre_roll"]), float(s["post_roll"])
        cands = []
        for i, p in enumerate(peaks):
            a, b = max(0, p - pre - 4), min(n, p + post + 4)
            cands.append({"id": f"c{i + 1:02d}", "peak": float(p), "score": float(score[p]), "a": a, "b": b,
                          "chat_class": cw.dominant_class(p - 5, p + 12), "chat": cw.top(p - 5, p + 12, 8),
                          "signals": {"score": round(float(score[p]), 2), "chat": round(float(z_chat[p]), 2),
                                      "people": round(float(z_ppl[p]), 2), "audio": round(float(z_aud[p]), 2)}})
        done = [0]

        def quick(c):
            seg = np.asarray(pcm[int(c["a"] * 16000):int(c["b"] * 16000)])
            c["words"] = transcribe.transcribe(seg, offset=0.0, live=True)
            c["voice"] = hype.voice_weight(transcribe.text_of(c["words"]))
            done[0] += 1
            _stage(sid, f"Listening to {len(cands)} candidate moments", 0.35 + 0.25 * done[0] / len(cands))

        with ThreadPoolExecutor(3) as ex:
            list(ex.map(quick, cands))
        if cancel_ev.is_set():
            raise InterruptedError

        # 4) rank
        _stage(sid, "Ranking moments", 0.62)
        for c in cands:
            c["rank_score"] = c["score"] + 0.35 * min(c["voice"], 6) + (0.8 if c["chat_class"] == "clip" else 0)
        picks = None
        try:
            picks = llm.rank(cands, target, streamer, game)
        except Exception:
            picks = None
        by_id = {c["id"]: c for c in cands}
        if picks:
            chosen = [by_id[p["id"]] for p in picks if p.get("id") in by_id][:target]
            for p in picks:
                if p.get("id") in by_id:
                    by_id[p["id"]]["llm_score"] = p.get("score")
        else:
            chosen = sorted(cands, key=lambda c: -c["rank_score"])[:target]
        chosen.sort(key=lambda c: c["peak"])

        # 5) full-quality sections, rendered in parallel
        _stage(sid, f"Cutting {len(chosen)} clips in full quality", 0.66, "rendering")
        vurls, vheaders = ([], {}) if local else _media_urls(
            url, "bv*[height<=1080][fps<=60]+ba/b[height<=1080]/bv*+ba/b")
        clip_ids = []
        session = f"vod-{sid}-{int(time.time())}"
        for c in chosen:
            if pipeline.owner_of(session, c["peak"]) is not None:
                continue
            cid = db.insert("clips", {
                "source_id": sid, "streamer": streamer, "platform": parsed["platform"], "game": game,
                "peak": c["peak"], "score": 0, "chat_class": c["chat_class"], "chat": c["chat"], "status": "queued",
                "signals": {**c["signals"], "raw_start": max(0.0, c["peak"] - pre - 14), "session": session}})
            pipeline.claim(session, cid, c["peak"] - pre, c["peak"] + post)
            clip_ids.append(cid)
            bus.publish("clip", {"id": cid, "status": "queued", "source_id": sid})
            a, b = max(0.0, c["peak"] - pre - 14), min(duration or n, c["peak"] + post + 16)

            def make_raw(d: Path, a=a, b=b) -> tuple[Path, float]:
                return cut_section([url] if local else vurls, {} if local else vheaders, a, b, d / "raw.mp4"), a

            pipeline.submit(cid, make_raw)

        # 6) wait for the batch
        t0 = time.time()
        while not cancel_ev.is_set():
            rows = db.all(f"SELECT status FROM clips WHERE id IN ({','.join('?' * len(clip_ids))})", clip_ids)
            finished = sum(r["status"] in ("ready", "rejected", "failed", "approved") for r in rows)
            _stage(sid, f"Rendering clips {finished}/{len(clip_ids)}", 0.66 + 0.34 * finished / len(clip_ids),
                   "rendering")
            if finished >= len(clip_ids):
                break
            time.sleep(2)
        ok = db.scalar(f"SELECT COUNT(*) FROM clips WHERE status IN ('ready','approved') AND id IN "
                       f"({','.join('?' * len(clip_ids))})", clip_ids)
        db.update("sources", sid, {"status": "done", "stage": f"{ok} clips ready", "progress": 1.0})
        bus.publish("source", {"id": sid, "status": "done", "stage": f"{ok} clips ready", "progress": 1.0})
        bus.note(f"VOD done: {ok} clips from {streamer} in {(time.time() - t0) / 60:.1f} min of rendering", source_id=sid)
    except InterruptedError:
        db.update("sources", sid, {"status": "stopped", "stage": "cancelled"})
        bus.publish("source", {"id": sid, "status": "stopped", "stage": "cancelled"})
    except Exception as e:
        traceback.print_exc()
        db.update("sources", sid, {"status": "error", "error": str(e)[:400], "stage": "failed"})
        bus.publish("source", {"id": sid, "status": "error", "error": str(e)[:400]})
        bus.note(f"VOD failed: {str(e)[:200]}", "error", sid)
    finally:
        _runs.pop(sid, None)


def _publish_timeline(sid: int, score: np.ndarray, peaks: list[int]) -> None:
    """Downsampled excitement curve for the UI (max 600 points)."""
    n = len(score)
    step = max(1, n // 600)
    curve = [round(float(score[i:i + step].max()), 2) for i in range(0, n, step)]
    st = (db.get("sources", sid) or {}).get("stats") or {}
    st.update({"curve": curve, "step": step, "peaks": peaks})
    db.update("sources", sid, {"stats": st})
    bus.publish("source", {"id": sid, "stats": st})
