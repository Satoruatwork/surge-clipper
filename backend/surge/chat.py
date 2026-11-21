"""Chat readers. Live: Twitch IRC (anonymous), YouTube (yt-dlp live_chat tail), Kick (Pusher websocket).
VOD replay: Twitch GQL comments, YouTube live_chat replay. All yield (seconds, user, text, boost)."""
from __future__ import annotations

import json
import random
import re
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from . import bus, tools

Push = Callable[[float, str, str, float], None]  # (wall or media seconds, user, text, boost)


# ---------------------------------------------------------------- URL parsing

def parse_source(url: str) -> dict:
    """{'platform': twitch|youtube|kick|file, 'channel': ..., 'video': ..., 'kind_hint': live|vod|None}"""
    u = url.strip()
    if Path(u).exists():
        return {"platform": "file", "channel": Path(u).stem, "video": u, "kind_hint": "vod"}
    p = urlparse(u if "://" in u else "https://" + u)
    host = (p.hostname or "").lower().removeprefix("www.").removeprefix("m.")
    parts = [x for x in p.path.split("/") if x]
    if host.endswith("twitch.tv"):
        if len(parts) >= 2 and parts[0] == "videos":
            return {"platform": "twitch", "channel": None, "video": parts[1], "kind_hint": "vod"}
        if len(parts) >= 3 and parts[1] == "video":
            return {"platform": "twitch", "channel": parts[0].lower(), "video": parts[2], "kind_hint": "vod"}
        return {"platform": "twitch", "channel": parts[0].lower() if parts else None, "video": None,
                "kind_hint": "live"}
    if host.endswith("kick.com"):
        if "videos" in parts or "video" in parts:
            return {"platform": "kick", "channel": parts[0].lower() if parts[0] != "video" else None,
                    "video": parts[-1], "kind_hint": "vod"}
        return {"platform": "kick", "channel": parts[0].lower() if parts else None, "video": None, "kind_hint": "live"}
    if "youtu" in host:
        vid = None
        if host == "youtu.be" and parts:
            vid = parts[0]
        elif "v=" in (p.query or ""):
            vid = dict(x.split("=", 1) for x in p.query.split("&") if "=" in x).get("v")
        elif len(parts) >= 2 and parts[0] in ("live", "shorts"):
            vid = parts[1]
        chan = parts[0] if parts and parts[0].startswith("@") else None
        return {"platform": "youtube", "channel": chan, "video": vid, "kind_hint": None}
    return {"platform": "other", "channel": host, "video": None, "kind_hint": None}


# ---------------------------------------------------------------- live readers

_BADGE_BOOST = {"broadcaster": 1.6, "moderator": 1.3, "vip": 1.3, "subscriber": 1.1}


def twitch_live(channel: str, push: Push, stop: threading.Event) -> None:
    tag_re = re.compile(r"^@([^ ]+) :([^!]+)![^ ]+ PRIVMSG #[^ ]+ :(.*)$")
    while not stop.is_set():
        try:
            s = socket.create_connection(("irc.chat.twitch.tv", 6667), timeout=20)
            nick = f"justinfan{random.randint(10000, 99999)}"
            s.sendall(f"CAP REQ :twitch.tv/tags\r\nPASS SCHMOOPIIE\r\nNICK {nick}\r\nJOIN #{channel}\r\n".encode())
            s.settimeout(2.0)
            buf = b""
            last_rx = time.time()
            while not stop.is_set():
                try:
                    data = s.recv(65536)
                except socket.timeout:
                    if time.time() - last_rx > 330:
                        raise ConnectionError("chat idle")
                    continue
                if not data:
                    raise ConnectionError("chat closed")
                last_rx = time.time()
                *lines, buf = (buf + data).split(b"\r\n")
                for raw in lines:
                    line = raw.decode("utf-8", "replace")
                    if line.startswith("PING"):
                        s.sendall(b"PONG :tmi.twitch.tv\r\n")
                        continue
                    m = tag_re.match(line)
                    if not m:
                        continue
                    tags = dict(kv.split("=", 1) for kv in m.group(1).split(";") if "=" in kv)
                    badges = {b.split("/")[0] for b in tags.get("badges", "").split(",") if b}
                    boost = max([_BADGE_BOOST.get(b, 1.0) for b in badges] or [1.0])
                    push(time.time(), tags.get("display-name") or m.group(2), m.group(3), boost)
            s.close()
        except Exception as e:  # reconnect forever until stopped
            if stop.is_set():
                return
            bus.note(f"Twitch chat #{channel} reconnecting ({e})", "warn")
            stop.wait(3)


def _yt_items(obj: dict):
    """Yield (usec_or_offset_ms, author, text, is_offset) from one yt-dlp live_chat JSON line."""
    act = obj.get("replayChatItemAction") or obj
    offset = act.get("videoOffsetTimeMsec")
    for a in act.get("actions", []) or []:
        item = (a.get("addChatItemAction") or {}).get("item") or {}
        r = item.get("liveChatTextMessageRenderer") or item.get("liveChatPaidMessageRenderer")
        if not r:
            continue
        text = "".join(run.get("text") or ((run.get("emoji") or {}).get("shortcuts") or [""])[0]
                       for run in (r.get("message") or {}).get("runs", []))
        author = (r.get("authorName") or {}).get("simpleText", "")
        if offset is not None:
            yield int(offset) / 1000.0, author, text, True
        else:
            yield int(r.get("timestampUsec", time.time() * 1e6)) / 1e6, author, text, False


def youtube_live(url: str, workdir: Path, push: Push, stop: threading.Event) -> None:
    out = workdir / "livechat"
    for old in workdir.glob("livechat.live_chat.json*"):
        old.unlink(missing_ok=True)
    proc = tools.popen([tools.ytdlp(), "--skip-download", "--write-subs", "--sub-langs", "live_chat",
                        "-o", str(out), url], stdout=open(workdir / "ytchat.log", "wb"), stderr=-2)
    try:
        pos, rest = 0, ""
        while not stop.is_set() and proc.poll() is None:
            stop.wait(1.0)
            f = next(iter(workdir.glob("livechat.live_chat.json*")), None)
            if not f:
                continue
            with open(f, encoding="utf-8", errors="replace") as fh:
                fh.seek(pos)
                chunk = fh.read()
                pos = fh.tell()
            *lines, rest = (rest + chunk).split("\n")
            for line in lines:
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                for ts, author, text, _ in _yt_items(obj):
                    push(time.time() if ts < 1e9 else ts, author, text, 1.0)
    finally:
        proc.terminate()


def _kick_api(path: str) -> dict:
    from curl_cffi import requests as creq
    r = creq.get(f"https://kick.com/api/{path}", impersonate="chrome", timeout=20)
    r.raise_for_status()
    return r.json()


def kick_channel(slug: str) -> dict:
    return _kick_api(f"v2/channels/{slug}")


def kick_live(slug: str, push: Push, stop: threading.Event) -> None:
    from websockets.sync.client import connect
    try:
        info = kick_channel(slug)
        room = (info.get("chatroom") or {}).get("id")
    except Exception as e:
        bus.note(f"Kick chat unavailable for {slug}: {e}", "warn")
        return
    url = "wss://ws-us2.pusher.com/app/32cbd69e4b950bf97679?protocol=7&client=js&version=8.4.0&flash=false"
    while not stop.is_set():
        try:
            with connect(url, open_timeout=20, close_timeout=2) as ws:
                ws.send(json.dumps({"event": "pusher:subscribe", "data": {"auth": "", "channel": f"chatrooms.{room}.v2"}}))
                while not stop.is_set():
                    try:
                        raw = ws.recv(timeout=2)
                    except TimeoutError:
                        continue
                    msg = json.loads(raw)
                    ev = msg.get("event", "")
                    if ev == "pusher:ping":
                        ws.send(json.dumps({"event": "pusher:pong", "data": {}}))
                    if not ev.endswith("ChatMessageEvent"):
                        continue
                    data = json.loads(msg.get("data") or "{}")
                    sender = data.get("sender") or {}
                    badges = {b.get("type") for b in (sender.get("identity") or {}).get("badges", [])}
                    boost = max([_BADGE_BOOST.get(b, 1.0) for b in badges] or [1.0])
                    text = re.sub(r"\[emote:\d+:([^\]]+)\]", r"\1", data.get("content") or "")
                    push(time.time(), sender.get("username", ""), text, boost)
        except Exception as e:
            if stop.is_set():
                return
            bus.note(f"Kick chat {slug} reconnecting ({e})", "warn")
            stop.wait(3)


def start_live(src: dict, url: str, workdir: Path, push: Push, stop: threading.Event) -> threading.Thread | None:
    target = None
    if src["platform"] == "twitch" and src.get("channel"):
        target = (twitch_live, (src["channel"], push, stop))
    elif src["platform"] == "youtube":
        target = (youtube_live, (url, workdir, push, stop))
    elif src["platform"] == "kick" and src.get("channel"):
        target = (kick_live, (src["channel"], push, stop))
    if not target:
        return None
    t = threading.Thread(target=target[0], args=target[1], daemon=True, name=f"chat-{src['platform']}")
    t.start()
    return t


# ---------------------------------------------------------------- VOD replay

_GQL = "https://gql.twitch.tv/gql"
_GQL_HDR = {"Client-Id": "kimne78kx3ncx6brgo4mv6wki5h1ko", "Content-Type": "text/plain;charset=UTF-8"}
_COMMENTS_HASH = "b70a3591ff0f4e0313d126c6a1502d79a1c02baebb288227c582044aa76adf6a"


def _twitch_page(client, video: str, offset: int | None = None, cursor: str | None = None):
    var = {"videoID": video}
    if cursor:
        var["cursor"] = cursor
    else:
        var["contentOffsetSeconds"] = int(offset or 0)
    body = [{"operationName": "VideoCommentsByOffsetOrCursor", "variables": var,
             "extensions": {"persistedQuery": {"version": 1, "sha256Hash": _COMMENTS_HASH}}}]
    r = client.post(_GQL, headers=_GQL_HDR, content=json.dumps(body), timeout=30)
    r.raise_for_status()
    data = r.json()[0]
    if data.get("errors"):
        raise RuntimeError(data["errors"][0].get("message", "gql error"))
    comments = ((data.get("data") or {}).get("video") or {}).get("comments") or {}
    out = []
    for e in comments.get("edges", []):
        n = e.get("node") or {}
        text = "".join(f.get("text", "") for f in (n.get("message") or {}).get("fragments", []))
        badges = {b.get("setID") for b in (n.get("message") or {}).get("userBadges", []) or []}
        boost = max([_BADGE_BOOST.get(b, 1.0) for b in badges] or [1.0])
        out.append((float(n.get("contentOffsetSeconds", 0)), (n.get("commenter") or {}).get("displayName", ""),
                    text, boost, e.get("cursor")))
    return out, bool((comments.get("pageInfo") or {}).get("hasNextPage"))


def twitch_vod_chat(video: str, duration: float, progress: Callable[[float], None] | None = None) -> list[tuple]:
    """Fetch the whole replay in parallel offset ranges (much faster than one cursor walk)."""
    import httpx
    n_ranges = max(1, min(12, int(duration // 900) + 1))
    edges = [duration * i / n_ranges for i in range(n_ranges + 1)]
    done = [0.0] * n_ranges

    def fetch(i: int) -> list[tuple]:
        a, b = edges[i], edges[i + 1]
        msgs, off, cursor = [], a, None
        with httpx.Client(http2=False) as client:
            for _ in range(5000):
                try:
                    page, more = _twitch_page(client, video, offset=None if cursor else off, cursor=cursor)
                except Exception:
                    if cursor:  # cursor paging refused (integrity): fall back to offsets
                        cursor = None
                        continue
                    raise
                if not page:
                    break
                for t, u, txt, boost, _c in page:
                    if a <= t < b:
                        msgs.append((t, u, txt, boost))
                last_t = page[-1][0]
                done[i] = min(1.0, (last_t - a) / max(1, b - a))
                if progress:
                    progress(sum(done) / n_ranges)
                if last_t >= b or not more:
                    break
                cursor = page[-1][4]
                off = last_t + 1
        return msgs

    with ThreadPoolExecutor(n_ranges) as ex:
        parts = list(ex.map(fetch, range(n_ranges)))
    seen, out = set(), []
    for t, u, txt, boost in sorted(m for p in parts for m in p):
        key = (round(t, 1), u, txt)
        if key not in seen:
            seen.add(key)
            out.append((t, u, txt, boost))
    return out


def youtube_vod_chat(url: str, workdir: Path) -> list[tuple]:
    out = workdir / "replay"
    tools.run([tools.ytdlp(), "--skip-download", "--write-subs", "--sub-langs", "live_chat", "-o", str(out), url],
              timeout=3600, check=False)
    f = next(iter(workdir.glob("replay.live_chat.json")), None)
    if not f:
        return []
    msgs = []
    for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        for t, author, text, is_off in _yt_items(obj):
            if is_off:
                msgs.append((t, author, text, 1.0))
    return msgs
