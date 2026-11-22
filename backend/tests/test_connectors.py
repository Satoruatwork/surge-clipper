"""AI connectors: storage, validation, the OpenAI-compatible and model-list calls, and how the AI writer uses them."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from surge import connectors, llm, modelpick  # noqa: E402


class Reply:
    def __init__(self, data=None, status=200, text=None, headers=None):
        self.data, self.status_code, self.headers = data, status, headers or {}
        self.text = text if text is not None else (__import__("json").dumps(data) if data is not None else "")

    def json(self):
        if self.data is None:
            raise ValueError("not json")
        return self.data


def completion(text: str, finish="stop") -> Reply:
    return Reply({"choices": [{"message": {"content": text}, "finish_reason": finish}]})


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(connectors, "FILE", tmp_path / "connectors.json")
    monkeypatch.setattr(connectors, "CHECKS_FILE", tmp_path / "model_checks.json")
    monkeypatch.setattr(connectors, "_cache", None)
    monkeypatch.setattr(connectors, "_quirks", {})
    monkeypatch.setattr(modelpick, "_memo", {})
    monkeypatch.setattr(connectors.config, "get", lambda key: "")
    monkeypatch.setattr(connectors.config, "save", lambda patch: None)


def openai_form(**over):
    form = {"name": "DeepSeek", "protocol": "openai", "base_url": "https://api.deepseek.com", "api_key": "sk-test-1234567890",
            "model": "deepseek-flash", "rank_model": "deepseek-v4-pro"}
    form.update(over)
    return form


def serve(monkeypatch, *replies):
    """Fake the OpenAI-style POST: each call returns the next reply and records (url, headers, body)."""
    calls, queue = [], list(replies)

    def post(url, headers, body, retry=True):
        calls.append((url, headers, body))
        answer = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(answer, Exception):
            raise answer
        return answer
    monkeypatch.setattr(connectors, "_post", post)
    return calls


# ------------------------------------------------------------------ addresses

def test_clean_url_forms():
    c = connectors.clean_url
    assert c("https://api.openai.com/v1/", "openai") == "https://api.openai.com/v1"
    assert c("api.deepseek.com", "openai") == "https://api.deepseek.com"
    assert c("localhost:11434/v1", "openai") == "http://localhost:11434/v1"
    assert c("https://api.openai.com/v1/chat/completions", "openai") == "https://api.openai.com/v1"
    assert c("https://api.anthropic.com/v1", "anthropic") == "https://api.anthropic.com"
    assert c("http://192.168.1.20:1234/v1", "openai") == "http://192.168.1.20:1234/v1"


def test_clean_url_refuses_unsafe_or_empty():
    for bad in ("", "http://example.com/v1", "ftp://example.com", "https://user:pw@example.com/v1"):
        with pytest.raises(connectors.ConnectorError):
            connectors.clean_url(bad, "openai")


# ------------------------------------------------------------------ storage

def test_save_encrypts_masks_and_activates_the_first():
    saved = connectors.save(openai_form())
    assert saved["has_key"] and saved["key_hint"] == "…7890" and "api_key" not in saved
    assert "sk-test-1234567890" not in connectors.FILE.read_text(encoding="utf-8")
    assert connectors.overview()["active"] == saved["id"]
    assert connectors.active()["api_key"] == "sk-test-1234567890"
    assert "api_key" not in str(connectors.overview()["connectors"])


def test_editing_keeps_the_key_unless_a_new_one_is_pasted():
    cid = connectors.save(openai_form())["id"]
    connectors.save(openai_form(id=cid, api_key="", model="deepseek-v4-pro"))
    connectors.save(openai_form(id=cid, api_key="••••••••••••"))
    assert connectors.active()["api_key"] == "sk-test-1234567890"
    connectors.save(openai_form(id=cid, api_key="sk-new-0987654321"))
    assert connectors.active()["api_key"] == "sk-new-0987654321"
    assert len(connectors.overview()["connectors"]) == 1


def test_a_model_is_required_and_rank_model_defaults_to_it():
    with pytest.raises(connectors.ConnectorError):
        connectors.save(openai_form(model=""))
    connectors.save(openai_form(rank_model=""))
    assert connectors.active()["rank_model"] == "deepseek-flash"


def test_activate_and_remove():
    a = connectors.save(openai_form(name="A"))["id"]
    b = connectors.save(openai_form(name="B"))["id"]
    assert connectors.overview()["active"] == a
    connectors.activate(b)
    assert connectors.active()["name"] == "B"
    connectors.remove(b)
    assert connectors.overview()["active"] == a
    connectors.remove(a)
    assert connectors.active() is None and connectors.overview()["active"] is None
    with pytest.raises(connectors.ConnectorError):
        connectors.activate("gone")


def test_state_survives_a_restart():
    connectors.save(openai_form())
    connectors.reset_cache()
    assert connectors.active()["model"] == "deepseek-flash"


def test_old_anthropic_key_becomes_the_first_connector(monkeypatch):
    saved = {}
    values = {"anthropic_api_key": "old-anthropic-key-123456", "llm_model": "claude-haiku-5-5",
              "llm_rank_model": "claude-sonnet-5-5"}
    monkeypatch.setattr(connectors.config, "get", lambda key: values.get(key, ""))
    monkeypatch.setattr(connectors.config, "save", lambda patch: saved.update(patch))
    conn = connectors.active()
    assert conn["name"] == "Anthropic" and conn["protocol"] == "anthropic" and conn["api_key"] == values["anthropic_api_key"]
    assert conn["model"] == "claude-haiku-5-5" and conn["rank_model"] == "claude-sonnet-5-5"
    assert saved == {"anthropic_api_key": ""}  # the secret now lives in one place
    assert "old-anthropic-key" not in connectors.FILE.read_text(encoding="utf-8")


def test_no_connector_when_nothing_is_set():
    assert connectors.active() is None
    assert connectors.overview()["connectors"] == [] and connectors.overview()["presets"]


# ------------------------------------------------------------------ OpenAI-compatible calls

def test_openai_request_shape(monkeypatch):
    calls = serve(monkeypatch, completion('{"ok": true}'))
    conn = connectors.save(openai_form()) and connectors.active()
    assert connectors.chat(conn, "hi", "system text", "deepseek-flash", 321) == '{"ok": true}'
    url, headers, body = calls[0]
    assert url == "https://api.deepseek.com/chat/completions"
    assert headers["Authorization"] == "Bearer sk-test-1234567890"
    assert body["model"] == "deepseek-flash" and body["max_tokens"] == 321
    assert body["response_format"] == {"type": "json_object"}
    assert body["messages"][0] == {"role": "system", "content": "system text"}


def test_a_local_server_needs_no_key(monkeypatch):
    calls = serve(monkeypatch, completion("{}"))
    connectors.save(openai_form(name="Ollama", base_url="http://localhost:11434/v1", api_key="", model="qwen"))
    connectors.chat(connectors.active(), "hi", "s", "qwen", 100)
    assert "Authorization" not in calls[0][1]


def test_learns_max_completion_tokens(monkeypatch):
    refusal = Reply({"error": {"message": "Unsupported parameter: 'max_tokens'. Use 'max_completion_tokens' instead."}},
                    400)
    calls = serve(monkeypatch, refusal, completion("{}"))
    connectors.save(openai_form())
    conn = connectors.active()
    connectors.chat(conn, "hi", "s", "deepseek-flash", 50)
    assert "max_tokens" in calls[0][2] and "max_completion_tokens" in calls[1][2] and "max_tokens" not in calls[1][2]
    connectors.chat(conn, "again", "s", "deepseek-flash", 50)  # remembered: no more failed first attempt
    assert "max_completion_tokens" in calls[2][2] and len(calls) == 3


def test_drops_json_mode_when_the_server_rejects_it(monkeypatch):
    refusal = Reply(None, 400, text="'response_format.type' must be 'json_schema' or 'text'")
    calls = serve(monkeypatch, refusal, completion("{}"))
    connectors.save(openai_form())
    connectors.chat(connectors.active(), "hi", "s", "deepseek-flash", 50)
    assert "response_format" in calls[0][2] and "response_format" not in calls[1][2]


def test_errors_are_worded_for_people(monkeypatch):
    connectors.save(openai_form())
    conn = connectors.active()
    for status, expect in ((401, "rejected the API key"), (404, "Fetch models"), (429, "rate limiting"),
                           (402, "out of credit"), (500, "answered 500")):
        serve(monkeypatch, Reply({"error": {"message": "nope sk-test-1234567890"}}, status))
        with pytest.raises(connectors.ConnectorError) as e:
            connectors.chat(conn, "hi", "s", "deepseek-flash", 50)
        assert expect in str(e.value) and "sk-test-1234567890" not in str(e.value)
    serve(monkeypatch, connectors.ConnectorError("Couldn't reach api.deepseek.com: offline"))
    with pytest.raises(connectors.ConnectorError, match="offline"):
        connectors.chat(conn, "hi", "s", "deepseek-flash", 50)


def test_reads_list_content_and_explains_empty_replies(monkeypatch):
    connectors.save(openai_form())
    conn = connectors.active()
    parts = Reply({"choices": [{"message": {"content": [{"type": "text", "text": '{"a"'}, {"type": "text", "text": ": 1}"}]}}]})
    serve(monkeypatch, parts)
    assert connectors.chat(conn, "hi", "s", "m", 10) == '{"a": 1}'
    serve(monkeypatch, completion("", finish="length"))
    with pytest.raises(connectors.ConnectorError, match="ran out of tokens"):
        connectors.chat(conn, "hi", "s", "m", 10)
    serve(monkeypatch, Reply(None, 200, text="<html>"))
    with pytest.raises(connectors.ConnectorError, match="OpenAI format"):
        connectors.chat(conn, "hi", "s", "m", 10)


def test_list_models(monkeypatch):
    seen = []

    def get(url, headers):
        seen.append((url, headers))
        return Reply({"data": [{"id": "models/gemini-3.8-flash"}, {"id": "gemini-4-hydrogen"}, {"id": "gemini-4-hydrogen"}]})
    monkeypatch.setattr(connectors, "_get", get)
    conn = {"protocol": "openai", "base_url": "https://x.example/v1", "api_key": "k"}
    assert connectors.list_models(conn) == ["gemini-3.8-flash", "gemini-4-hydrogen"]
    assert seen[0][0] == "https://x.example/v1/models" and seen[0][1]["Authorization"] == "Bearer k"
    assert seen[0][1]["User-Agent"].startswith("Surge/")
    connectors.list_models({"protocol": "anthropic", "base_url": "https://api.anthropic.com", "api_key": "k"})
    assert seen[1][0] == "https://api.anthropic.com/v1/models" and seen[1][1]["x-api-key"] == "k"
    monkeypatch.setattr(connectors, "_get", lambda url, headers: Reply(None, 401, text="no"))
    with pytest.raises(connectors.ConnectorError, match="rejected"):
        connectors.list_models(conn)


# ------------------------------------------------------------------ the AI writer on top

def test_parse_json_copes_with_what_models_add():
    assert llm.parse_json('{"a": 1}') == {"a": 1}
    assert llm.parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert llm.parse_json('<think>maybe {"x": 2}</think>\nSure! {"a": {"b": "}"}, "c": [1,],} Hope that helps.') == \
        {"a": {"b": "}"}, "c": [1]}
    for bad in ("no json here", '{"a": 1', "[1, 2]", '{"a": }'):
        with pytest.raises(ValueError):
            llm.parse_json(bad)


def test_the_writer_uses_the_right_model_for_clips_and_ranking(monkeypatch):
    connectors.save(openai_form())
    used = []

    def chat(conn, prompt, system, model, max_tokens=1500):
        used.append(model)
        return '{"hook": "He drove into the sea", "title": "Worst getaway", "picks": [{"id": "c1", "score": 8}]}'
    monkeypatch.setattr(connectors, "chat", chat)
    out = llm.write_clip({"streamer": "Testy", "words": [{"w": "no", "s": 0.0, "e": 0.3}], "duration": 30})
    assert out["source"] == "ai" and out["hook"] == "He drove into the sea" and used == ["deepseek-flash"]
    picks = llm.rank([{"id": "c1", "peak": 90, "score": 3.0, "words": []}], 1, "Testy", "GTA 6")
    assert picks == [{"id": "c1", "score": 8}] and used[-1] == "deepseek-v4-pro"


def test_the_writer_falls_back_when_the_service_fails(monkeypatch):
    connectors.save(openai_form())
    monkeypatch.setattr(connectors, "chat", lambda *a, **k: (_ for _ in ()).throw(connectors.ConnectorError("down")))
    out = llm.write_clip({"streamer": "Testy", "words": [], "duration": 30})
    assert out["source"] == "builtin" and out["hook"] and out["title"]


def test_no_connector_means_the_built_in_writer():
    assert not llm.enabled()
    assert llm.write_clip({"streamer": "Testy", "words": [], "duration": 30})["source"] == "builtin"


def test_connector_test_reports_plainly(monkeypatch):
    conn = {"protocol": "openai", "base_url": "https://x.example/v1", "api_key": "k", "model": "small", "rank_model": "big"}
    good = '{"hook": "He drove into the sea", "title": "Worst getaway", "score": 7, "hashtags": ["#gta6"], ' \
           '"picks": [{"id": "c03", "score": 8}]}'
    monkeypatch.setattr(connectors, "chat", lambda *a, **k: good)
    out = llm.test(conn)
    assert out["ok"] and "small works" in out["message"] and "big works too" in out["message"]
    monkeypatch.setattr(connectors, "chat", lambda *a, **k: "I think the hook should be: wow")
    out = llm.test(conn)
    assert not out["ok"] and "clean JSON" in out["message"]
    monkeypatch.setattr(connectors, "chat", lambda *a, **k: '{"score": 5}')
    assert "without the hook and title" in llm.test(conn)["message"]
    monkeypatch.setattr(connectors, "chat", lambda *a, **k: (_ for _ in ()).throw(connectors.ConnectorError("bad key")))
    assert llm.test(conn) == {"ok": False, "message": "bad key"}
    assert "No AI connector" in llm.test_active()["message"]


# ------------------------------------------------------------------ the HTTP API

def test_api_round_trip_never_returns_the_key(monkeypatch):
    from fastapi.testclient import TestClient

    from surge.api import app
    client = TestClient(app)
    monkeypatch.setattr(connectors, "_get", lambda url, headers: Reply({"data": [{"id": "deepseek-flash"}]}))
    r = client.post("/api/connectors", json=openai_form())
    assert r.status_code == 200 and "sk-test-1234567890" not in r.text
    body = r.json()
    cid = body["active"]
    assert body["connectors"][0]["has_key"] and body["presets"] and body["recommended"]["picks"]
    listed = client.post("/api/connectors/models", json={"id": cid}).json()
    assert listed["models"] == ["deepseek-flash"] and listed["best"]["clip"] == "deepseek-flash"
    assert "sk-test-1234567890" not in str(listed)
    assert client.post("/api/connectors", json=openai_form(model="")).status_code == 400
    assert client.post("/api/connectors/test", json={"base_url": "http://example.com"}).json()["ok"] is False
    assert client.delete(f"/api/connectors/{cid}").json()["connectors"] == []
