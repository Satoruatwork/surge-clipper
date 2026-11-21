# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build: two programs sharing one _internal folder.

  Surge.exe       the windowed app: engine + pywebview window       (packaging/entry_surge.py)
  streamlink.exe  the live-stream grabber Surge shells out to       (packaging/entry_streamlink.py)

packaging/build.ps1 drops ffmpeg.exe, ffprobe.exe and yt-dlp.exe beside them afterwards. tools.py looks for all four
next to sys.executable, so the engine runs the same from source and installed."""
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_dynamic_libs, collect_submodules

HERE = Path(SPECPATH)
ROOT = HERE.parent
ASSETS = ROOT / "assets"
EXCLUDES = ["tkinter", "pytest", "_pytest", "matplotlib", "IPython"]


def gather(*packages):
    datas, binaries, hidden = [], [], []
    for name in packages:
        d, b, h = collect_all(name)
        datas += d
        binaries += b
        hidden += h
    return datas, binaries, hidden


# ------------------------------------------------------------------ Surge.exe
datas, binaries, hidden = gather("anthropic")
for pkg in ("uvicorn", "websockets", "anyio", "python_multipart", "google.auth", "google.oauth2",
            "google_auth_oauthlib", "googleapiclient", "webview"):
    hidden += collect_submodules(pkg)
hidden += ["clr"]
datas += collect_data_files("faster_whisper") + collect_data_files("httplib2") + collect_data_files("certifi")
datas += collect_data_files("googleapiclient", includes=["discovery_cache/documents/youtube.v3.json"])
binaries += collect_dynamic_libs("ctranslate2") + collect_dynamic_libs("onnxruntime")

for sub in ("fonts", "music", "stickers"):  # the sfx pack is deliberately absent: each install downloads its own
    datas.append((str(ASSETS / sub), f"assets/{sub}"))
for name in ("campaign_sources.json", "music_catalog.json", "face_detection_yunet_2023mar.onnx", "surge.png"):
    datas.append((str(ASSETS / name), "assets"))
datas.append((str(ROOT / "ui" / "dist"), "ui/dist"))

surge = Analysis([str(HERE / "entry_surge.py")], pathex=[str(ROOT / "backend")], binaries=binaries, datas=datas,
                 hiddenimports=hidden, excludes=EXCLUDES)

# Trim what Surge never uses: the other ~600 Google API discovery documents (108 MB; it only calls YouTube v3) and
# OpenCV's bundled video backend (it only decodes stills and runs the face detector).
surge.datas = [d for d in surge.datas if "discovery_cache/documents/" not in d[0].replace("\\", "/")
               or d[0].replace("\\", "/").endswith("/youtube.v3.json")]
surge.binaries = [b for b in surge.binaries if "opencv_videoio_ffmpeg" not in b[0]]

# ------------------------------------------------------------------ streamlink.exe
sl_datas, sl_binaries, sl_hidden = gather("streamlink")
sl_hidden += collect_submodules("streamlink_cli") + ["lxml.etree", "lxml._elementpath", "lxml.html"]
sl_binaries += collect_dynamic_libs("Crypto")

streamlink = Analysis([str(HERE / "entry_streamlink.py")], pathex=[str(ROOT / "backend")], binaries=sl_binaries,
                      datas=sl_datas, hiddenimports=sl_hidden, excludes=EXCLUDES)

# ------------------------------------------------------------------ one folder
exe_surge = EXE(PYZ(surge.pure), surge.scripts, [], exclude_binaries=True, name="Surge", console=False, upx=False,
                icon=str(ASSETS / "surge.ico"))
exe_streamlink = EXE(PYZ(streamlink.pure), streamlink.scripts, [], exclude_binaries=True, name="streamlink",
                     console=True, upx=False)

COLLECT(exe_surge, surge.binaries, surge.datas, exe_streamlink, streamlink.binaries, streamlink.datas,
        strip=False, upx=False, name="Surge")
