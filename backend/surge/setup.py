"""Setup center: what's configured, what's missing, and live tests for each connection (Google, Meta, the AI writer)."""
from __future__ import annotations

import json
import shutil
import time
from datetime import datetime, timedelta, timezone

import httpx

from . import config, connectors, db, llm, tools, youtube
from .config import DATA, MODELS


def _pacific_midnight() -> float:
    """YouTube's quota day resets at midnight Pacific (PST = UTC-8, PDT = UTC-7)."""
    now = datetime.now(timezone.utc)
    y = now.year
    # US DST: second Sunday of March 10:00 UTC to first Sunday of November 09:00 UTC (approximation is fine here)
    mar = datetime(y, 3, 8 + (6 - datetime(y, 3, 8).weekday()) % 7, 10, tzinfo=timezone.utc)
    nov = datetime(y, 11, 1 + (6 - datetime(y, 11, 1).weekday()) % 7, 9, tzinfo=timezone.utc)
    offset = timedelta(hours=-7 if mar <= now < nov else -8)
    local = now + offset
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0) - offset
    return midnight.timestamp()


def google_client() -> dict | None:
    try:
        path = youtube._secret_path()
    except RuntimeError:
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    kind = "installed" if "installed" in data else "web" if "web" in data else None
    body = data.get(kind or "", {})
    cid = body.get("client_id", "")
    return {"type": kind, "project_id": body.get("project_id"), "client_id": f"…{cid[-28:]}" if cid else None}


def status() -> dict:
    s = config.load()
    yt = db.all("SELECT id, name, status, error FROM accounts WHERE platform='youtube'")
    ig = db.all("SELECT id, name, status, error, token_expires FROM accounts WHERE platform='instagram'")
    uploads_today = db.scalar(
        "SELECT COUNT(*) FROM posts p JOIN accounts a ON a.id=p.account_id WHERE a.platform='youtube' "
        "AND p.external_id IS NOT NULL AND p.updated_at>=?", (_pacific_midnight(),)) or 0
    soon = time.time() + 10 * 86400
    models = sorted(p.name.replace("models--", "").split("--")[-1] for p in MODELS.glob("models--*")) \
        if MODELS.exists() else []
    disk = shutil.disk_usage(DATA)
    ai = connectors.active()
    return {
        "google": {"client": google_client(), "channels": len(yt),
                   "broken": [a["name"] for a in yt if a["status"] == "error"],
                   "uploads_today": uploads_today, "upload_limit": int(s.get("yt_daily_uploads") or 100)},
        "meta": {"app_id": bool(s.get("meta_app_id")), "secret": bool(s.get("meta_app_secret")),
                 "redirect": s.get("meta_redirect_uri"), "accounts": len(ig),
                 "broken": [a["name"] for a in ig if a["status"] == "error"],
                 "expiring": [a["name"] for a in ig if a.get("token_expires") and a["token_expires"] < soon]},
        "ai": {"on": ai is not None, "name": ai["name"] if ai else None, "model": ai["model"] if ai else None},
        "engine": {"encoder": tools.detect_encoder(), "models": models,
                   "disk_free_gb": round(disk.free / 1e9, 1), "disk_total_gb": round(disk.total / 1e9, 1)},
    }


def test_google() -> dict:
    c = google_client()
    if not c:
        return {"ok": False, "message": "No Google OAuth client file yet. Add the JSON you downloaded."}
    if c["type"] != "installed":
        return {"ok": False, "message": "That client is a Web application. Create one of type Desktop app instead."}
    return {"ok": True, "message": f"Desktop client for project {c['project_id']} is ready. Connect a channel to finish."}


def test_meta() -> dict:
    s = config.load()
    app, secret = s.get("meta_app_id"), s.get("meta_app_secret")
    if not app or not secret:
        return {"ok": False, "message": "Add the Meta app ID and app secret first."}
    try:
        r = httpx.get(f"https://graph.facebook.com/{app}", params={"fields": "name", "access_token": f"{app}|{secret}"},
                      timeout=20)
        data = r.json()
    except Exception as e:
        return {"ok": False, "message": f"Couldn't reach Meta: {e}"}
    if r.status_code < 400 and not data.get("error"):
        return {"ok": True, "message": f"Meta app \"{data.get('name', app)}\" answered. Credentials are valid."}
    # Instagram-login apps have their own app ID that the Facebook graph can't look up: check the shape instead
    import re
    if re.fullmatch(r"\d{10,20}", str(app)) and re.fullmatch(r"[0-9a-f]{32}", str(secret)):
        return {"ok": True, "message": "Saved. The ID and secret look right; Instagram confirms them on the first sign-in."}
    return {"ok": False, "message": (data.get("error") or {}).get("message", "That app ID or secret doesn't look right.")}


def test_ai() -> dict:
    return llm.test_active()


CALLBACK_HTML = """<!doctype html><meta charset="utf-8"><title>Surge</title>
<body style="margin:0;display:grid;place-items:center;height:100vh;background:#17121f;color:#f4ece4;font:16px system-ui">
<div style="max-width:440px;text-align:center"><h1 style="font-size:22px">{title}</h1><p style="color:#b3a6c2">{text}</p></div>
</body>"""
