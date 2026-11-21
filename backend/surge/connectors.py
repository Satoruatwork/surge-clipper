"""AI connectors: where the AI writer sends its requests. A connector is a base URL, an API key and two model names, for
Anthropic or for any OpenAI-compatible service (OpenAI, Gemini, DeepSeek, Groq, OpenRouter, Ollama, LM Studio...).
Keys are encrypted at rest with Windows DPAPI and never leave the engine: the API only reports whether one is set."""
from __future__ import annotations

import ipaddress
import json
import re
import threading
import time
import uuid
from urllib.parse import urlparse

import httpx

from . import config, vault
from .config import DATA

FILE = DATA / "connectors.json"
PROTOCOLS = ("openai", "anthropic")
ANTHROPIC_VERSION = "2023-06-01"

# Starting points for the "Add connector" form. Model names are what these services list today; they rename models
# often, so the form also has Fetch models, and Test checks that a model really works with Surge's prompts.
PRESETS = [
    {"id": "anthropic", "name": "Anthropic", "protocol": "anthropic", "base_url": "https://api.anthropic.com",
     "model": "claude-haiku-5-5", "rank_model": "claude-sonnet-5-5",
     "key_url": "https://console.anthropic.com/settings/keys",
     "note": "Best results: Surge's prompts were written and tuned with Claude."},
    {"id": "openai", "name": "OpenAI", "protocol": "openai", "base_url": "https://api.openai.com/v1",
     "model": "gpt-6-luna", "rank_model": "gpt-6.2-sol", "key_url": "https://platform.openai.com/api-keys",
     "note": "gpt-6-luna is the cheap fast tier; gpt-6.2-sol is stronger for ranking."},
    {"id": "gemini", "name": "Google Gemini", "protocol": "openai",
     "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
     "model": "gemini-3.8-flash", "rank_model": "gemini-4-hydrogen", "key_url": "https://aistudio.google.com/apikey",
     "note": "Google AI Studio keys may have a free tier with limits."},
    {"id": "deepseek", "name": "DeepSeek", "protocol": "openai", "base_url": "https://api.deepseek.com",
     "model": "deepseek-flash", "rank_model": "deepseek-v4-pro", "key_url": "https://platform.deepseek.com/api_keys",
     "note": "The cheapest of the big options."},
    {"id": "openrouter", "name": "OpenRouter", "protocol": "openai", "base_url": "https://openrouter.ai/api/v1",
     "model": "deepseek/deepseek-v4.1-flash", "rank_model": "anthropic/claude-sonnet-5.5",
     "key_url": "https://openrouter.ai/keys",
     "note": "One key, hundreds of models. Model names ending in :free cost nothing but are rate limited."},
    {"id": "groq", "name": "Groq", "protocol": "openai", "base_url": "https://api.groq.com/openai/v1",
     "model": "", "rank_model": "", "key_url": "https://console.groq.com/keys",
     "note": "Very fast. Press Fetch models and pick a large instruct model."},
    {"id": "ollama", "name": "Ollama (on this PC)", "protocol": "openai", "base_url": "http://localhost:11434/v1",
     "model": "", "rank_model": "", "key_url": None,
     "note": "Free and private. Needs no key. Use a 14B or larger instruct model: smaller ones often break the JSON."},
    {"id": "lmstudio", "name": "LM Studio (on this PC)", "protocol": "openai", "base_url": "http://localhost:1234/v1",
     "model": "", "rank_model": "", "key_url": None,
     "note": "Free and private. Start its local server, then press Fetch models."},
    {"id": "custom-openai", "name": "Custom (OpenAI-compatible)", "protocol": "openai", "base_url": "",
     "model": "", "rank_model": "", "key_url": None,
     "note": "Any service that offers /chat/completions: paste its base URL and key."},
    {"id": "custom-anthropic", "name": "Custom (Anthropic-compatible)", "protocol": "anthropic", "base_url": "",
     "model": "", "rank_model": "", "key_url": None,
     "note": "A service that speaks Anthropic's /v1/messages format."},
]

RECOMMENDED = {
    "intro": "Surge asks the model for strict JSON: a score, a hook, a title, hashtags and an edit plan. Pick a model "
             "that follows instructions and answers with clean JSON.",
    "picks": [
        {"label": "Best results", "detail": "Anthropic: Claude Haiku 5.5 for each clip and Claude Sonnet 5.5 for ranking a "
                                            "broadcast. Surge's prompts were written and tuned with Claude."},
        {"label": "No Anthropic account", "detail": "OpenAI (gpt-6-luna for clips, gpt-6.2-sol for ranking), Google Gemini "
                                                    "(gemini-3.8-flash, gemini-4-hydrogen) or DeepSeek (deepseek-flash, "
                                                    "deepseek-v4-pro, the cheapest). Any OpenAI-compatible service works: "
                                                    "paste its base URL and key."},
        {"label": "Free", "detail": "OpenRouter models whose name ends in :free (for example Gemma 4 31B), a free Google AI "
                                    "Studio key, or a model on your own PC with Ollama or LM Studio. Free tiers change and "
                                    "are rate limited."},
        {"label": "On your own PC", "detail": "Use a 14B or larger instruct model. Models under 8B often break the JSON, "
                                              "and then Surge falls back to its built-in writer for that clip."},
    ],
    "rules": [
        "Each clip is one short request: pick a fast, cheap model that does not think first. On the cheap tier that is "
        "roughly a tenth of a cent per clip.",
        "Ranking a broadcast sends about 15,000 to 20,000 tokens once: pick a stronger model with at least 64k context.",
        "Press Test before you rely on a model. Surge sends a real clip prompt and checks that the reply is clean JSON.",
        "Model names change often. Fetch models shows what your service offers today.",
    ],
}


class ConnectorError(Exception):
    """A problem with a connector, worded for the person using the app."""


_lock = threading.RLock()
_cache: dict | None = None
_quirks: dict[tuple[str, str], dict] = {}  # per server and model: parameters it turned out not to accept


# ------------------------------------------------------------------ storage

def _write(data: dict) -> None:
    disk = {"active": data["active"],
            "items": [{**i, "api_key": vault.seal(i["api_key"]) if i.get("api_key") else ""} for i in data["items"]]}
    FILE.parent.mkdir(parents=True, exist_ok=True)
    FILE.write_text(json.dumps(disk, indent=2), encoding="utf-8")


def _migrate() -> dict:
    """First start after the connectors arrived: the old single Anthropic key becomes the first connector."""
    legacy = config.get("anthropic_api_key")
    if not legacy:
        return {"active": None, "items": []}
    item = {"id": "anthropic", "name": "Anthropic", "protocol": "anthropic", "base_url": "https://api.anthropic.com",
            "api_key": legacy, "model": config.get("llm_model") or "claude-haiku-5-5",
            "rank_model": config.get("llm_rank_model") or "claude-sonnet-5-5"}
    data = {"active": "anthropic", "items": [item]}
    _write(data)
    config.save({"anthropic_api_key": ""})  # one place for the secret
    return data


def _load() -> dict:
    global _cache
    with _lock:
        if _cache is not None:
            return _cache
        try:
            raw = json.loads(FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = None
        if isinstance(raw, dict) and isinstance(raw.get("items"), list):
            items = []
            for i in raw["items"]:
                if isinstance(i, dict) and i.get("id") and i.get("base_url") and i.get("protocol") in PROTOCOLS:
                    items.append({"id": str(i["id"]), "name": str(i.get("name") or i["id"]), "protocol": i["protocol"],
                                  "base_url": str(i["base_url"]), "api_key": vault.unseal(str(i.get("api_key") or "")),
                                  "model": str(i.get("model") or ""), "rank_model": str(i.get("rank_model") or "")})
            _cache = {"active": raw.get("active") if any(x["id"] == raw.get("active") for x in items) else
                      (items[0]["id"] if items else None), "items": items}
        else:
            _cache = _migrate()
        return _cache


def public(item: dict) -> dict:
    key = item.get("api_key") or ""
    return {"id": item["id"], "name": item["name"], "protocol": item["protocol"], "base_url": item["base_url"],
            "model": item.get("model", ""), "rank_model": item.get("rank_model", ""), "has_key": bool(key),
            "key_hint": ("…" + key[-4:]) if len(key) >= 12 else ""}


def overview() -> dict:
    with _lock:
        data = _load()
        return {"connectors": [public(i) for i in data["items"]], "active": data["active"],
                "presets": PRESETS, "recommended": RECOMMENDED}


def active() -> dict | None:
    """The connector the AI writer uses, with its key. For the engine only."""
    with _lock:
        data = _load()
        item = next((i for i in data["items"] if i["id"] == data["active"]), None)
        return dict(item) if item and item.get("model") else None


# ------------------------------------------------------------------ validation

def _local(host: str) -> bool:
    host = host.lower()
    if host == "localhost" or host.endswith((".localhost", ".local")):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private or ip.is_link_local


def clean_url(raw: object, protocol: str) -> str:
    url = str(raw or "").strip()
    if not url:
        raise ConnectorError("Add the base URL, for example https://api.openai.com/v1")
    if "://" not in url:
        host = url.split("/")[0].rsplit("@", 1)[-1].split(":")[0]
        url = ("http://" if _local(host) else "https://") + url
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise ConnectorError("The base URL must start with http:// or https:// and name a server.")
    if p.username or p.password:
        raise ConnectorError("Put the key in the API key box, not in the address.")
    if p.scheme == "http" and not _local(p.hostname):
        raise ConnectorError("Use an https address for services on the internet. Plain http is only for localhost "
                             "and your own network.")
    path = p.path.rstrip("/")
    for tail in ("/chat/completions", "/completions", "/messages"):  # someone pasted the whole endpoint
        if path.endswith(tail):
            path = path[: -len(tail)]
    if protocol == "anthropic" and path.endswith("/v1"):  # the Anthropic client adds /v1 itself
        path = path[:-3]
    return f"{p.scheme}://{p.netloc}{path}"


def _new_key(raw: object) -> str | None:
    key = str(raw or "").strip()
    return None if not key or set(key) <= {"•", "*"} else key


def _field(body: dict, existing: dict | None, key: str) -> str:
    """A field the form sent, or (when it sent none) the saved one. An empty field the form did send stays empty."""
    return str(body[key] or "") if key in body else str((existing or {}).get(key) or "")


def _draft(body: dict, existing: dict | None, need_model: bool = True) -> dict:
    protocol = body.get("protocol") if body.get("protocol") in PROTOCOLS else (existing or {}).get("protocol", "openai")
    base_url = clean_url(_field(body, existing, "base_url"), protocol)
    name = _field(body, existing, "name").strip()[:60] or urlparse(base_url).hostname or "Connector"
    model = _field(body, existing, "model").strip()[:120]
    if need_model and not model:
        raise ConnectorError("Pick a model for clips. Press Fetch models to see what the service offers.")
    rank = _field(body, existing, "rank_model").strip()[:120] or model
    key = _new_key(body.get("api_key"))
    if key is None:
        key = (existing or {}).get("api_key", "")
    return {"name": name, "protocol": protocol, "base_url": base_url, "api_key": key, "model": model,
            "rank_model": rank}


def draft(body: dict, need_model: bool = True) -> dict:
    """A connector built from what the form holds, saved or not, so Test and Fetch models work before Save."""
    with _lock:
        existing = next((i for i in _load()["items"] if i["id"] == body.get("id")), None)
    return _draft(body, existing, need_model)


# ------------------------------------------------------------------ changes

def save(body: dict) -> dict:
    with _lock:
        data = _load()
        existing = next((i for i in data["items"] if i["id"] == body.get("id")), None)
        item = _draft(body, existing)
        if existing is None:
            base = re.sub(r"[^a-z0-9]+", "-", item["name"].lower()).strip("-") or "connector"
            item["id"] = f"{base}-{uuid.uuid4().hex[:4]}"
            data["items"].append(item)
            saved = item
        else:
            existing.update(item)
            saved = existing
        if not data["active"]:
            data["active"] = saved["id"]
        _write(data)
        return public(saved)


def remove(cid: str) -> None:
    with _lock:
        data = _load()
        data["items"] = [i for i in data["items"] if i["id"] != cid]
        if data["active"] == cid:
            data["active"] = data["items"][0]["id"] if data["items"] else None
        _write(data)


def activate(cid: str) -> None:
    with _lock:
        data = _load()
        if not any(i["id"] == cid for i in data["items"]):
            raise ConnectorError("That connector no longer exists.")
        data["active"] = cid
        _write(data)


def reset_cache() -> None:
    global _cache
    with _lock:
        _cache = None


# ------------------------------------------------------------------ talking to the service

def _host(conn: dict) -> str:
    return urlparse(conn["base_url"]).netloc


def _error_text(text: str) -> str:
    try:
        data = json.loads(text)
    except ValueError:
        return text.strip()[:200]
    err = data.get("error", data) if isinstance(data, dict) else data
    if isinstance(err, dict):
        err = err.get("message") or err.get("detail") or json.dumps(err)
    return str(err).strip()[:200]


def _http_error(conn: dict, status: int, text: str, model: str) -> ConnectorError:
    host = _host(conn)
    key = conn.get("api_key") or ""
    detail = _error_text(text)
    detail = detail.replace(key, "***") if key else detail
    if status in (401, 403):
        return ConnectorError(f"{host} rejected the API key ({status}). Check the key, and that it is for this service.")
    if status == 404:
        return ConnectorError(f"{host} answered 404: the base URL or the model '{model}' is wrong. "
                              "Press Fetch models to see what it offers.")
    if status == 402:
        return ConnectorError(f"{host} says the account is out of credit (402). {detail}")
    if status == 429:
        return ConnectorError(f"{host} is rate limiting this key, or the account is out of quota (429). {detail}")
    return ConnectorError(f"{host} answered {status}: {detail}")


def _post(url: str, headers: dict, body: dict) -> httpx.Response:
    for attempt in range(3):
        try:
            r = httpx.post(url, headers=headers, json=body, timeout=httpx.Timeout(120.0, connect=10.0),
                           follow_redirects=True)
        except httpx.HTTPError as e:
            raise ConnectorError(f"Couldn't reach {urlparse(url).netloc}: {e}") from e
        if r.status_code in (429, 500, 502, 503, 504) and attempt < 2:
            try:
                wait = float(r.headers.get("retry-after") or 2 * (attempt + 1))
            except ValueError:
                wait = 2.0 * (attempt + 1)
            time.sleep(min(wait, 10.0))
            continue
        return r
    return r


def _get(url: str, headers: dict) -> httpx.Response:
    try:
        return httpx.get(url, headers=headers, timeout=httpx.Timeout(30.0, connect=10.0), follow_redirects=True)
    except httpx.HTTPError as e:
        raise ConnectorError(f"Couldn't reach {urlparse(url).netloc}: {e}") from e


def _chat_anthropic(conn: dict, prompt: str, system: str, model: str, max_tokens: int) -> str:
    import anthropic
    client = anthropic.Anthropic(api_key=conn.get("api_key") or "none", base_url=conn["base_url"], max_retries=2,
                                 timeout=90)
    try:
        msg = client.messages.create(model=model, max_tokens=max_tokens, system=system,
                                     messages=[{"role": "user", "content": prompt}])
    except anthropic.APIStatusError as e:
        raise _http_error(conn, e.status_code, getattr(e, "message", "") or str(e), model) from e
    except anthropic.APIConnectionError as e:
        raise ConnectorError(f"Couldn't reach {_host(conn)}: {e}") from e
    return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")


def _chat_openai(conn: dict, prompt: str, system: str, model: str, max_tokens: int) -> str:
    url = conn["base_url"] + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if conn.get("api_key"):
        headers["Authorization"] = "Bearer " + conn["api_key"]
    quirk = _quirks.setdefault((conn["base_url"], model), {})
    r = None
    for _ in range(4):  # servers differ on a few parameters: learn what this one rejects, then stop sending it
        body = {"model": model, "messages": [{"role": "system", "content": system},
                                             {"role": "user", "content": prompt}]}
        body["max_completion_tokens" if quirk.get("completion_tokens") else "max_tokens"] = max_tokens
        if not quirk.get("no_json_mode"):
            body["response_format"] = {"type": "json_object"}
        r = _post(url, headers, body)
        if r.status_code != 400:
            break
        why = r.text.lower()
        if "max_completion_tokens" in why and not quirk.get("completion_tokens"):
            quirk["completion_tokens"] = True
        elif ("response_format" in why or "json_object" in why or "json mode" in why) and not quirk.get("no_json_mode"):
            quirk["no_json_mode"] = True
        else:
            break
    if r.status_code >= 400:
        raise _http_error(conn, r.status_code, r.text, model)
    try:
        choice = (r.json().get("choices") or [{}])[0]
    except ValueError as e:
        raise ConnectorError(f"{_host(conn)} did not answer in the OpenAI format. Is the base URL right?") from e
    content = (choice.get("message") or {}).get("content")
    if isinstance(content, list):
        content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
    text = content or choice.get("text") or ""
    if not str(text).strip():
        why = " because it ran out of tokens, which thinking models do. Pick a model that answers directly." \
            if choice.get("finish_reason") == "length" else "."
        raise ConnectorError(f"{model} returned an empty reply{why}")
    return str(text)


def chat(conn: dict, prompt: str, system: str, model: str, max_tokens: int = 1500) -> str:
    """One request to the connector's service. Returns the reply text or raises ConnectorError."""
    if conn["protocol"] == "anthropic":
        return _chat_anthropic(conn, prompt, system, model, max_tokens)
    return _chat_openai(conn, prompt, system, model, max_tokens)


def list_models(conn: dict) -> list[str]:
    if conn["protocol"] == "anthropic":
        url = conn["base_url"] + "/v1/models"
        headers = {"x-api-key": conn.get("api_key") or "none", "anthropic-version": ANTHROPIC_VERSION}
    else:
        url = conn["base_url"] + "/models"
        headers = {"Authorization": "Bearer " + conn["api_key"]} if conn.get("api_key") else {}
    r = _get(url, headers)
    if r.status_code >= 400:
        raise _http_error(conn, r.status_code, r.text, "")
    try:
        data = r.json()
    except ValueError as e:
        raise ConnectorError(f"{_host(conn)} did not answer with a model list. Is the base URL right?") from e
    rows = data.get("data") or data.get("models") or [] if isinstance(data, dict) else data
    ids = {str(m.get("id") or m.get("name") or "").removeprefix("models/") for m in rows if isinstance(m, dict)}
    return sorted(i for i in ids if i)[:500]
