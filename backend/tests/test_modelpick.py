"""Picking models: what each service offers with prices, the best and cheapest for clips and ranking, picking by
testing, and the OpenCode Zen / Go and Gemini specifics."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from surge import connectors, llm, modelpick  # noqa: E402

GO = {"id": "go", "name": "OpenCode Go", "protocol": "openai", "base_url": "https://opencode.ai/inference/go/openai/v1",
      "api_key": "oc_sk_test_123456", "model": "", "rank_model": ""}
ZEN = {**GO, "id": "zen", "name": "OpenCode Zen", "base_url": "https://opencode.ai/inference/openai/v1"}
GEMINI = {"id": "gem", "name": "Google Gemini", "protocol": "openai", "api_key": "AIza-test-123456", "model": "",
          "rank_model": "", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai"}


def oc(mid, price_in, price_out, released="2026-10-01", kind="chat", allowance=None, context=1_000_000):
    row = {"id": mid, "type": kind, "release_date": released, "limit": {"context": context},
           "cost": {"text": {"input": {"price": price_in}, "output": {"price": price_out}}}}
    if allowance is not None:
        row["go"] = {"monthly_usage": {"go": allowance}}
    return row


GO_ROWS = [oc("deepseek-v4.1-flash", 0.15, 0.6, "2026-10-17", allowance=100), oc("glm-5.3", 1.4, 4.4, allowance=500),
           oc("claude-haiku-5-5", 0.1, 0.5, "2026-10-07", allowance=15), oc("beam-free", 0, 0),
           oc("text-embedding-3-small", 0.02, 0, kind="embedding"), oc("gpt-5.5-pro", 30, 180)]


@pytest.fixture(autouse=True)
def clean(monkeypatch, tmp_path):
    monkeypatch.setattr(modelpick, "_memo", {})
    monkeypatch.setattr(connectors, "CHECKS_FILE", tmp_path / "model_checks.json")
    monkeypatch.setattr(modelpick, "_reference", lambda: {
        "gemini-3-8-flash": oc("gemini-3.8-flash", 0.75, 3.75, "2026-09-02"),
        "gemini-4-hydrogen": oc("gemini-4-hydrogen", 1.5, 7.5, "2026-11-12"),
        "claude-haiku-5-5": oc("claude-haiku-5-5", 0.1, 0.5, "2026-10-07")})


def rows_for(monkeypatch, rows):
    monkeypatch.setattr(connectors, "model_rows", lambda conn: rows)


def test_opencode_go_offer_prices_and_rules(monkeypatch):
    rows_for(monkeypatch, GO_ROWS)
    got = {r["id"]: r for r in modelpick.offer(GO)}
    assert set(got) == {"deepseek-v4.1-flash", "glm-5.3", "claude-haiku-5-5"}  # free-only, embedding, $30 model out
    haiku = got["claude-haiku-5-5"]
    assert haiku["clip_cost"] == pytest.approx(0.305)  # dollars per 1,000 clips
    assert haiku["clip_note"] == "In your Go plan: about 49,180 clips a month"
    assert [r["id"] for r in modelpick.order(list(got.values()), "clip")][0] == "claude-haiku-5-5"
    assert modelpick.order(list(got.values()), "rank")[0]["id"] == "glm-5.3"
    assert modelpick.cheapest(list(got.values()), "clip")["id"] == "deepseek-v4.1-flash"  # most clips per allowance


def test_older_versions_rank_after_the_newest_of_each_family(monkeypatch):
    rows_for(monkeypatch, [oc("claude-haiku-4-5", 1, 5, "2025-10-15"), oc("claude-haiku-5-5", 0.1, 0.5, "2026-10-07"),
                           oc("gpt-6-luna", 0.1, 0.5, "2026-09-22")])
    assert [r["id"] for r in modelpick.order(modelpick.offer(ZEN), "clip")] == \
        ["claude-haiku-5-5", "gpt-6-luna", "claude-haiku-4-5"]


def test_gemini_free_key_flash_first_and_prices_from_the_reference(monkeypatch):
    rows_for(monkeypatch, [{"id": "models/gemini-3.8-flash"}, {"id": "models/gemini-4-hydrogen"},
                           {"id": "models/gemini-embedding-2"}, {"id": "models/imagen-5"}, {"id": "models/gemma-4-31b-it"}])
    rows = modelpick.offer(GEMINI)
    by = {r["id"]: r for r in rows}
    assert set(by) == {"gemini-3.8-flash", "gemini-4-hydrogen", "gemma-4-31b-it"}
    assert by["gemini-3.8-flash"]["free"] == "Free tier with daily limits" and by["gemini-3.8-flash"]["clip_cost"] == 0
    assert by["gemini-4-hydrogen"]["free"] is None and by["gemini-4-hydrogen"]["rank_note"].endswith("per broadcast")
    assert modelpick.order(rows, "clip")[0]["id"] == "gemini-3.8-flash"
    assert modelpick.order(rows, "rank")[0]["id"] == "gemini-4-hydrogen"
    listing = modelpick.listing(rows)
    assert listing["cheapest"]["clip"] == "gemini-3.8-flash" and listing["best"]["rank"] == "gemini-4-hydrogen"


def test_autopick_keeps_the_first_model_that_passes(monkeypatch):
    rows_for(monkeypatch, [{"id": "models/gemini-3.8-flash"}, {"id": "models/gemini-4-hydrogen"}])
    calls = []

    def clip(conn, model, retry=True):
        calls.append(("clip", model, retry))
        return True, f"{model} works"

    def rank(conn, model, retry=True):
        calls.append(("rank", model, retry))
        return False, "429: limit 0 on the free tier"
    monkeypatch.setattr(llm, "check_clip", clip)
    monkeypatch.setattr(llm, "check_rank", rank)
    out = modelpick.autopick(GEMINI)
    assert out["ok"] and out["model"] == out["rank_model"] == "gemini-3.8-flash"  # the paid model failed: same model
    assert calls[0] == ("clip", "gemini-3.8-flash", False) and ("rank", "gemini-4-hydrogen", False) in calls
    assert "for ranking too" in out["message"] and len(out["tried"]) == 2

    monkeypatch.setattr(llm, "check_clip", lambda conn, model, retry=True: (False, f"{model}: not clean JSON"))
    bad = modelpick.autopick(GEMINI)
    assert not bad["ok"] and "not clean JSON" in bad["message"]


def test_best_and_cheapest_across_connectors(monkeypatch):
    lists = {GO["base_url"]: GO_ROWS, GEMINI["base_url"]: [{"id": "models/gemini-3.8-flash"}]}
    monkeypatch.setattr(connectors, "model_rows", lambda conn: lists[conn["base_url"]])
    out = modelpick.across([GO, GEMINI, {**ZEN, "api_key": ""}])  # no key: skipped
    clip = out["picks"]["clip"]
    assert clip["best"]["id"] == "claude-haiku-5-5" and clip["best"]["connector_name"] == "OpenCode Go"
    assert clip["cheapest"]["id"] == "gemini-3.8-flash" and clip["cheapest"]["connector"] == "gem"
    assert out["errors"] == []
    monkeypatch.setattr(connectors, "model_rows",
                        lambda conn: (_ for _ in ()).throw(connectors.ConnectorError("bad key")))
    modelpick.forget()
    assert modelpick.across([GEMINI])["errors"][0]["error"] == "bad key"


def test_models_that_failed_on_this_key_leave_the_picks(monkeypatch):
    rows_for(monkeypatch, GO_ROWS)
    connectors.record_check(GO, "claude-haiku-5-5", "clip", False, "answered in prose")
    connectors.record_check(GO, "deepseek-v4.1-flash", "clip", True, "works")
    best = modelpick.across([GO])["picks"]["clip"]["best"]
    assert best["id"] == "deepseek-v4.1-flash" and best["checked"]["clip"] is True
    assert modelpick.across([{**GO, "api_key": "another-key-999"}])["picks"]["clip"]["best"]["id"] == "claude-haiku-5-5"
    assert "oc_sk_test" not in connectors.CHECKS_FILE.read_text(encoding="utf-8")


def test_openrouter_prices_and_free_models(monkeypatch):
    conn = {**GEMINI, "base_url": "https://openrouter.ai/api/v1"}
    rows_for(monkeypatch, [{"id": "anthropic/claude-haiku-5.5", "pricing": {"prompt": "0.0000001", "completion": "0.0000005"},
                            "context_length": 1_000_000},
                           {"id": "deepseek/deepseek-v4.1-flash:free", "pricing": {"prompt": "0", "completion": "0"}},
                           {"id": "openrouter/auto", "pricing": {"prompt": "-1", "completion": "-1"}},
                           {"id": "x/image-maker", "architecture": {"output_modalities": ["image"]}}])
    by = {r["id"]: r for r in modelpick.offer(conn)}
    assert by["anthropic/claude-haiku-5.5"]["price_in"] == pytest.approx(0.1)
    assert by["deepseek/deepseek-v4.1-flash:free"]["free"] == "Free on OpenRouter, rate limited"
    assert by["openrouter/auto"]["price_in"] is None and "x/image-maker" not in by


def test_opencode_requests_name_surge_and_keep_a_session(monkeypatch):
    sent = []

    def post(url, headers, body, retry=True):
        sent.append((url, headers, retry))
        return type("R", (), {"status_code": 200, "text": "", "json": lambda self: {"choices": [{"message": {"content": "{}"}}]}})()
    monkeypatch.setattr(connectors, "_post", post)
    connectors.chat(ZEN, "hi", "s", "claude-haiku-5-5", 50)
    connectors.chat(ZEN, "again", "s", "claude-haiku-5-5", 50, retry=False)
    url, headers, retry = sent[0]
    assert url == "https://opencode.ai/inference/openai/v1/chat/completions"
    assert headers["User-Agent"].startswith("Surge/") and headers["x-opencode-session"].startswith("ses_")
    assert sent[1][1]["x-opencode-session"] == headers["x-opencode-session"] and sent[1][2] is False
    seen = []
    monkeypatch.setattr(connectors, "_get", lambda url, headers: seen.append(url) or
                        type("R", (), {"status_code": 200, "json": lambda self: {"data": []}})())
    connectors.model_rows(GO)
    connectors.model_rows(ZEN)
    assert seen == [connectors.OPENCODE_GO_MODELS, connectors.OPENCODE_MODELS]


def test_quick_presets_exist():
    ids = {p["id"]: p for p in connectors.PRESETS}
    assert ids["gemini"]["quick"] and ids["opencode-go"]["quick"] and ids["opencode-zen"]["quick"]
    assert ids["opencode-go"]["base_url"] == "https://opencode.ai/inference/go/openai/v1"
    assert ids["opencode-zen"]["base_url"] == "https://opencode.ai/inference/openai/v1"
    assert connectors.clean_url(ids["gemini"]["base_url"], "openai") == \
        "https://generativelanguage.googleapis.com/v1beta/openai"
