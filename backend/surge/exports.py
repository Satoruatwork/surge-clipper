"""Export a source's finished clips to a folder you can hand to someone: numbered MP4s, best first, plus
captions.txt with each clip's title, caption, hashtags, music credit and where the moment came from."""
from __future__ import annotations

import ctypes
import re
import shutil
import time
from pathlib import Path

from . import db


def videos_dir() -> Path:
    """The user's Videos folder (it can live in OneDrive), or ~/Videos."""
    try:
        guid = (ctypes.c_ubyte * 16).from_buffer_copy(  # FOLDERID_Videos {18989B1D-99B5-455B-841C-AB7C74E4DDFC}
            bytes.fromhex("1d9b9818b5995b45841cab7c74e4ddfc"))
        out = ctypes.c_wchar_p()
        if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(out)) == 0:
            path = Path(out.value)
            ctypes.windll.ole32.CoTaskMemFree(out)
            return path
    except Exception:
        pass
    return Path.home() / "Videos"


def safe_name(text: str, limit: int = 80) -> str:
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", text or "").strip().rstrip(".")
    return re.sub(r"\s+", " ", text)[:limit].strip() or "clip"


def _when(seconds: float) -> str:
    s = int(seconds or 0)
    return f"{s // 3600}h{s % 3600 // 60:02d}m{s % 60:02d}s"


def clips_of(source_id: int) -> list[dict]:
    rows = db.all("SELECT * FROM clips WHERE source_id=? AND status IN ('ready','approved')", (source_id,))
    views = lambda c: ((c.get("signals") or {}).get("viral") or {}).get("views") or 0  # noqa: E731
    return sorted(rows, key=lambda c: (-views(c), -float(c.get("score") or 0), c["id"]))


def export_source(source_id: int) -> tuple[Path, int]:
    src = db.get("sources", source_id) or {}
    clips = [c for c in clips_of(source_id) if c.get("path") and Path(c["path"]).exists()]
    if not clips:
        raise ValueError("No finished clips to export yet.")
    who = (src.get("stats") or {}).get("streamer") or src.get("channel") or "Surge"
    label = "most-clipped" if src.get("kind") == "viral" else (src.get("kind") or "clips")
    folder = videos_dir() / "Surge exports" / safe_name(f"{who} {label} {time.strftime('%Y-%m-%d %H%M')}")
    folder.mkdir(parents=True, exist_ok=True)
    lines = [f"{who}: {len(clips)} clips, best first. Paste each caption with its video.", ""]
    for i, c in enumerate(clips, 1):
        name = f"{i:02d} - {safe_name(c.get('title') or c.get('hook') or f'clip {c['id']}')}.mp4"
        shutil.copy2(c["path"], folder / name)
        tags = " ".join(c.get("hashtags") or [])
        credit = (c.get("fx") or {}).get("credit")
        viral = (c.get("signals") or {}).get("viral") or {}
        lines += [f"=== {name}", f"Title: {c.get('title') or ''}", "Caption:",
                  (c.get("description") or "").strip(), tags]
        if credit:
            lines.append(credit)
        where = f"{c.get('platform') or ''} broadcast"
        if viral.get("video"):
            where = f"twitch.tv/videos/{viral['video']} at {_when(c.get('start') or 0)}"
            if viral.get("views"):
                where += f" ({viral['views']} views on viewer clips)"
        lines += [f"Length: {float(c.get('duration') or 0):.0f}s · From: {where}", ""]
    (folder / "captions.txt").write_text("\n".join(lines), encoding="utf-8")
    return folder, len(clips)
