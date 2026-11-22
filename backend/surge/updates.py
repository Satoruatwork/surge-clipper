"""Updates. Surge asks GitHub for the newest release when it starts, then every few hours, and tells the UI when it is
newer than this build. Nothing is downloaded or run without a click:

- Update now (small update): when the release has a patch from this version, Surge downloads only the files that
  changed (megabytes, not the whole installer), checks them against the release's checksums and its own install,
  keeps a copy of every file it replaces, then a short script swaps them in after Surge closes, restarts it and puts
  back any live recordings. A failed copy restores the old files.
- Download installer: opens the installer link in the browser; the installer upgrades in place."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
import zipfile
from pathlib import Path, PurePosixPath

import httpx

from . import __version__, bus, config
from .config import DATA, FROZEN

REPO = "Satoruatwork/surge-clipper"
LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
SITE = f"https://github.com/{REPO}/"  # the only links Surge will ever open or download from
RELEASES = SITE + "releases/latest"
EVERY = 6 * 3600
RETRY = 30 * 60
INSTALLER = re.compile(r"^Surge-Setup-.+\.exe$", re.I)
TAG = re.compile(r"^v\d+\.\d+\.\d+$")
UPDATES = DATA / "updates"
RESUME = UPDATES / "resume.json"
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\{1FFBB38F-6BD5-4B7A-8897-F019E3B7D4A5}_is1"


class UpdateError(Exception):
    """Why a small update can't go ahead, worded for the person using the app."""


def patch_name(current: str, latest: str) -> str:
    return f"Surge-Patch-{current}-to-{latest}.zip"


def can_patch() -> bool:
    """Small updates replace the installed app's own files: only the installed Windows app can do that."""
    return FROZEN and sys.platform == "win32"


def _blank() -> dict:
    return {"current": __version__, "latest": None, "available": False, "url": None, "download": None, "size": None,
            "sha256": None, "patch": None, "notes": "", "published": None, "checked": None, "error": None}


_lock = threading.Lock()
_state = _blank()
_notified: str | None = None


def _version(text: str) -> tuple[int, int, int] | None:
    m = re.match(r"\s*v?(\d+)\.(\d+)\.(\d+)", text or "")
    return (int(m[1]), int(m[2]), int(m[3])) if m else None


def newer(latest: str, current: str) -> bool:
    a, b = _version(latest), _version(current)
    return bool(a and b and a > b)


def _own(url: object, prefix: str) -> str | None:
    return url if isinstance(url, str) and url.startswith(prefix) else None


def _plain(md: str) -> str:
    """Release notes are Markdown; the banner shows plain text."""
    md = re.sub(r"```\w*\n?", "", md)                                     # code fences
    md = re.sub(r"^#{1,6}\s*", "", md, flags=re.M)                        # headings
    md = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", md)                      # links keep their text
    md = re.sub(r"(\*\*|__)(.+?)\1", r"\2", md)                           # bold
    md = re.sub(r"(?<![\w*])\*(?!\s)([^*\n]+?)\*(?![\w*])", r"\1", md)    # italics (bullets keep their star)
    return md.replace("`", "").strip()


def _digest(asset: dict) -> str | None:
    d = str(asset.get("digest") or "")
    return d.removeprefix("sha256:") if d.startswith("sha256:") else None


def _read(rel: dict, current: str) -> dict:
    latest = str(rel.get("tag_name") or "").lstrip("vV")
    asset = next((a for a in rel.get("assets") or [] if INSTALLER.match(str(a.get("name", "")))), None) or {}
    digest = str(asset.get("digest") or "")
    small = next((a for a in rel.get("assets") or [] if a.get("name") == patch_name(current, latest)), None)
    patch = None
    if small and _own(small.get("browser_download_url"), SITE + "releases/download/"):
        patch = {"url": small["browser_download_url"], "size": small.get("size") if isinstance(small.get("size"), int)
                 else None, "sha256": _digest(small)}
    return {
        "patch": patch,
        "current": current,
        "latest": latest or None,
        "available": bool(latest) and newer(latest, current) and not rel.get("draft") and not rel.get("prerelease"),
        "url": _own(rel.get("html_url"), SITE) or SITE + "releases/latest",
        "download": _own(asset.get("browser_download_url"), SITE + "releases/download/"),
        "size": asset["size"] if isinstance(asset.get("size"), int) else None,
        "sha256": digest.removeprefix("sha256:") if digest.startswith("sha256:") else None,
        "notes": _plain(str(rel.get("body") or ""))[:2000],
        "published": rel.get("published_at"),
    }


def _from_redirect(cur: str) -> dict | None:
    """The API's anonymous quota (60 an hour per address) is quickly used up on shared and mobile networks. The public
    releases/latest page isn't metered that way: it redirects to the newest tag, which is all the banner needs."""
    r = httpx.get(RELEASES, headers={"User-Agent": f"Surge/{cur}"}, timeout=10, follow_redirects=False)
    tag = str(r.headers.get("location") or "").rsplit("/", 1)[-1]
    if r.status_code not in (301, 302, 303, 307, 308) or not TAG.match(tag):
        return None
    latest = tag[1:]
    return {**_blank(), "current": cur, "latest": latest, "available": newer(latest, cur),
            "url": f"{SITE}releases/tag/{tag}", "download": f"{SITE}releases/download/{tag}/Surge-Setup-{latest}.exe"}


def _fetch(cur: str) -> dict:
    r = httpx.get(LATEST, headers={"Accept": "application/vnd.github+json", "User-Agent": f"Surge/{cur}"},
                  timeout=10, follow_redirects=True)
    if r.status_code == 404:  # no release published yet
        return {**_blank(), "current": cur}
    if r.status_code in (403, 429):
        found = _from_redirect(cur)
        if found:
            return found
    r.raise_for_status()
    return _read(r.json(), cur)


def status() -> dict:
    with _lock:
        out = dict(_state)
    out["enabled"] = bool(config.get("check_updates"))
    out["can_patch"] = can_patch()
    out["job"] = job()
    return out


def check(current: str | None = None) -> dict:
    """Ask GitHub now. Never raises: a failed check keeps what was known and records why."""
    global _notified
    cur = current or __version__
    try:
        found, error = _fetch(cur), None
    except httpx.HTTPStatusError as e:
        code = e.response.status_code
        found, error = None, ("GitHub is limiting requests from this network right now. Surge will try again soon."
                              if code in (403, 429) else f"GitHub answered {code}.")
    except Exception as e:
        found, error = None, f"Couldn't reach GitHub: {e}"
    with _lock:
        if found is not None:
            _state.update(found)
        _state["error"] = error
        _state["checked"] = time.time()
    out = status()
    if out["available"] and out["latest"] != _notified:
        _notified = out["latest"]
        bus.note(f"Surge {out['latest']} is available. The banner at the top has the download.")
        bus.publish("update", out)
    return out


def open_download() -> bool:
    """Open the installer (or the release page) in the default browser. Only links under this repo are opened."""
    with _lock:
        url = _state["download"] or _state["url"]
    if not _own(url, SITE):
        return False
    webbrowser.open(url)
    return True


# ---------------------------------------------------------------- small updates

_job_lock = threading.Lock()
_job = {"state": "idle", "progress": 0.0, "message": "", "error": None}


def job() -> dict:
    with _job_lock:
        return dict(_job)


def _set_job(**kw) -> None:
    with _job_lock:
        _job.update(kw)
        out = dict(_job)
    bus.publish("update_job", out)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _safe(rel: str) -> bool:
    p = PurePosixPath(rel)
    return bool(rel) and not p.is_absolute() and ".." not in p.parts and ":" not in rel and "\\" not in rel


def install() -> dict:
    """Start a small update in the background. Raises UpdateError when only the installer can update."""
    st = status()
    if not can_patch():
        raise UpdateError("Small updates work in the installed app. Download the installer instead.")
    if not (st["available"] and st.get("patch")):
        raise UpdateError("There's no small update from your version. Download the installer instead.")
    with _job_lock:
        if _job["state"] in ("downloading", "checking", "restarting"):
            return dict(_job)
    _set_job(state="downloading", progress=0.0, message="Downloading the update", error=None)
    threading.Thread(target=_install, args=(st, Path(sys.executable).parent), daemon=True, name="update").start()
    return job()


def _download(url: str, dest: Path, size: int | None) -> None:
    if not _own(url, SITE + "releases/download/"):
        raise UpdateError("The update isn't on Surge's own release page.")
    got = 0
    with httpx.stream("GET", url, headers={"User-Agent": f"Surge/{__version__}"}, timeout=60,
                      follow_redirects=True) as r:
        if r.status_code == 404:
            raise UpdateError("This release has no small update from your version.")
        r.raise_for_status()
        total = size or int(r.headers.get("content-length") or 0)
        with open(dest, "wb") as f:
            for chunk in r.iter_bytes(1 << 16):
                f.write(chunk)
                got += len(chunk)
                if total:
                    _set_job(progress=round(min(1.0, got / total), 3))


def _published_sha(latest: str, name: str) -> str | None:
    """The file's SHA-256 from the release's SHA256SUMS.txt."""
    r = httpx.get(f"{SITE}releases/download/v{latest}/SHA256SUMS.txt", headers={"User-Agent": f"Surge/{__version__}"},
                  timeout=20, follow_redirects=True)
    if r.status_code != 200:
        return None
    for line in r.text.splitlines():
        parts = line.strip().split()
        if len(parts) == 2 and parts[1].lstrip("*") == name:
            return parts[0].lower()
    return None


def stage(zpath: Path, dest: Path) -> dict:
    """Unpack the patch into dest and check every file against patch.json. Returns patch.json."""
    with zipfile.ZipFile(zpath) as z:
        plan = json.loads(z.read("patch.json"))
        for rel in plan["changed"] + plan["removed"]:
            if not _safe(rel):
                raise UpdateError(f"The update names an unsafe path ({rel[:80]}).")
        for rel in plan["changed"]:
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(f"files/{rel}") as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out, 1 << 20)
            if _sha256(target) != plan["files"][rel]["sha256"]:
                raise UpdateError(f"{rel} in the update doesn't match its checksum.")
    return plan


def differences(app: Path, plan: dict) -> list[str]:
    """Files this install should share with the new version but doesn't (missing, or another size)."""
    keep = set(plan["files"]) - set(plan["changed"])
    return [rel for rel in sorted(keep)
            if not (app / rel).is_file() or (app / rel).stat().st_size != plan["files"][rel]["size"]]


def _backup(app: Path, plan: dict, dest: Path) -> None:
    for rel in plan["changed"] + plan["removed"]:
        src = app / rel
        if src.is_file():
            (dest / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest / rel)


def script(app: Path, work: Path, plan: dict, version: str, pid: int, uninstall_key: str = UNINSTALL_KEY) -> str:
    """The batch file that swaps the files in once Surge has closed, then starts it again. A failed copy puts the
    backed-up files back."""
    (work / "removed.txt").write_text("".join(r.replace("/", "\\") + "\n" for r in plan["removed"]), encoding="utf-8")
    key = "HKCU\\" + uninstall_key
    return "\r\n".join([
        "@echo off", "chcp 65001 >nul", "setlocal",
        f'set "APP={app}"', f'set "WORK={work}"', 'set "LOG=%WORK%\\apply.log"',
        f'echo Updating Surge to {version} > "%LOG%"',
        "for /l %%i in (1,1,90) do (",
        f'  tasklist /FI "PID eq {pid}" /NH 2>nul | find " {pid} " >nul || goto ready',
        "  timeout /t 1 /nobreak >nul", ")",
        ":ready",
        # helpers Surge started (ffmpeg, streamlink, yt-dlp) must not hold files open
        "powershell -NoProfile -ExecutionPolicy Bypass -Command \"Get-Process | Where-Object { $_.Path -and "
        "$_.Path.StartsWith($env:APP + '\\', [StringComparison]::OrdinalIgnoreCase) } | Stop-Process -Force\" "
        '>> "%LOG%" 2>&1',
        'robocopy "%WORK%\\files" "%APP%" /E /R:10 /W:1 /NP /NFL /NDL >> "%LOG%"',
        "if errorlevel 8 goto undo",
        'for /f "usebackq delims=" %%F in ("%WORK%\\removed.txt") do del /f /q "%APP%\\%%F" >> "%LOG%" 2>&1',
        f'reg query "{key}" >nul 2>&1 && reg add "{key}" /v DisplayVersion /d "{version}" /f >nul',
        f'reg query "{key}" >nul 2>&1 && reg add "{key}" /v DisplayName /d "Surge {version}" /f >nul',
        'echo done >> "%LOG%"', 'cd /d "%APP%"', 'start "" "%APP%\\Surge.exe"', "exit /b 0",
        ":undo", 'echo copy failed, restoring the old files >> "%LOG%"',
        'robocopy "%WORK%\\backup" "%APP%" /E /R:10 /W:1 /NP /NFL /NDL >> "%LOG%"',
        'cd /d "%APP%"', 'start "" "%APP%\\Surge.exe"', "exit /b 1", ""])


def _install(st: dict, app: Path) -> None:
    try:
        latest = st["latest"]
        work = UPDATES / latest
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True)
        zpath = work / patch_name(st["current"], latest)
        _download(st["patch"]["url"], zpath, st["patch"].get("size"))
        _set_job(state="checking", progress=1.0, message="Checking the update")
        want = st["patch"].get("sha256") or _published_sha(latest, zpath.name)
        if not want or _sha256(zpath) != want:
            raise UpdateError("The download doesn't match the checksum published with the release.")
        plan = stage(zpath, work / "files")
        if plan.get("from") != st["current"] or plan.get("to") != latest:
            raise UpdateError("The update is for another version.")
        off = differences(app, plan)
        if off:
            raise UpdateError(f"This install isn't a plain {st['current']} ({off[0]} differs), so only the installer "
                              "can update it safely.")
        _backup(app, plan, work / "backup")
        bat = work / "apply.cmd"
        bat.write_text(script(app, work, plan, latest, os.getpid()), encoding="utf-8")
        from . import live
        RESUME.write_text(json.dumps({"at": time.time(), "to": latest, "live": sorted(live.running())}),
                          encoding="utf-8")
        _set_job(state="restarting", message=f"Restarting Surge {latest}")
        bus.note(f"Updating to Surge {latest}: Surge restarts in a moment and resumes its live recordings.")
        subprocess.Popen(["cmd.exe", "/c", str(bat)], close_fds=True,
                         creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
        threading.Timer(1.5, _quit).start()
    except Exception as e:
        msg = str(e) if isinstance(e, UpdateError) else f"The small update failed: {str(e)[:200]}"
        _set_job(state="failed", error=msg, message=msg + " Download the installer instead.")


def _quit() -> None:
    """Stop the recordings cleanly, then let the update script take over."""
    try:
        from . import live
        live.stop_all()
        time.sleep(1.0)
    finally:
        os._exit(0)


def resume_after_update() -> None:
    """After a small update: start the live recordings that were running, once, and clear the update's files."""
    try:
        info = json.loads(RESUME.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    RESUME.unlink(missing_ok=True)
    if info.get("to") != __version__:  # the swap failed and put the old files back: keep its log
        bus.note(f"The update to Surge {info.get('to')} didn't finish, so Surge {__version__} is still installed. "
                 f"Download the installer to update. Details: {UPDATES / str(info.get('to')) / 'apply.log'}", "warn")
        return
    for old in UPDATES.glob("*"):
        if old.is_dir():
            shutil.rmtree(old, ignore_errors=True)
    if time.time() - float(info.get("at") or 0) > 30 * 60:
        return
    from . import db, live
    for sid in info.get("live") or []:
        if db.get("sources", sid):
            db.update("sources", sid, {"status": "starting", "error": None})
            live.start(sid)
    bus.note(f"Updated to Surge {__version__}." + (" Live recordings resumed." if info.get("live") else ""))


def _loop() -> None:
    try:
        resume_after_update()
    except Exception:  # a resume problem must never stop the update checks
        pass
    time.sleep(8)  # let the engine finish starting first
    while True:
        wait = EVERY
        try:
            if config.get("check_updates") and check()["error"]:
                wait = RETRY
        except Exception:  # the checker must never take the engine down
            wait = RETRY
        time.sleep(wait)


def start() -> None:
    threading.Thread(target=_loop, daemon=True, name="updates").start()
