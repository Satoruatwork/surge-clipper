"""Live monitor: records a stream into a 2-second segment ring buffer, listens to its audio and chat in real time,
scores excitement every second, and cuts a clip the moment a spike settles."""
from __future__ import annotations

import csv
import json
import shutil
import subprocess
import threading
import time
import traceback
from collections import deque
from pathlib import Path

import numpy as np

from . import bus, chat, config, db, hype, moment, pipeline, render, tools, transcribe
from .config import RUNS

SR = 16000
SEG = 2
_monitors: dict[int, "LiveMonitor"] = {}
_reg_lock = threading.Lock()


def running() -> dict[int, "LiveMonitor"]:
    return dict(_monitors)


def start(source_id: int) -> None:
    with _reg_lock:
        m = _monitors.get(source_id)
        if m and m.is_alive():
            return
        m = LiveMonitor(source_id)
        _monitors[source_id] = m
        m.start()


def stop(source_id: int) -> None:
    with _reg_lock:
        m = _monitors.pop(source_id, None)
    if m:
        m.halt()
    else:
        db.update("sources", source_id, {"status": "stopped"})


def stop_all() -> None:
    for sid in list(_monitors):
        stop(sid)


def pulse_history(source_id: int) -> list[dict]:
    m = _monitors.get(source_id)
    return list(m.pulses) if m else []


class LiveMonitor(threading.Thread):
    def __init__(self, source_id: int):
        super().__init__(daemon=True, name=f"live-{source_id}")
        self.sid = source_id
        self.halt_ev = threading.Event()
        self.pulses: deque = deque(maxlen=900)
        self.procs: list[subprocess.Popen] = []

    # ------------------------------------------------------------ lifecycle
    def halt(self) -> None:
        self.halt_ev.set()
        self._kill()

    def _kill(self) -> None:
        for p in self.procs:
            try:
                p.terminate()
            except Exception:
                pass
        for p in self.procs:
            try:
                p.wait(5)
            except Exception:
                p.kill()
        self.procs = []

    def _status(self, status: str, **extra) -> None:
        db.update("sources", self.sid, {"status": status, **extra})
        bus.publish("source", {"id": self.sid, "status": status, **extra})

    def run(self) -> None:
        src = db.get("sources", self.sid)
        self.url = src["url"]
        self.info = chat.parse_source(self.url)
        self.base = RUNS / f"src{self.sid:04d}"
        self.base.mkdir(parents=True, exist_ok=True)
        epoch = 0
        while not self.halt_ev.is_set():
            meta = self._metadata()
            if meta is None:
                self._status("offline", stage="waiting for the stream to go live")
                if self.halt_ev.wait(60):
                    break
                continue
            epoch += 1
            try:
                self._session(epoch, meta)
            except Exception as e:
                traceback.print_exc()
                bus.note(f"{self.info.get('channel')}: recorder error {e}", "error", self.sid)
            self._kill()
            if not self.halt_ev.is_set():
                self._status("reconnecting", stage="stream dropped, reconnecting")
                self.halt_ev.wait(5)
        self._kill()
        self._status("stopped", stage=None)

    def _metadata(self) -> dict | None:
        p = tools.run([tools.streamlink(), "--json", self.url], timeout=60, check=False)
        try:
            data = json.loads(p.stdout or b"{}")
        except json.JSONDecodeError:
            data = {}
        if data.get("error") or not data.get("streams"):
            return None
        m = data.get("metadata") or {}
        title = m.get("title") or ""
        game = m.get("category") or ""
        streamer = m.get("author") or self.info.get("channel") or ""
        db.update("sources", self.sid, {"title": title, "channel": streamer, "platform": self.info["platform"]})
        return {"title": title, "game": game, "streamer": streamer}

    # ------------------------------------------------------------ one recording session
    def _session(self, epoch: int, meta: dict) -> None:
        s = config.load()
        work = self.base / f"e{epoch:03d}"
        if work.exists():
            shutil.rmtree(work, ignore_errors=True)
        segdir = work / "seg"
        segdir.mkdir(parents=True)
        self.streamer = meta["streamer"]
        self.game = meta["game"] or "GTA 6"
        self.session = f"live-{self.sid}-{int(time.time())}"

        sl_args = [tools.streamlink(), "--stdout", "--hls-live-edge", "3", "--stream-segment-threads", "2",
                   "--retry-open", "2"]
        if self.info["platform"] == "twitch":
            sl_args += ["--twitch-disable-ads"]
        sl = tools.popen([*sl_args, self.url, "1080p60,1080p,best"], stdout=subprocess.PIPE,
                         stderr=open(work / "streamlink.log", "wb"))
        ff = tools.popen([tools.ffmpeg(), "-hide_banner", "-v", "error", "-fflags", "+genpts", "-i", "pipe:0",
                          "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy", "-f", "segment",
                          "-segment_time", str(SEG), "-segment_format", "mpegts",
                          "-segment_list", str(work / "seg.csv"), "-segment_list_type", "csv",
                          "-segment_list_flags", "live", str(segdir / "s%06d.ts"),
                          "-map", "0:a:0", "-ac", "1", "-ar", str(SR), "-f", "s16le", "pipe:1"],
                         stdin=sl.stdout, stdout=subprocess.PIPE, stderr=open(work / "ffmpeg.log", "wb"))
        sl.stdout.close()
        self.procs = [sl, ff]

        # shared state
        self.work, self.segdir = work, segdir
        self.lock = threading.Lock()
        self.pcm = bytearray()
        self.pcm_base = 0            # sample index of pcm[0]
        self.samples = 0             # total samples received
        self.clock: deque = deque(maxlen=200)  # (wall, media seconds)
        self.loud: list[float] = []
        self.words: list[dict] = []
        self.cw = hype.ChatWindow()
        self.chat_lag = float(s["chat_lag"])
        self.started_wall = time.time()
        self.clips_made = 0

        stop = threading.Event()
        threads = [threading.Thread(target=self._read_pcm, args=(ff, stop), daemon=True),
                   threading.Thread(target=self._listen, args=(stop,), daemon=True)]
        for t in threads:
            t.start()
        chat.start_live(self.info, self.url, work, self._on_chat, stop)
        self._status("live", stage="watching", error=None)
        bus.note(f"Watching {self.streamer} live · {self.game}", source_id=self.sid)
        try:
            self._analyze(ff, stop)
        finally:
            stop.set()

    # ------------------------------------------------------------ inputs
    def media_now(self) -> float:
        return self.samples / SR

    def media_at(self, wall: float) -> float:
        with self.lock:
            if not self.clock:
                return 0.0
            w0, m0 = self.clock[-1]
        return m0 + (wall - w0)

    def _read_pcm(self, ff: subprocess.Popen, stop: threading.Event) -> None:
        carry = b""
        while not stop.is_set():
            chunk = ff.stdout.read(SR * 2 // 4)  # 250 ms
            if not chunk:
                break
            data = carry + chunk
            usable = len(data) - len(data) % 2
            data, carry = data[:usable], data[usable:]
            with self.lock:
                self.pcm += data
                self.samples += len(data) // 2
                self.clock.append((time.time(), self.samples / SR))
                full = self.samples // SR
                while len(self.loud) < full:
                    i = len(self.loud)
                    a = (i * SR - self.pcm_base) * 2
                    sec = np.frombuffer(bytes(self.pcm[a:a + SR * 2]), np.int16)
                    self.loud.append(float(hype.loudness_db(sec)[0]) if len(sec) == SR else -60.0)
                keep = SR * 2 * 180  # keep 3 minutes of PCM
                if len(self.pcm) > keep * 1.2:
                    drop = len(self.pcm) - keep
                    drop -= drop % 2
                    del self.pcm[:drop]
                    self.pcm_base += drop // 2

    def _on_chat(self, wall: float, user: str, text: str, boost: float) -> None:
        if self.samples == 0:
            return
        sec = int(self.media_at(wall))  # when it arrived; the measured chat delay is applied when reading
        with self.lock:
            self.cw.add(max(0, sec), user, text, boost)

    def _listen(self, stop: threading.Event) -> None:
        """Rolling live transcription: feeds the voice signal and the UI's live transcript."""
        last_end = 0.0
        while not stop.is_set():
            if stop.wait(4.0):
                break
            now = self.media_now()
            if now < 6:
                continue
            a = max(last_end - 1.0, now - 10.0)
            with self.lock:
                i0 = int(a * SR) - self.pcm_base
                i1 = int(now * SR) - self.pcm_base
                if i0 < 0:
                    i0 = 0
                pcm = np.frombuffer(bytes(self.pcm[i0 * 2:i1 * 2]), np.int16).copy()
            if len(pcm) < SR * 2:
                continue
            try:
                new = transcribe.transcribe(pcm, offset=a, live=True)
            except Exception as e:
                bus.note(f"live transcription error: {e}", "warn", self.sid)
                continue
            new = [w for w in new if w["s"] >= last_end - 0.05]
            if new:
                last_end = new[-1]["e"]
                with self.lock:
                    self.words.extend(new)
                    cutoff = now - 900
                    while self.words and self.words[0]["e"] < cutoff:
                        self.words.pop(0)
                bus.publish("transcript", {"id": self.sid, "text": transcribe.text_of(new)[-220:], "t": now})

    # ------------------------------------------------------------ scoring loop
    def _analyze(self, ff: subprocess.Popen, stop: threading.Event) -> None:
        s = config.load()
        chat_hist, people_hist, audio_hist = [], [], []
        evaluated = 0
        last_trigger = -1e9
        pending: dict | None = None
        waiting: list[dict] = []
        adapt = 1.0
        last_thumb = 0.0
        last_stats = 0.0
        last_adapt = time.time()
        sens = float(s["sensitivity"])
        cooldown = float(s["cooldown"])
        pre, post = float(s["pre_roll"]), float(s["post_roll"])
        target = max(1.0, float(s["target_per_hour"]))
        last_lag = time.time()
        while not self.halt_ev.is_set():
            if ff.poll() is not None:
                bus.note(f"{self.streamer}: stream ended or dropped", "warn", self.sid)
                return
            time.sleep(0.5)
            now = self.media_now()
            L = int(round(self.chat_lag))
            upto = int(now - L - 2)
            while evaluated < upto and evaluated < len(self.loud):
                t = evaluated
                with self.lock:
                    w5, u5 = self.cw.series(t + L - 4, t + L + 1)
                    chat_hist.append(float(w5.sum()))
                    people_hist.append(float(len(set().union(*[self.cw.users.get(x, set())
                                                                 for x in range(t + L - 4, t + L + 1)]))))
                    voice = sum(hype.voice_weight(w["w"]) for w in self.words if t - 4 <= w["s"] <= t + 1)
                aud = float(np.mean(self.loud[max(0, t - 1):t + 1]))
                audio_hist.append(aud)
                zc = hype.robust_z(chat_hist[-1], np.array(chat_hist[-241:-1]))
                zp = hype.robust_z(people_hist[-1], np.array(people_hist[-241:-1]))
                za = hype.robust_z(aud, np.array(audio_hist[-301:-1]))
                zv = min(4.0, voice)
                has_chat = self.cw.total > 30
                score = float(hype.fuse(min(zc, 10), min(zp, 10), min(za, 8), zv, has_chat))
                thr = sens * adapt
                evaluated += 1

                if pending is None and score >= thr and t - last_trigger >= cooldown and t > 25:
                    pending = {"start": t, "peak": t, "best": score, "z": (zc, zp, za, zv)}
                elif pending is not None:
                    if score > pending["best"]:
                        pending.update(peak=t, best=score, z=(zc, zp, za, zv))
                    if t - pending["start"] >= 6:
                        last_trigger = pending["peak"]
                        pending["ready"] = pending["peak"] + post + 12  # the cut window reaches post + 10
                        waiting.append(pending)
                        bus.note(f"Spike on {self.streamer} at {_fmt(pending['peak'])} "
                                 f"(score {pending['best']:.1f})", source_id=self.sid)
                        pending = None

                with self.lock:
                    rate = self.cw.rate(t + L - 9, t + L + 1)
                    chatters = self.cw.chatters(t + L - 9, t + L + 1)
                    cls = self.cw.dominant_class(t + L - 14, t + L + 1)
                    top = self.cw.top(t + L - 14, t + L + 1, 5)
                pulse = {"id": self.sid, "t": t, "score": round(score, 2), "thr": round(thr, 2),
                         "chat": round(zc, 2), "people": round(zp, 2), "audio": round(za, 2), "voice": round(zv, 2),
                         "rate": round(rate), "chatters": chatters, "cls": cls, "top": top,
                         "loud": round(aud, 1)}
                self.pulses.append(pulse)
                bus.publish("pulse", pulse)

            for m in [m for m in waiting if self.media_now() >= m["ready"]]:
                waiting.remove(m)
                self._make_clip(m, pre, post)

            # measure this stream's chat delay: the shift that lines chat bursts up with loud moments
            if time.time() - last_lag > 180 and len(self.loud) > 300:
                last_lag = time.time()
                n = len(self.loud)
                a0 = max(0, n - 600)
                with self.lock:
                    chat_s = np.array([self.cw.weighted.get(x, 0.0) for x in range(a0, n)], np.float32)
                loud = np.array(self.loud[a0:n], np.float32)
                lag, r = moment.best_chat_lag(chat_s, loud - np.median(loud), 15)
                if r > 0.12 and 1 <= lag <= 14:
                    self.chat_lag = round(0.6 * self.chat_lag + 0.4 * lag, 1)
                    st = {**((db.get("sources", self.sid) or {}).get("stats") or {}), "chat_lag": self.chat_lag}
                    db.update("sources", self.sid, {"stats": st})

            # adapt the threshold toward the target rate (clips per hour of stream)
            if time.time() - last_adapt > 60:
                last_adapt = time.time()
                hours = max(0.05, (time.time() - self.started_wall) / 3600)
                rate_now = self.clips_made / hours
                if rate_now < target * 0.85:
                    adapt = max(0.55, adapt * 0.94)
                elif rate_now > target * 1.15:
                    adapt = min(1.8, adapt * 1.06)

            if time.time() - last_thumb > 10:
                last_thumb = time.time()
                self._thumb()
            if time.time() - last_stats > 5:
                last_stats = time.time()
                self._prune()
                st = {"uptime": int(time.time() - self.started_wall), "clips": self.clips_made,
                      "chat_total": self.cw.total, "threshold": round(sens * adapt, 2), "game": self.game,
                      "streamer": self.streamer}
                db.update("sources", self.sid, {"stats": st})
                bus.publish("source", {"id": self.sid, "stats": st})

    def _segments(self) -> list[tuple[str, float, float]]:
        out = []
        f = self.work / "seg.csv"
        if not f.exists():
            return out
        with open(f, newline="") as fh:
            for row in csv.reader(fh):
                if len(row) >= 3:
                    try:
                        out.append((row[0], float(row[1]), float(row[2])))
                    except ValueError:
                        pass
        return out

    def _make_clip(self, m: dict, pre: float, post: float) -> None:
        peak = float(m["peak"])
        owner = pipeline.owner_of(self.session, peak)
        if owner is not None:  # this moment is already inside another clip: one clip per moment
            bus.note(f"{self.streamer}: spike at {_fmt(peak)} is already in clip #{owner}", source_id=self.sid)
            return
        a, b = peak - pre - 12, peak + post + 10
        segs = [x for x in self._segments() if x[2] > a and x[1] < b and (self.segdir / x[0]).exists()]
        if not segs:
            bus.note(f"{self.streamer}: no recording for spike at {_fmt(peak)}", "warn", self.sid)
            return
        L = int(round(self.chat_lag))
        with self.lock:
            cls = self.cw.dominant_class(peak + L - 5, peak + L + 12)
            top = self.cw.top(peak + L - 5, peak + L + 12, 8)
        zc, zp, za, zv = m["z"]
        clip_id = db.insert("clips", {
            "source_id": self.sid, "streamer": self.streamer, "platform": self.info["platform"], "game": self.game,
            "peak": peak, "score": 0, "chat_class": cls, "chat": top, "status": "queued",
            "signals": {"score": round(m["best"], 2), "chat": round(zc, 2), "people": round(zp, 2),
                        "audio": round(za, 2), "voice": round(zv, 2), "raw_start": segs[0][1],
                        "session": self.session}})
        pipeline.claim(self.session, clip_id, peak - pre, peak + post)
        # copy the segments now: the ring buffer will delete them
        d = pipeline.clip_dir(clip_id)
        raw = d / "raw.ts"
        with open(raw, "wb") as out:
            for name, _, _ in segs:
                with open(self.segdir / name, "rb") as fh:
                    shutil.copyfileobj(fh, out)
        raw_start = segs[0][1]
        self.clips_made += 1
        bus.publish("clip", {"id": clip_id, "status": "queued", "source_id": self.sid})
        pipeline.submit(clip_id, lambda _d, raw=raw, raw_start=raw_start: (raw, raw_start))

    def _thumb(self) -> None:
        segs = self._segments()
        if len(segs) < 2:
            return
        name = segs[-2][0]
        try:
            render.snapshot(self.segdir / name, 0, self.base / "live.jpg")
            settings = (db.get("sources", self.sid) or {}).get("settings") or {}
            if "frame" not in settings:
                info = tools.probe(self.segdir / name)
                settings["frame"] = [info["width"], info["height"]]
                db.update("sources", self.sid, {"settings": settings})
            bus.publish("thumb", {"id": self.sid, "v": int(time.time())})
        except Exception:
            pass

    def _prune(self) -> None:
        segs = self._segments()
        if not segs:
            return
        horizon = segs[-1][2] - 360  # keep 6 minutes
        for name, _, end in segs:
            if end < horizon:
                (self.segdir / name).unlink(missing_ok=True)


def _fmt(t: float) -> str:
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{int(t % 60):02d}"
