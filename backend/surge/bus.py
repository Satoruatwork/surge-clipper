"""Thread-safe pub/sub: engine threads publish, websocket clients receive."""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from collections import deque

log = logging.getLogger("surge")

_loop: asyncio.AbstractEventLoop | None = None
_subs: set[asyncio.Queue] = set()
_lock = threading.Lock()
_recent_logs: deque = deque(maxlen=300)


def attach(loop: asyncio.AbstractEventLoop) -> None:
    global _loop
    _loop = loop


def subscribe() -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=2000)
    with _lock:
        _subs.add(q)
    return q


def unsubscribe(q: asyncio.Queue) -> None:
    with _lock:
        _subs.discard(q)


def _deliver(msg: str) -> None:
    for q in list(_subs):
        if q.full():
            try:
                q.get_nowait()
            except asyncio.QueueEmpty:
                pass
        q.put_nowait(msg)


def publish(kind: str, data: dict | None = None) -> None:
    if _loop is None:
        return
    msg = json.dumps({"type": kind, "data": data or {}, "t": time.time()}, default=str)
    try:
        _loop.call_soon_threadsafe(_deliver, msg)
    except RuntimeError:
        pass


def note(text: str, level: str = "info", source_id: int | None = None) -> None:
    """Human-readable activity line, kept for the UI's activity feed."""
    entry = {"text": text, "level": level, "source_id": source_id, "t": time.time()}
    _recent_logs.append(entry)
    getattr(log, "warning" if level == "warn" else ("error" if level == "error" else "info"))(text)
    publish("log", entry)


def recent_logs() -> list[dict]:
    return list(_recent_logs)
