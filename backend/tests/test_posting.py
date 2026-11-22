"""Posting under YouTube's daily upload limits: refusals wait instead of failing, channels learn their cap, posts that
failed on a limit come back in fresh slots, and uploads stay inside the day's allowance."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from surge import db, distribute, growth, youtube  # noqa: E402

PROJECT_ERR = ("<HttpError 429 when requesting None returned \"Quota exceeded for quota metric 'Video Uploads' and "
               "limit 'Video Uploads per day' of service 'youtube.googleapis.com'\">")
CHANNEL_ERR = "<HttpError 400 when requesting None returned \"The user has exceeded the number of videos they may upload.\">"


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_PATH", tmp_path / "surge.db")
    monkeypatch.setattr(db._local, "c", None, raising=False)
    db.init()
    settings = {"yt_daily_uploads": 100, "min_gap_minutes": 60, "slot_jitter_minutes": 0}
    monkeypatch.setattr(distribute.config, "get", lambda k: settings.get(k))
    monkeypatch.setattr(distribute.bus, "publish", lambda *a, **k: None)
    monkeypatch.setattr(distribute.bus, "note", lambda *a, **k: None)
    yield settings
    db._local.c.close()
    db._local.c = None


def account(name: str, **over) -> int:
    return db.insert("accounts", {"platform": "youtube", "name": name, "external_id": name, "active": 1,
                                  "status": "ok", "windows": ["00:00-00:00"], "daily_cap": 30, **over})


def post(aid: int, at: float, **over) -> int:
    cid = db.insert("clips", {"source_id": 1, "status": "approved", "path": "clip.mp4", "title": "t"})
    return db.insert("posts", {"clip_id": cid, "account_id": aid, "scheduled_at": at, "status": "queued",
                               "title": "t", "description": "d", "attempts": 0, **over})


def uploaded(aid: int, at: float) -> int:
    return post(aid, at, status="published", external_id=f"v{at}", uploaded_at=at)


def test_limit_errors_are_recognised():
    assert youtube.limit_hit(PROJECT_ERR) == "project"
    assert youtube.limit_hit(CHANNEL_ERR) == "channel"
    assert youtube.limit_hit("<HttpError 500 backendError>") is None


def test_a_project_refusal_waits_instead_of_failing(monkeypatch):
    a, b = account("NTM"), account("second")
    pid = post(a, time.time() + 600)
    waiting = post(b, time.time() + 900)
    monkeypatch.setattr(distribute.youtube, "upload", lambda *a, **k: (_ for _ in ()).throw(RuntimeError(PROJECT_ERR)))
    distribute._post_job(pid)
    p = db.get("posts", pid)
    assert p["status"] == "queued" and p["attempts"] == 0 and not p["error"]
    for aid in (a, b):  # one Google project uploads for every channel: all of them wait
        acc = db.get("accounts", aid)
        assert acc["hold_until"] > time.time() + 3000 and "daily upload limit" in acc["hold_reason"]
    assert db.get("posts", pid)["scheduled_at"] >= db.get("accounts", a)["hold_until"]
    assert db.get("posts", waiting)["scheduled_at"] >= db.get("accounts", b)["hold_until"]


def test_a_channel_refusal_teaches_the_channel_its_cap(monkeypatch):
    a = account("small")
    now = time.time()
    for k in range(3):
        uploaded(a, now - 3600 * (k + 1))
    pid = post(a, now + 600)
    monkeypatch.setattr(distribute.youtube, "upload", lambda *a, **k: (_ for _ in ()).throw(RuntimeError(CHANNEL_ERR)))
    distribute._post_job(pid)
    acc = db.get("accounts", a)
    assert acc["day_limit"] == 3 and growth.effective_cap(acc) == 3
    assert acc["hold_until"] == pytest.approx(now - 3 * 3600 + 86400, abs=5)  # when the oldest upload ages out
    assert db.get("posts", pid)["status"] == "queued"


def test_posts_that_failed_on_a_limit_come_back(monkeypatch):
    a = account("small")
    now = time.time()
    for k in range(10):
        uploaded(a, now - 3 * 3600 - k * 60)
    gone = [post(a, now - 1800, status="failed", attempts=3, error=CHANNEL_ERR) for _ in range(2)]
    other = post(a, now - 1800, status="failed", attempts=3, error="<HttpError 403 forbidden>")
    assert distribute.recover() == 2
    assert db.get("accounts", a)["day_limit"] == 10
    for pid in gone:
        p = db.get("posts", pid)
        assert p["status"] == "queued" and p["attempts"] == 0 and p["scheduled_at"] > now
    assert db.get("posts", other)["status"] == "failed"  # a real error stays failed


def test_uploads_stay_inside_the_day(monkeypatch, fresh):
    a = account("NTM")
    now = time.time()
    soon, later = post(a, now + 2 * 3600), post(a, now + 30 * 3600)
    sent = []
    monkeypatch.setattr(distribute._uploads, "submit", lambda fn, pid: sent.append(pid))
    distribute._dispatch()
    assert sent == [soon]  # a Short scheduled tomorrow afternoon isn't uploaded yet
    fresh["yt_daily_uploads"] = 1
    uploaded(a, now - 60)
    sent.clear()
    distribute._dispatch()
    assert sent == [] and db.get("accounts", a)["hold_until"] >= youtube.quota_reset() - 1
    assert later in [r["id"] for r in db.all("SELECT id FROM posts WHERE status='queued'")]


def test_a_day_never_holds_more_than_the_project_can_upload(fresh):
    fresh["yt_daily_uploads"] = 2
    a, b = account("one"), account("two")
    start = time.time() + 3600
    times = [distribute.next_slot(db.get("accounts", aid), start) for aid in (a, b)]
    for aid, t in zip((a, b), times):
        post(aid, t)
    third = distribute.next_slot(db.get("accounts", a), start)
    day_start, day_end = distribute.day_bounds(db.get("accounts", a), times[0])
    assert all(day_start <= t < day_end for t in times) and third >= day_end
