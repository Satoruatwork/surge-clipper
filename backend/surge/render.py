"""Turn a source window into a finished 1080x1920 short: layout, captions, hook, tag, loudness, bleeps."""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from . import captions, config, fx, tools
from .config import DATA, FONTS

W, H = 1080, 1920
CAM_SHARE = 0.36


def fonts_arg(work: Path) -> str:
    """ffmpeg runs with cwd=work and a filtergraph can't carry a drive-letter path, so hand it a relative one. When the
    fonts sit on another drive than the clips (app installed to a custom folder), use a copy next to the data."""
    try:
        return os.path.relpath(FONTS, work).replace("\\", "/")
    except ValueError:
        local = DATA / "fonts"
        if not local.exists():
            shutil.copytree(FONTS, local)
        return os.path.relpath(local, work).replace("\\", "/")


def even(v: float) -> int:
    return max(2, int(round(v / 2)) * 2)


def seam_y(cam_share: float = CAM_SHARE) -> int:
    return even(H * cam_share)


def video_chain(layout: str, sw: int, sh: int, cam: list[int] | None, focus: float = 0.5,
                cam_share: float = CAM_SHARE) -> str:
    """Filtergraph from [0:v] to [base] (1080x1920)."""
    def game_crop(target_w: int, target_h: int, avoid: list[int] | None = None) -> str:
        cw = min(sw, even(sh * target_w / target_h))
        ch = sh if cw < sw else min(sh, even(sw * target_h / target_w))
        cx = int(min(max(0, focus * sw - cw / 2), sw - cw))
        cy = int((sh - ch) / 2)
        if avoid:  # never show the webcam twice: slide the game crop off the overlay when it fits
            ax, ay, aw, ah = avoid
            if ax < cx + cw and cx < ax + aw and ay < cy + ch and cy < ay + ah:
                spots = [p for p in (ax + aw + 2, ax - cw - 2) if 0 <= p <= sw - cw]
                if spots:
                    cx = int(min(spots, key=lambda p: abs(p - cx)))
        return f"crop={cw}:{ch}:{cx - cx % 2}:{cy - cy % 2},scale={target_w}:{target_h}:flags=lanczos,setsar=1"

    if layout == "split" and cam:
        ch_out = seam_y(cam_share)
        gh_out = H - ch_out
        x, y, cw, chh = cam
        want = W / ch_out  # make the cam crop match the output band exactly (no stretching)
        if cw / chh > want:
            nw = even(chh * want)
            x, cw = x + (cw - nw) // 2, nw
        else:
            nh = even(cw / want)
            y, chh = y + (chh - nh) // 2, nh
        x, y = max(0, min(x, sw - cw)), max(0, min(y, sh - chh))
        return (f"[0:v]split=2[c][g];[c]crop={cw}:{chh}:{x - x % 2}:{y - y % 2},scale={W}:{ch_out}:flags=lanczos,"
                f"setsar=1[cam];[g]{game_crop(W, gh_out, cam)}[game];[cam][game]vstack=inputs=2,"
                f"drawbox=x=0:y={ch_out - 3}:w={W}:h=6:color=black@0.85:t=fill[base]")
    if layout == "pip" and cam:
        x, y, cw, chh = cam
        pw = 430
        ph = even(pw * chh / cw)
        return (f"[0:v]split=2[c][g];[g]{game_crop(W, H, cam)}[game];[c]crop={cw}:{chh}:{x}:{y},scale={pw}:{ph},"
                f"pad={pw + 12}:{ph + 12}:6:6:color=white[cam];[game][cam]overlay=40:470[base]")
    if layout == "full":
        return f"[0:v]{game_crop(W, H)}[base]"
    # blur: the whole 16:9 frame on a blurred fill
    return (f"[0:v]split=2[bg][fg];[bg]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
            f"boxblur=28:3,eq=brightness=-0.12:saturation=1.15[bgb];[fg]scale={W}:-2:flags=lanczos[fgs];"
            f"[bgb][fgs]overlay=0:(H-h)/2,setsar=1[base]")


def audio_chain(duration: float, bleeps: list[tuple[float, float]], src: str = "0:a") -> str:
    base = f"[{src}]aresample=48000,aformat=channel_layouts=stereo"
    if not bleeps:
        return f"{base},loudnorm=I=-14:TP=-1.5:LRA=11[a]"
    cond = "+".join(f"between(t,{max(0, s - 0.03):.3f},{e + 0.03:.3f})" for s, e in bleeps)
    return (f"{base},volume=0:enable='{cond}'[voice];"
            f"sine=frequency=1000:sample_rate=48000:duration={duration:.3f},aformat=channel_layouts=stereo,"
            f"volume=0.22,volume=0:enable='not({cond})'[beep];"
            f"[voice][beep]amix=inputs=2:normalize=0:duration=first,loudnorm=I=-14:TP=-1.5:LRA=11[a]")


def render(src: Path, start: float, duration: float, out: Path, words: list[dict], layout: str,
           cam: list[int] | None, hook: str = "", tag: str = "", style: str | None = None,
           focus: float = 0.5, has_audio: bool = True, fx_res: dict | None = None,
           audio_shift: float = 0.0) -> Path:
    """words are relative to `start`. audio_shift > 0 pulls the stream's audio earlier (for streams whose voice
    arrives late), < 0 delays it. Writes out + returns it."""
    s = config.load()
    style = style or s["caption_style"]
    sw, sh = tools.probe(src)["width"], tools.probe(src)["height"]
    if layout in ("split", "pip") and not cam:
        layout = "blur"
    work = out.parent
    ass = work / f"{out.stem}.ass"
    captions.write(ass, captions.build_ass(
        words, duration, layout, style, hook=hook, hook_seconds=float(s["hook_seconds"]), tag=tag,
        seam_y=seam_y() if layout == "split" else None, profanity=s["profanity"],
        max_words=int(s.get("caption_words") or 0) or None))
    fonts_rel = fonts_arg(work)
    fps = int(s["fps"])
    fx_res = fx_res or {}
    extra: list[str] = []          # extra ffmpeg inputs after the source (index 1..)
    vchain = [video_chain(layout, sw, sh, cam, focus), f"[base]fps={fps}[f0]"]
    cur = "f0"
    zp = fx.zoompan(fx_res.get("zooms") or [], fx_res.get("shakes") or [], fps, W, H)
    if zp:
        vchain.append(f"[{cur}]{zp},setsar=1[f1]")
        cur = "f1"
    fl = fx.flashes(fx_res.get("flashes") or [])
    if fl:
        vchain.append(f"[{cur}]{fl}[f2]")
        cur = "f2"
    if s.get("stickers", True):
        spots = fx.sticker_spots(layout, seam_y())
        for i, st in enumerate(fx_res.get("stickers") or []):
            path = fx.library.stickers().get(st["name"])
            if not path:
                continue
            idx = 1 + len(extra) // 6
            extra += ["-loop", "1", "-t", f"{duration:.3f}", "-i", path]
            x, y = spots[i % len(spots)]
            t0 = st["t"]
            vchain.append(f"[{idx}:v]scale=250:-1,format=rgba[sk{i}];[{cur}][sk{i}]overlay=x={x}:"
                          f"y='{y}-80*exp(-11*(t-{t0:.2f}))':enable='between(t,{t0:.2f},{t0 + 0.95:.2f})'"
                          f":eof_action=pass[st{i}]")
            cur = f"st{i}"
    vchain.append(f"[{cur}]subtitles={ass.name}:fontsdir={fonts_rel}[v]")
    vf = ";".join(vchain)
    bleeps = captions.profane_spans(words) if s["profanity"] == "bleep" else []
    voice = "0:a"
    if has_audio and abs(audio_shift) >= 0.01:  # the same file again, read from a shifted point for its audio
        voice = f"{1 + len(extra) // 6}:a"
        extra += ["-ss", f"{max(0.0, start + audio_shift):.3f}", "-t", f"{duration:.3f}", "-i", str(src)]
    n_inputs = 1 + len(extra) // 6
    achain = ""
    if has_audio:
        music = fx_res.get("music")
        sounds = fx_res.get("sounds") or []
        if not music and not sounds:
            achain = audio_chain(duration, bleeps, voice)
        else:
            parts = [audio_chain(duration, bleeps, voice).replace(",loudnorm=I=-14:TP=-1.5:LRA=11[a]",
                                                           ",loudnorm=I=-16:TP=-2:LRA=11[vo]")]
            mix = ["vo"]
            if music:
                mi = n_inputs
                n_inputs += 1
                extra += ["-ss", f"{music['offset']:.2f}", "-i", music["file"]]
                d_ms = int(music["delay"] * 1000)
                mvol = float(s.get("music_volume", -22)) + float(music.get("gain") or 0)
                parts.append(f"[vo]asplit=2[vo1][vk]")
                parts.append(f"[{mi}:a]aresample=48000,aformat=channel_layouts=stereo,"
                             f"atrim=0:{max(0.5, duration - music['delay']):.2f},adelay={d_ms}|{d_ms},"
                             f"volume={mvol:.1f}dB,afade=t=in:st={music['delay']:.2f}:d=0.5,"
                             f"afade=t=out:st={max(0.0, duration - 1.6):.2f}:d=1.5[mraw]")
                parts.append("[mraw][vk]sidechaincompress=threshold=0.035:ratio=6:attack=15:release=400[mus]")
                mix = ["vo1", "mus"]
            for j, snd in enumerate(sounds):
                si = n_inputs
                n_inputs += 1
                extra += ["-i", snd["file"]]
                ms = int(snd["t"] * 1000)
                vol = float(s.get("sfx_volume", -4)) + float(snd.get("gain") or 0)
                parts.append(f"[{si}:a]aresample=48000,aformat=channel_layouts=stereo,adelay={ms}|{ms},"
                             f"volume={vol:.1f}dB[sx{j}]")
                mix.append(f"sx{j}")
            parts.append("".join(f"[{m}]" for m in mix) + f"amix=inputs={len(mix)}:normalize=0:duration=first:"
                         "dropout_transition=0,alimiter=limit=0.95,loudnorm=I=-14:TP=-1.5:LRA=11[a]")
            achain = ";".join(parts)
    fg = vf + (";" + achain if achain else "")
    cmd = [tools.ffmpeg(), "-hide_banner", "-v", "error", "-y", "-ss", f"{start:.3f}", "-t", f"{duration:.3f}",
           "-i", str(src), *extra, "-filter_complex", fg, "-map", "[v]"]
    if has_audio:
        cmd += ["-map", "[a]", "-c:a", "aac", "-b:a", "192k", "-ar", "48000"]
    cmd += [*tools.encoder_args(s["encoder"]), "-r", str(int(s["fps"])), "-movflags", "+faststart",
            "-t", f"{duration:.3f}", out.name]
    p = tools.popen(cmd, cwd=str(work), stdout=-3, stderr=-1)
    _, err = p.communicate(timeout=900)
    if p.returncode != 0:
        # hardware encoders can fail on odd inputs: retry once on x264 before giving up
        if "libx264" not in " ".join(tools.encoder_args(s["encoder"])):
            cmd2 = [c for c in cmd]
            i = cmd2.index("-c:v")
            j = cmd2.index("-pix_fmt")
            cmd2[i:j] = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"]
            p = tools.popen(cmd2, cwd=str(work), stdout=-3, stderr=-1)
            _, err = p.communicate(timeout=900)
        if p.returncode != 0:
            raise RuntimeError("render failed: " + err.decode("utf-8", "replace").strip()[-500:])
    return out


def thumbnail(video: Path, at: float, out: Path) -> Path:
    tools.run([tools.ffmpeg(), "-v", "error", "-y", "-ss", f"{max(0, at):.2f}", "-i", str(video), "-frames:v", "1",
               "-vf", "scale=432:-2:out_range=full,format=yuvj420p", "-q:v", "3", str(out)], timeout=60)
    return out


def snapshot(src: Path, at: float, out: Path, width: int = 960) -> Path:
    seek = ["-ss", f"{at:.2f}"] if at > 0 else []  # at=0: first frame, always a keyframe in a live segment
    tools.run([tools.ffmpeg(), "-v", "error", "-y", *seek, "-i", str(src), "-frames:v", "1",
               "-vf", f"scale={width}:-2:out_range=full,format=yuvj420p", "-q:v", "3", str(out)], timeout=60)
    return out
