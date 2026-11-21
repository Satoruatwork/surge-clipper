"""Update notice. Surge asks GitHub for the newest release when it starts, then every few hours, and tells the UI when it
is newer than this build. It never downloads or runs anything by itself: "Download update" opens the installer link in
your browser, and the installer upgrades in place."""
from __future__ import annotations

import re
import threading
import time
import webbrowser

import httpx

from . import __version__, bus, config

REPO = "Satoruatwork/surge-clipper"
LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
SITE = f"https://github.com/{REPO}/"  # the only links Surge will ever open
RELEASES = SITE + "releases/latest"
EVERY = 6 * 3600
RETRY = 30 * 60
INSTALLER = re.compile(r"^Surge-Setup-.+\.exe$", re.I)
TAG = re.compile(r"^v\d+\.\d+\.\d+$")


def _blank() -> dict:
    return {"current": __version__, "latest": None, "available": False, "url": None, "download": None, "size": None,
            "sha256": None, "notes": "", "published": None, "checked": None, "error": None}


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


def _read(rel: dict, current: str) -> dict:
    latest = str(rel.get("tag_name") or "").lstrip("vV")
    asset = next((a for a in rel.get("assets") or [] if INSTALLER.match(str(a.get("name", "")))), None) or {}
    digest = str(asset.get("digest") or "")
    return {
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


def _loop() -> None:
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
