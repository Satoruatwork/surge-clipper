"""`Surge.exe --selftest`: load every piece the installed app leans on and report one line each, offline.
The build runs it against the frozen bundle; exit code 0 means everything loaded."""
from __future__ import annotations

import traceback
from pathlib import Path


def _tool(name: str, flag: str) -> str:
    from . import tools
    exe = getattr(tools, name)()
    out = tools.run([exe, flag], timeout=60).stdout.decode("utf-8", "replace").strip().splitlines()
    return f"{Path(exe).name} {out[0][:70] if out else ''}".strip()


def _streamlink_plugins() -> str:
    from . import tools
    p = tools.run([tools.streamlink(), "--plugins"], timeout=60)
    text = (p.stdout + p.stderr).decode("utf-8", "replace").lower()
    assert "twitch" in text, "streamlink did not list its twitch plugin"
    return "plugins load (twitch found)"


def _ctranslate2() -> str:
    import ctranslate2
    return "cpu compute types " + ",".join(sorted(ctranslate2.get_supported_compute_types("cpu")))


def _vad() -> str:
    import onnxruntime
    from faster_whisper.utils import get_assets_path
    model = next(Path(get_assets_path()).glob("*.onnx"))
    onnxruntime.InferenceSession(str(model), providers=["CPUExecutionProvider"])
    return f"{model.name} loads in onnxruntime"


def _av() -> str:
    import av
    return f"PyAV {av.__version__}"


def _opencv() -> str:
    import cv2
    from .facecam import MODEL
    cv2.FaceDetectorYN.create(str(MODEL), "", (320, 240), 0.75, 0.3, 50)
    return f"opencv {cv2.__version__}, YuNet face model loads"


def _google() -> str:
    from googleapiclient.discovery import build
    build("youtube", "v3", developerKey="x", cache_discovery=False)
    return "YouTube v3 discovery document loads"


def _anthropic() -> str:
    import anthropic
    anthropic.Anthropic(api_key="x")
    return f"anthropic {anthropic.__version__}"


def _curl() -> str:
    from curl_cffi import requests
    requests.Session(impersonate="chrome").close()
    return "curl_cffi browser impersonation ready"


def _webview() -> str:
    import clr  # noqa: F401  (pythonnet: starts the .NET runtime the window needs)
    import webview
    return f"pywebview {getattr(webview, '__version__', '?')}, .NET runtime loads"


def _vault() -> str:
    from . import vault
    assert vault.unseal(vault.seal("surge")) == "surge"
    return "DPAPI round trip"


def _assets() -> str:
    from . import config, library
    assert (config.UI_DIST / "index.html").is_file(), "ui/dist/index.html is missing"
    counts = {"fonts": len(list(config.FONTS.glob("*.ttf"))), "songs": len(list(library.MUSIC_DIR.glob("*.mp3"))),
              "stickers": len(list(library.STICKER_DIR.glob("*.png")))}
    assert all(counts.values()), f"missing assets: {counts}"
    return "UI built, " + ", ".join(f"{n} {k}" for k, n in counts.items())


def _api() -> str:
    from .api import app
    return f"{len(app.routes)} API routes"


CHECKS = [
    ("ffmpeg", lambda: _tool("ffmpeg", "-version")),
    ("ffprobe", lambda: _tool("ffprobe", "-version")),
    ("yt-dlp", lambda: _tool("ytdlp", "--version")),
    ("streamlink", lambda: _tool("streamlink", "--version")),
    ("streamlink plugins", _streamlink_plugins),
    ("ctranslate2", _ctranslate2),
    ("silero vad", _vad),
    ("av", _av),
    ("opencv", _opencv),
    ("google api", _google),
    ("anthropic", _anthropic),
    ("curl_cffi", _curl),
    ("webview", _webview),
    ("vault", _vault),
    ("assets", _assets),
    ("api", _api),
]


def run() -> int:
    from . import __version__, config
    lines, bad = [f"Surge {__version__} self-test"], 0
    for name, check in CHECKS:
        try:
            lines.append(f"ok    {name}: {check()}")
        except Exception as e:  # report every failure, not just the first
            bad += 1
            lines.append(f"FAIL  {name}: {type(e).__name__}: {e}")
            lines.append("      " + traceback.format_exc().strip().splitlines()[-3].strip())
    lines.append(f"{len(CHECKS) - bad}/{len(CHECKS)} passed")
    report = "\n".join(lines)
    print(report)
    (config.DATA / "selftest.txt").write_text(report + "\n", encoding="utf-8")
    return 1 if bad else 0
