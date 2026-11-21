"""External binaries (ffmpeg, ffprobe, streamlink, yt-dlp) and hardware encoder detection."""
from __future__ import annotations

import functools
import json
import shutil
import subprocess
import sys
from pathlib import Path

from . import bus

_VENV_SCRIPTS = Path(sys.executable).parent
CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def _find(name: str) -> str:
    local = _VENV_SCRIPTS / (name + (".exe" if sys.platform == "win32" else ""))
    if local.exists():
        return str(local)
    found = shutil.which(name)
    if not found:
        raise FileNotFoundError(f"{name} not found. Install it or add it to PATH.")
    return found


@functools.cache
def ffmpeg() -> str:
    return _find("ffmpeg")


@functools.cache
def ffprobe() -> str:
    return _find("ffprobe")


@functools.cache
def streamlink() -> str:
    return _find("streamlink")


@functools.cache
def ytdlp() -> str:
    return _find("yt-dlp")


def run(cmd: list, timeout: float | None = None, check: bool = True) -> subprocess.CompletedProcess:
    p = subprocess.run([str(c) for c in cmd], capture_output=True, timeout=timeout, creationflags=CREATE_NO_WINDOW)
    if check and p.returncode != 0:
        err = p.stderr.decode("utf-8", "replace").strip().splitlines()
        raise RuntimeError(f"{Path(str(cmd[0])).name} failed: {' | '.join(err[-4:])[:600]}")
    return p


def popen(cmd: list, **kw) -> subprocess.Popen:
    return subprocess.Popen([str(c) for c in cmd], creationflags=CREATE_NO_WINDOW, **kw)


def probe(path: str | Path) -> dict:
    p = run([ffprobe(), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
            timeout=60)
    info = json.loads(p.stdout or b"{}")
    v = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), {})
    a = next((s for s in info.get("streams", []) if s.get("codec_type") == "audio"), None)
    return {"width": int(v.get("width") or 0), "height": int(v.get("height") or 0),
            "duration": float(info.get("format", {}).get("duration") or 0), "has_audio": a is not None}


# Ordered by speed. Each is tried with a real 1-frame encode; the first that works wins.
_ENCODERS = {
    "h264_nvenc": ["-c:v", "h264_nvenc", "-preset", "p5", "-tune", "hq", "-rc", "vbr", "-cq", "21", "-b:v", "0"],
    "h264_qsv": ["-c:v", "h264_qsv", "-preset", "faster", "-global_quality", "22", "-look_ahead", "0"],
    "h264_amf": ["-c:v", "h264_amf", "-quality", "quality", "-rc", "cqp", "-qp_i", "20", "-qp_p", "22"],
    "libx264": ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"],
}


@functools.cache
def detect_encoder() -> str:
    for name in _ENCODERS:
        if name == "libx264":
            break
        try:
            run([ffmpeg(), "-hide_banner", "-v", "error", "-f", "lavfi", "-i", "color=black:s=256x256:d=0.2",
                 "-frames:v", "3", *_ENCODERS[name], "-f", "null", "-"], timeout=20)
            bus.note(f"Hardware encoder ready: {name}")
            return name
        except Exception:
            continue
    return "libx264"


def encoder_args(choice: str = "auto") -> list[str]:
    name = detect_encoder() if choice in ("auto", "", None) else choice
    return _ENCODERS.get(name, _ENCODERS["libx264"]) + ["-pix_fmt", "yuv420p"]


def has_filter(name: str) -> bool:
    p = run([ffmpeg(), "-hide_banner", "-filters"], timeout=20, check=False)
    return f" {name} " in p.stdout.decode("utf-8", "replace")
