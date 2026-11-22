"""YouTube channels: OAuth (installed-app loopback), resumable Shorts upload with native scheduling (publishAt),
stats. YouTube owns the schedule once uploaded, so scheduled Shorts go out even with this PC off."""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from . import bus, config, db, vault
from .config import TOKENS

SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.readonly"]
CLIENT_FILE = TOKENS / "google_client_secret.json"


def _secret_path() -> Path:
    custom = config.get("google_client_secret")
    if custom and Path(custom).exists():
        return Path(custom)
    if CLIENT_FILE.exists():
        return CLIENT_FILE
    raise RuntimeError("Add your Google OAuth client (client_secret.json) in Settings → Platforms first.")


def _save_creds(account_id: int, creds) -> None:
    f = TOKENS / f"yt_{account_id}.enc"
    f.write_text(vault.seal(creds.to_json()), encoding="utf-8")
    db.update("accounts", account_id, {"token_file": str(f), "token_expires": (creds.expiry.timestamp()
                                                                             if creds.expiry else None)})


def creds_for(account: dict):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    raw = vault.unseal(Path(account["token_file"]).read_text(encoding="utf-8"))
    creds = Credentials.from_authorized_user_info(json.loads(raw), SCOPES)
    if not creds.valid:
        creds.refresh(Request())
        _save_creds(account["id"], creds)
    return creds


def service(account: dict):
    from googleapiclient.discovery import build
    return build("youtube", "v3", credentials=creds_for(account), cache_discovery=False)


def connect() -> list[int]:
    """Blocking: opens the browser for Google sign-in, returns the connected account id(s)."""
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    flow = InstalledAppFlow.from_client_secrets_file(str(_secret_path()), SCOPES)
    creds = flow.run_local_server(port=0, open_browser=True, prompt="select_account consent", access_type="offline",
                                  authorization_prompt_message="",
                                  success_message="Surge is connected to this channel. You can close this tab.")
    yt = build("youtube", "v3", credentials=creds, cache_discovery=False)
    items = yt.channels().list(part="snippet,statistics", mine=True).execute().get("items", [])
    if not items:
        raise RuntimeError("That Google account has no YouTube channel.")
    ids = []
    for ch in items:
        sn, stats = ch["snippet"], ch.get("statistics", {})
        existing = db.one("SELECT id FROM accounts WHERE platform='youtube' AND external_id=?", (ch["id"],))
        values = {"platform": "youtube", "external_id": ch["id"], "name": sn.get("title"),
                  "handle": sn.get("customUrl") or "", "avatar": ((sn.get("thumbnails") or {}).get("default") or {}).get("url"),
                  "followers": int(stats.get("subscriberCount") or 0), "views_total": int(stats.get("viewCount") or 0),
                  "status": "ok", "error": None}
        if existing:
            aid = existing["id"]
            db.update("accounts", aid, values)
        else:
            aid = db.insert("accounts", values)
        _save_creds(aid, creds)
        ids.append(aid)
        bus.note(f"YouTube channel connected: {sn.get('title')}")
    return ids


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def upload(account: dict, path: str, title: str, description: str, tags: list[str], publish_at: float | None,
           progress: Callable[[float], None] | None = None) -> dict:
    from googleapiclient.http import MediaFileUpload
    yt = service(account)
    title = title.strip()[:100] or "Clip"
    body = {
        "snippet": {"title": title, "description": description[:4900],
                    "tags": [t.lstrip("#") for t in tags][:20], "categoryId": "20"},
        "status": {"privacyStatus": "private" if publish_at else "public", "selfDeclaredMadeForKids": False,
                   "containsSyntheticMedia": False, "embeddable": True},
    }
    if publish_at:
        body["status"]["publishAt"] = _iso(publish_at)
    media = MediaFileUpload(path, chunksize=8 * 1024 * 1024, resumable=True, mimetype="video/mp4")
    req = yt.videos().insert(part="snippet,status", body=body, media_body=media, notifySubscribers=True)
    resp = None
    while resp is None:
        status, resp = req.next_chunk(num_retries=4)
        if status and progress:
            progress(status.progress())
    vid = resp["id"]
    st = resp.get("status", {})
    locked = (not publish_at and st.get("privacyStatus") == "private")
    return {"id": vid, "url": f"https://youtube.com/shorts/{vid}", "privacy": st.get("privacyStatus"),
            "publish_at": st.get("publishAt"), "locked": locked}


def stats(account: dict, video_ids: list[str]) -> dict[str, dict]:
    yt = service(account)
    out = {}
    for i in range(0, len(video_ids), 50):
        res = yt.videos().list(part="statistics,status", id=",".join(video_ids[i:i + 50])).execute()
        for it in res.get("items", []):
            s = it.get("statistics", {})
            out[it["id"]] = {"views": int(s.get("viewCount") or 0), "likes": int(s.get("likeCount") or 0),
                             "comments": int(s.get("commentCount") or 0),
                             "privacy": (it.get("status") or {}).get("privacyStatus")}
    return out


def refresh_channel(account: dict) -> None:
    yt = service(account)
    items = yt.channels().list(part="snippet,statistics", id=account["external_id"]).execute().get("items", [])
    if items:
        s = items[0].get("statistics", {})
        db.update("accounts", account["id"], {"followers": int(s.get("subscriberCount") or 0),
                                              "views_total": int(s.get("viewCount") or 0), "status": "ok"})


def now() -> float:
    return time.time()


# ---------------------------------------------------------------- daily upload limits

# YouTube refuses uploads in two ways once a daily limit is used up: the Google project's "Video Uploads per day"
# quota (shared by every channel the project serves) and the channel's own cap on uploads.
PROJECT_FULL = re.compile(r"quota ?exceeded|Video Uploads per day", re.I)
CHANNEL_FULL = re.compile(r"uploadLimitExceeded|exceeded the number of videos", re.I)


def limit_hit(error: str) -> str | None:
    """'channel' or 'project' when an upload was refused because a daily upload limit is used up, else None."""
    if CHANNEL_FULL.search(error or ""):
        return "channel"
    if PROJECT_FULL.search(error or ""):
        return "project"
    return None


def quota_day_start(at: float | None = None) -> float:
    """When YouTube's quota day began: midnight Pacific (PST = UTC-8, PDT = UTC-7)."""
    now = datetime.fromtimestamp(time.time() if at is None else at, timezone.utc)
    y = now.year
    # US DST: second Sunday of March 10:00 UTC to first Sunday of November 09:00 UTC (approximation is fine here)
    mar = datetime(y, 3, 8 + (6 - datetime(y, 3, 8).weekday()) % 7, 10, tzinfo=timezone.utc)
    nov = datetime(y, 11, 1 + (6 - datetime(y, 11, 1).weekday()) % 7, 9, tzinfo=timezone.utc)
    offset = timedelta(hours=-7 if mar <= now < nov else -8)
    midnight = (now + offset).replace(hour=0, minute=0, second=0, microsecond=0) - offset
    return midnight.timestamp()


def quota_reset(at: float | None = None) -> float:
    return quota_day_start(at) + 86400


def _uploaded_at() -> str:
    return "COALESCE(p.uploaded_at, p.created_at)"  # posts uploaded before uploaded_at existed: close enough


def uploads_today() -> int:
    """Uploads your Google project made since the quota day began (one project serves every channel)."""
    return db.scalar("SELECT COUNT(*) FROM posts p JOIN accounts a ON a.id=p.account_id WHERE a.platform='youtube' "
                     f"AND p.external_id IS NOT NULL AND {_uploaded_at()} >= ?", (quota_day_start(),)) or 0


def channel_uploads(account_id: int, since: float) -> list[float]:
    """When this channel's uploads since `since` happened, oldest first."""
    rows = db.all(f"SELECT {_uploaded_at()} t FROM posts p WHERE p.account_id=? AND p.external_id IS NOT NULL "
                  f"AND {_uploaded_at()} >= ? ORDER BY t", (account_id, since))
    return [float(r["t"]) for r in rows]
