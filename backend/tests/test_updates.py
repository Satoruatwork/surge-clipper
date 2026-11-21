"""Update notice: version order, reading GitHub's release JSON, only opening this repo's links, failing quietly."""
from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from surge import updates  # noqa: E402

BASE = "https://github.com/Satoruatwork/surge-clipper/"


def release(**over):
    rel = {
        "tag_name": "v0.3.0", "draft": False, "prerelease": False, "published_at": "2026-12-01T10:00:00Z",
        "html_url": BASE + "releases/tag/v0.3.0", "body": "Faster captions.",
        "assets": [
            {"name": "SHA256SUMS.txt", "size": 89, "browser_download_url": BASE + "releases/download/v0.3.0/SHA256SUMS.txt"},
            {"name": "Surge-Setup-0.3.0.exe", "size": 316581738, "digest": "sha256:abc123",
             "browser_download_url": BASE + "releases/download/v0.3.0/Surge-Setup-0.3.0.exe"},
        ],
    }
    rel.update(over)
    return rel


class Reply:
    def __init__(self, data=None, status=200):
        self.data, self.status_code = data, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("bad status", request=httpx.Request("GET", updates.LATEST),
                                        response=httpx.Response(self.status_code))

    def json(self):
        return self.data


def serve(monkeypatch, reply):
    def get(url, **kw):
        if isinstance(reply, Exception):
            raise reply
        return reply
    monkeypatch.setattr(updates.httpx, "get", get)


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(updates, "_state", updates._blank())
    monkeypatch.setattr(updates, "_notified", None)


def test_version_order():
    assert updates.newer("0.3.0", "0.2.0")
    assert updates.newer("v0.10.0", "0.9.9")
    assert not updates.newer("0.2.0", "0.2.0")
    assert not updates.newer("0.1.9", "0.2.0")
    assert not updates.newer("nightly", "0.2.0")


def test_newer_release_is_offered(monkeypatch):
    serve(monkeypatch, Reply(release()))
    out = updates.check("0.2.0")
    assert out["available"] and out["latest"] == "0.3.0" and out["current"] == "0.2.0"
    assert out["download"].endswith("Surge-Setup-0.3.0.exe")
    assert out["size"] == 316581738 and out["sha256"] == "abc123"
    assert out["url"] == BASE + "releases/tag/v0.3.0" and out["notes"] == "Faster captions."


def test_release_notes_are_shown_as_plain_text(monkeypatch):
    body = "## What's new\n**Install:** run `Surge.exe`, *then* [read](https://x.example).\n* item"
    serve(monkeypatch, Reply(release(body=body)))
    assert updates.check("0.2.0")["notes"] == "What's new\nInstall: run Surge.exe, then read.\n* item"


def test_same_version_is_not_an_update(monkeypatch):
    serve(monkeypatch, Reply(release(tag_name="v0.2.0")))
    assert not updates.check("0.2.0")["available"]


def test_prerelease_and_draft_are_ignored(monkeypatch):
    serve(monkeypatch, Reply(release(prerelease=True)))
    assert not updates.check("0.2.0")["available"]
    serve(monkeypatch, Reply(release(draft=True)))
    assert not updates.check("0.2.0")["available"]


def test_links_outside_the_repo_are_never_kept(monkeypatch):
    foreign = release(html_url="https://evil.example/x", assets=[
        {"name": "Surge-Setup-0.3.0.exe", "size": 1, "browser_download_url": "https://evil.example/Surge-Setup-0.3.0.exe"}])
    serve(monkeypatch, Reply(foreign))
    out = updates.check("0.2.0")
    assert out["download"] is None and out["url"] == BASE + "releases/latest"
    opened = []
    monkeypatch.setattr(updates.webbrowser, "open", opened.append)
    assert updates.open_download() and opened == [BASE + "releases/latest"]


def test_open_download_prefers_the_installer(monkeypatch):
    serve(monkeypatch, Reply(release()))
    updates.check("0.2.0")
    opened = []
    monkeypatch.setattr(updates.webbrowser, "open", opened.append)
    assert updates.open_download()
    assert opened == [BASE + "releases/download/v0.3.0/Surge-Setup-0.3.0.exe"]


def test_nothing_to_open_before_a_check(monkeypatch):
    monkeypatch.setattr(updates.webbrowser, "open", lambda url: pytest.fail("opened a link with nothing known"))
    assert not updates.open_download()


def test_no_release_yet(monkeypatch):
    serve(monkeypatch, Reply(None, 404))
    out = updates.check("0.2.0")
    assert not out["available"] and out["error"] is None


def test_failed_check_keeps_what_was_known(monkeypatch):
    serve(monkeypatch, Reply(release()))
    updates.check("0.2.0")
    serve(monkeypatch, httpx.ConnectError("offline"))
    out = updates.check("0.2.0")
    assert out["available"] and out["latest"] == "0.3.0"
    assert "offline" in out["error"]


def test_status_reports_the_setting(monkeypatch):
    monkeypatch.setattr(updates.config, "get", lambda key: True)
    assert updates.status()["enabled"] is True
    monkeypatch.setattr(updates.config, "get", lambda key: False)
    assert updates.status()["enabled"] is False
