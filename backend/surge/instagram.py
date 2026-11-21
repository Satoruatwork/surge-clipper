"""Instagram professional accounts via the official Graph API: Instagram Login tokens (IGAA..., one account each)
or a Facebook Business token (EAA..., every IG account linked to your Pages at once). Reels go up with the
resumable rupload protocol in 4 MB parts."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from . import bus, config, db, vault
from .config import TOKENS

IG = "https://graph.instagram.com"
FB = "https://graph.facebook.com"
RUPLOAD = "https://rupload.facebook.com/ig-api-upload"
SCOPES = "instagram_business_basic,instagram_business_content_publish,instagram_business_manage_insights"
CHUNK = 4 * 1024 * 1024


class IGError(RuntimeError):
    pass


def _ver() -> str:
    return config.get("ig_graph_version") or "v21.0"


def _check(r: httpx.Response, what: str) -> dict:
    try:
        data = r.json()
    except ValueError:
        data = {}
    if r.status_code >= 400 or (isinstance(data, dict) and data.get("error")):
        err = (data.get("error") or {}) if isinstance(data, dict) else {}
        raise IGError(f"Instagram {what}: {err.get('message') or r.text[:200]}")
    return data


def _save(account_id: int, tok: dict) -> None:
    f = TOKENS / f"ig_{account_id}.enc"
    f.write_text(vault.seal(json.dumps(tok)), encoding="utf-8")
    db.update("accounts", account_id, {"token_file": str(f), "token_expires": tok.get("expires_at")})


def token_of(account: dict) -> dict:
    return json.loads(vault.unseal(Path(account["token_file"]).read_text(encoding="utf-8")))


def _upsert(user: dict, tok: dict) -> int:
    existing = db.one("SELECT id FROM accounts WHERE platform='instagram' AND external_id=?", (user["id"],))
    values = {"platform": "instagram", "external_id": user["id"], "name": user.get("name") or user.get("username"),
              "handle": "@" + (user.get("username") or ""), "avatar": user.get("profile_picture_url"),
              "followers": int(user.get("followers_count") or 0), "status": "ok", "error": None}
    if existing:
        aid = existing["id"]
        db.update("accounts", aid, values)
    else:
        aid = db.insert("accounts", values)
    _save(aid, {**tok, "user_id": user["id"]})
    bus.note(f"Instagram connected: @{user.get('username')}")
    return aid


def connect_token(token: str) -> list[int]:
    token = token.strip()
    with httpx.Client(timeout=30) as c:
        if token.startswith("EAA"):  # Facebook-side: enumerate every IG business account on the Pages
            data = _check(c.get(f"{FB}/{_ver()}/me/accounts", params={
                "fields": "name,instagram_business_account{id,username,name,profile_picture_url,followers_count}",
                "limit": 100, "access_token": token}), "pages")
            ids = []
            for page in data.get("data", []):
                ig = page.get("instagram_business_account")
                if ig:
                    ids.append(_upsert(ig, {"access_token": token, "graph": "facebook", "expires_at": None}))
            if not ids:
                raise IGError("No Instagram professional account is linked to the Pages this token can see.")
            return ids
        expires_at = time.time() + 59 * 86400
        secret = config.get("meta_app_secret")
        if secret:  # turn a short-lived token into a 60-day one (fails harmlessly if it already is one)
            r = c.get(f"{IG}/access_token", params={"grant_type": "ig_exchange_token", "client_secret": secret,
                                                     "access_token": token})
            if r.status_code < 400 and r.json().get("access_token"):
                token = r.json()["access_token"]
                expires_at = time.time() + int(r.json().get("expires_in") or 5184000)
        me = _check(c.get(f"{IG}/{_ver()}/me", params={
            "fields": "user_id,username,name,profile_picture_url,account_type,followers_count",
            "access_token": token}), "profile")
        user = {**me, "id": str(me.get("user_id") or me.get("id"))}
        return [_upsert(user, {"access_token": token, "graph": "instagram", "expires_at": expires_at})]


def auth_url() -> str:
    app = config.get("meta_app_id")
    if not app:
        raise IGError("Add your Meta app ID and secret in Settings → Platforms first.")
    return "https://www.instagram.com/oauth/authorize?" + urlencode({
        "client_id": app, "redirect_uri": config.get("meta_redirect_uri"), "response_type": "code",
        "scope": SCOPES, "enable_fb_login": "0", "force_authentication": "1"})


def connect_code(pasted: str) -> list[int]:
    """Accepts the full URL Instagram redirected to (or just the code)."""
    code = pasted.strip()
    if "code=" in code:
        code = parse_qs(urlparse(code).query).get("code", [""])[0]
    code = code.split("#")[0]
    with httpx.Client(timeout=30) as c:
        data = _check(c.post("https://api.instagram.com/oauth/access_token", data={
            "client_id": config.get("meta_app_id"), "client_secret": config.get("meta_app_secret"),
            "grant_type": "authorization_code", "redirect_uri": config.get("meta_redirect_uri"), "code": code}),
            "login")
    return connect_token(data["access_token"])


def refresh(account: dict) -> None:
    tok = token_of(account)
    if tok.get("graph") != "instagram":
        return
    with httpx.Client(timeout=30) as c:
        data = _check(c.get(f"{IG}/refresh_access_token", params={"grant_type": "ig_refresh_token",
                                                                    "access_token": tok["access_token"]}), "refresh")
    tok.update(access_token=data["access_token"], expires_at=time.time() + int(data.get("expires_in") or 5184000))
    _save(account["id"], tok)


def _base(tok: dict) -> str:
    return f"{FB if tok.get('graph') == 'facebook' else IG}/{_ver()}"


def publish(account: dict, path: str, caption: str, progress: Callable[[float], None] | None = None) -> dict:
    tok = token_of(account)
    base, uid, at = _base(tok), tok["user_id"], tok["access_token"]
    size = Path(path).stat().st_size
    with httpx.Client(timeout=120) as c:
        for attempt in range(3):
            cont = _check(c.post(f"{base}/{uid}/media", data={
                "media_type": "REELS", "upload_type": "resumable", "caption": caption[:2200],
                "share_to_feed": "true", "access_token": at}), "create upload")["id"]
            off = 0
            failed = False
            with open(path, "rb") as fh:
                while off < size:
                    fh.seek(off)
                    part = fh.read(CHUNK)
                    r = None
                    for _ in range(3):
                        r = c.post(f"{RUPLOAD}/{_ver()}/{cont}", content=part, timeout=300, headers={
                            "Authorization": f"OAuth {at}", "offset": str(off), "file_size": str(size)})
                        if r.status_code in (200, 206) or r.status_code < 500:
                            break
                        time.sleep(2)
                    if r.status_code >= 400:
                        if "ProcessingFailed" in r.text:
                            failed = True  # transient on Meta's side: start over with a fresh container
                            break
                        raise IGError(f"Instagram upload refused: {r.text[:200]}")
                    off += len(part)
                    if progress:
                        progress(0.8 * off / size)
            if failed:
                time.sleep(10)
                continue
            deadline = time.time() + 900
            while True:
                st = _check(c.get(f"{base}/{cont}", params={"fields": "status_code,status", "access_token": at}),
                            "processing").get("status_code")
                if st == "FINISHED":
                    break
                if st in ("ERROR", "EXPIRED"):
                    raise IGError("Instagram couldn't process the video (Reels need H.264 MP4, 3-90 s).")
                if time.time() > deadline:
                    raise IGError("Instagram took too long to process the video.")
                time.sleep(4)
            mid = _check(c.post(f"{base}/{uid}/media_publish", data={"creation_id": cont, "access_token": at}),
                         "publish")["id"]
            if progress:
                progress(1.0)
            link = c.get(f"{base}/{mid}", params={"fields": "permalink", "access_token": at}).json().get("permalink")
            return {"id": mid, "url": link}
    raise IGError("Instagram kept failing to process the upload. Try again later.")


def stats(account: dict, media_ids: list[str]) -> dict[str, dict]:
    tok = token_of(account)
    base, at = _base(tok), tok["access_token"]
    out = {}
    with httpx.Client(timeout=30) as c:
        for mid in media_ids:
            views = likes = comments = 0
            r = c.get(f"{base}/{mid}/insights", params={"metric": "views,likes,comments", "access_token": at})
            if r.status_code < 400:
                for m in r.json().get("data", []):
                    v = int((m.get("values") or [{}])[0].get("value") or 0)
                    views, likes, comments = ((v, likes, comments) if m["name"] == "views" else
                                              (views, v, comments) if m["name"] == "likes" else (views, likes, v))
            else:
                f = c.get(f"{base}/{mid}", params={"fields": "like_count,comments_count", "access_token": at}).json()
                likes, comments = int(f.get("like_count") or 0), int(f.get("comments_count") or 0)
            out[mid] = {"views": views, "likes": likes, "comments": comments}
    return out


def refresh_profile(account: dict) -> None:
    tok = token_of(account)
    with httpx.Client(timeout=30) as c:
        r = c.get(f"{_base(tok)}/{tok['user_id']}", params={"fields": "followers_count,media_count",
                                                            "access_token": tok["access_token"]})
        if r.status_code < 400:
            db.update("accounts", account["id"], {"followers": int(r.json().get("followers_count") or 0),
                                                  "status": "ok"})
