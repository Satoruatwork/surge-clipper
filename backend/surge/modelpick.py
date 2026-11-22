"""Which model to use. Lists what a connector's service offers with what Surge knows about each model (cost per 1,000
clips, free tier, how new it is), ranks the candidates for clips and for ranking a broadcast, and picks by testing: a
model is only chosen after Surge's own test prompt comes back as clean JSON.

Prices come from the services that publish them: OpenCode's public model lists, which sell at the providers' list
prices and so also price Claude, GPT and Gemini keys, and OpenRouter's list. Google's free tier is from its pricing
page (Flash and Gemma models are free with daily limits)."""
from __future__ import annotations

import re
import threading
import time
from datetime import datetime
from urllib.parse import urlparse

from . import connectors, llm

CLIP_TOKENS = (1800, 250)    # roughly what one clip prompt and its reply cost, in tokens
RANK_TOKENS = (18000, 600)   # ranking a broadcast
FAST_INPUT = 1.0             # $ per 1M input tokens: at or under this a model counts as the fast, cheap tier
PRICEY_INPUT = 15.0          # never offer a model whose input costs more than this per 1M tokens
MIN_RANK_CONTEXT = 64_000

# Families Surge's prompts do well with, best first. They only order the candidates: the test decides.
CLIP_FAMILIES = [r"claude-haiku", r"gpt-\d+(\.\d+)?-luna", r"gemini-\d+(\.\d+)?-flash$", r"deepseek-v\d+(\.\d+)?-flash",
                 r"glm-\d+(\.\d+)?-flash", r"qwen\d+(\.\d+)?-flash", r"muse-ember", r"mimo-v\d+(\.\d+)?-flash",
                 r"minimax-m\d+(\.\d+)?-flash", r"gemini-\d+(\.\d+)?-flash-lite", r"deepseek-flash"]
RANK_FAMILIES = [r"claude-sonnet", r"gpt-\d+(\.\d+)?-sol", r"gemini-\d+-hydrogen", r"kimi-k\d+(\.\d+)?-pro",
                 r"qwen\d+(\.\d+)?-max", r"glm-\d+(\.\d+)?$", r"deepseek-v\d+(\.\d+)?-pro",
                 r"gemini-\d+-(argon|carbon|neon)", r"grok-\d+(\.\d+)?$", r"kimi-k\d+(\.\d+)?$", r"mistral-large"]
FAMILIES = {"clip": CLIP_FAMILIES, "rank": RANK_FAMILIES}
NOT_CHAT = re.compile(r"embed|image|imagen|veo|tts|audio|realtime|live|transcri|whisper|moderation|guard|decision|"
                      r"banana|aqa|robotics|computer-use|search|rerank", re.I)
FAST_NAME = re.compile(r"flash|mini|nano|lite|luna|haiku|small|instant|turbo|\b[1-9]b\b|-[1-9]b", re.I)

_lock = threading.Lock()
_memo: dict = {}


def _remember(key, ttl: float, make):
    now = time.time()
    with _lock:
        hit = _memo.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    value = make()
    with _lock:
        _memo[key] = (now, value)
    return value


def forget() -> None:
    with _lock:
        _memo.clear()


# ------------------------------------------------------------------ reading a model row

def _num(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f >= 0 else None  # OpenRouter prices its routers at -1: unknown


def bare(mid: str) -> str:
    """The model's own name: no vendor prefix, no :tag, lower case."""
    return mid.lower().rsplit("/", 1)[-1].split(":", 1)[0]


def _key(mid: str) -> str:
    """One spelling across services (OpenRouter writes claude-sonnet-5.5 where others write claude-sonnet-5-5)."""
    return bare(mid).replace(".", "-")


def _prices(row: dict) -> tuple[float | None, float | None]:
    text = ((row.get("cost") or {}).get("text") or {}) if isinstance(row.get("cost"), dict) else {}
    if text:  # OpenCode: dollars per 1M tokens
        return _num((text.get("input") or {}).get("price")), _num((text.get("output") or {}).get("price"))
    pricing = row.get("pricing") if isinstance(row.get("pricing"), dict) else {}
    if pricing:  # OpenRouter: dollars per token, as strings
        p, c = _num(pricing.get("prompt")), _num(pricing.get("completion"))
        return (None if p is None else p * 1e6), (None if c is None else c * 1e6)
    return None, None


def _released(row: dict) -> float:
    for k in ("release_date", "created_at", "created"):
        v = row.get(k)
        if isinstance(v, (int, float)) and v > 1e9:
            return float(v)
        if isinstance(v, str) and v[:4].isdigit():
            try:
                return datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp()
            except ValueError:
                pass
    return 0.0


def _context(row: dict) -> int | None:
    v = (row.get("limit") or {}).get("context") if isinstance(row.get("limit"), dict) else None
    v = v or row.get("context_length") or row.get("inputTokenLimit") or row.get("context_window")
    return int(v) if isinstance(v, (int, float)) and v > 0 else None


def _fit(name: str, role: str) -> int:
    fams = FAMILIES[role]
    return next((i for i, f in enumerate(fams) if re.search(f, name)), len(fams))


def _reference() -> dict[str, dict]:
    """OpenCode's public list of paid models, keyed by model name: list prices for Claude, GPT, Gemini, DeepSeek..."""
    def load():
        try:
            r = connectors._get(connectors.OPENCODE_MODELS, {"User-Agent": connectors.USER_AGENT})
            rows = (r.json() or {}).get("data") or [] if r.status_code < 400 else []
        except (connectors.ConnectorError, ValueError):
            return {}
        return {_key(str(m.get("id"))): m for m in rows if isinstance(m, dict) and m.get("id")}
    return _remember("reference", 12 * 3600, load)


def _free_reason(conn: dict, mid: str, price_in: float | None, price_out: float | None) -> str | None:
    host = (urlparse(conn["base_url"]).hostname or "").lower()
    if connectors._local(host):
        return "Free, runs on your PC"
    if host.endswith("openrouter.ai") and (mid.endswith(":free") or (price_in == 0 and price_out == 0)):
        return "Free on OpenRouter, rate limited"
    if host.endswith("googleapis.com") and re.search(r"flash|gemma", bare(mid)):
        return "Free tier with daily limits"
    return None


def _usable(conn: dict, mid: str, row: dict, price_in: float | None) -> bool:
    if row.get("type") not in (None, "chat"):  # OpenCode marks chat models
        return False
    outputs = (row.get("architecture") or {}).get("output_modalities") if isinstance(row.get("architecture"), dict) else None
    if outputs and "text" not in outputs:  # OpenRouter
        return False
    if NOT_CHAT.search(mid):
        return False
    if connectors.is_opencode(conn) and (price_in == 0 or mid.endswith("-free")):
        return False  # OpenCode's free models answer 403 outside OpenCode
    return price_in is None or price_in <= PRICEY_INPUT


def _per(tokens: tuple[int, int], price_in: float | None, price_out: float | None) -> float | None:
    if price_in is None or price_out is None:
        return None
    return (tokens[0] * price_in + tokens[1] * price_out) / 1e6


def _money(v: float) -> str:
    return f"${v:.2f}" if v >= 0.1 else f"${v:.3f}" if v >= 0.001 else "under $0.001"


def _unit_cost(row: dict, role: str) -> float | None:
    """Dollars for one clip or one broadcast."""
    if role == "clip":
        return None if row["clip_cost"] is None else row["clip_cost"] / 1000
    return row["rank_cost"]


def _spend(row: dict, role: str) -> tuple[int, float]:
    """What using the model costs you, comparable across services: free, then inside a plan (by the share of the
    plan's monthly allowance one use takes), then paid by the dollar."""
    unit = _unit_cost(row, role)
    if row["free"]:
        return 0, 0.0
    if row.get("allowance") and unit is not None:
        return 1, unit / row["allowance"]
    return 2, unit if unit is not None else 1e9


def describe(row: dict, role: str) -> str:
    if row["free"]:
        return row["free"]
    unit = _unit_cost(row, role)
    if unit is None:
        return "Price not published"
    if row.get("allowance") and unit:
        n = int(row["allowance"] / unit)
        return f"In your Go plan: about {n:,} {'clips' if role == 'clip' else 'broadcasts'} a month"
    return f"{_money(row['clip_cost'])} per 1,000 clips" if role == "clip" else f"{_money(unit)} per broadcast"


# ------------------------------------------------------------------ what a service offers

def offer(conn: dict, fresh: bool = False) -> list[dict]:
    """Every chat model the connector's service offers that Surge could use, with prices and fit. Cached 6 hours."""
    def build():
        rows = connectors.model_rows(conn)
        host = (urlparse(conn["base_url"]).hostname or "").lower()
        own_prices = connectors.is_opencode(conn) or host.endswith("openrouter.ai")
        ref = {} if own_prices or connectors._local(host) else _reference()  # a model on this PC has no list price
        out, seen = [], set()
        for row in rows:
            mid = str(row.get("id") or row.get("name") or "").removeprefix("models/")
            if not mid or mid in seen:
                continue
            seen.add(mid)
            info = row if own_prices else {**ref.get(_key(mid), {}), **{k: v for k, v in row.items() if v}}
            price_in, price_out = _prices(info)
            if not _usable(conn, mid, row, price_in):
                continue
            name = bare(mid)
            free = _free_reason(conn, mid, price_in, price_out)
            allowance = _num(((info.get("go") or {}).get("monthly_usage") or {}).get("go")) \
                if isinstance(info.get("go"), dict) else None
            tier = ("fast" if price_in <= FAST_INPUT else "strong") if price_in is not None else \
                ("fast" if FAST_NAME.search(name) else "strong")
            item = {"id": mid, "name": str(info.get("name") or mid), "price_in": price_in, "price_out": price_out,
                    "free": free, "clip_cost": 0.0 if free else _per(CLIP_TOKENS, price_in, price_out),
                    "rank_cost": 0.0 if free else _per(RANK_TOKENS, price_in, price_out),
                    "context": _context(info), "released": _released(info), "tier": tier, "allowance": allowance,
                    "fit": {"clip": _fit(name, "clip"), "rank": _fit(name, "rank")}}
            if item["clip_cost"] is not None and not free:
                item["clip_cost"] *= 1000  # per 1,000 clips
            item["clip_note"], item["rank_note"] = describe(item, "clip"), describe(item, "rank")
            out.append(item)
        return out
    cache_key = ("offer", conn["base_url"], (conn.get("api_key") or "")[-8:])
    return _remember(cache_key, 0 if fresh else 6 * 3600, build)


def _older(rows: list[dict], role: str) -> set[int]:
    """Rows that are an older version within a known family and service (claude-haiku-4-5 next to claude-haiku-5-5)."""
    newest: dict[tuple, float] = {}
    for r in rows:
        k = (r.get("connector"), r["fit"][role])
        newest[k] = max(newest.get(k, 0.0), r["released"])
    return {id(r) for r in rows
            if r["fit"][role] < len(FAMILIES[role]) and r["released"] < newest[(r.get("connector"), r["fit"][role])]}


def _failed(r: dict, role: str) -> bool:
    return (r.get("checked") or {}).get(role) is False


def with_checks(rows: list[dict], conn: dict) -> list[dict]:
    """Mark each model with its recent test result on this key: True passed, False failed, None not tested."""
    done = connectors.checks(conn)
    return [{**r, "checked": {role: (done.get(r["id"]) or {}).get(role) for role in ("clip", "rank")}} for r in rows]


def order(rows: list[dict], role: str) -> list[dict]:
    """Best first: the newest of a family Surge does well with, the right tier, free, newer, cheaper. Models that
    failed Surge's test on this key are left out."""
    want = "fast" if role == "clip" else "strong"
    pool = [r for r in rows if not _failed(r, role) and
            (role == "clip" or (r.get("context") or MIN_RANK_CONTEXT) >= MIN_RANK_CONTEXT)]
    older = _older(pool, role)
    return sorted(pool, key=lambda r: (id(r) in older, r["fit"][role], r["tier"] != want, not r["free"],
                                       -r["released"], _spend(r, role)))


def cheapest(rows: list[dict], role: str) -> dict | None:
    """The cheapest model worth using, among the families Surge does well with (or any free one)."""
    rows = [r for r in rows if not _failed(r, role)]
    older = _older(rows, role)
    pool = [r for r in rows if (r["fit"][role] < len(FAMILIES[role]) and id(r) not in older) or r["free"]]
    pool = [r for r in pool if _spend(r, role)[1] < 1e9]
    return min(pool, key=lambda r: (_spend(r, role), r["fit"][role]), default=None)


def listing(rows: list[dict], conn: dict | None = None) -> dict:
    """What the form shows after Fetch models: recommended ones first, then the rest by price."""
    rows = with_checks(rows, conn) if conn else rows
    best = {role: (order(rows, role) or [None])[0] for role in ("clip", "rank")}
    low = {role: cheapest(rows, role) for role in ("clip", "rank")}
    top = {r["id"] for r in order(rows, "clip")[:6] + order(rows, "rank")[:6]}
    ranked = sorted(rows, key=lambda r: (r["id"] not in top, min(r["fit"].values()), not r["free"],
                                         r["clip_cost"] if r["clip_cost"] is not None else 1e9, -r["released"]))
    return {"models": sorted(r["id"] for r in rows), "details": ranked,
            "best": {k: v["id"] if v else None for k, v in best.items()},
            "cheapest": {k: v["id"] if v else None for k, v in low.items()}}


# ------------------------------------------------------------------ picking by testing

def autopick(conn: dict, clip_tries: int = 4, rank_tries: int = 3) -> dict:
    """Test the best candidates for clips, then for ranking, and keep the first of each that works."""
    rows = offer(conn, fresh=True)
    if not rows:
        raise connectors.ConnectorError(f"{urlparse(conn['base_url']).netloc} listed no chat models Surge can use.")
    tried: list[dict] = []
    clip = None
    for r in order(rows, "clip")[:clip_tries]:
        ok, message = llm.check_clip(conn, r["id"], retry=False)
        tried.append({"model": r["id"], "role": "clip", "ok": ok, "message": message})
        if ok:
            clip = r
            break
    if clip is None:
        return {"ok": False, "tried": tried,
                "message": "None of the models Surge tried answered with clean JSON. First problem: " + tried[0]["message"]
                if tried else "No model to try."}
    rank = None
    bad_json = {t["model"] for t in tried if not t["ok"]}  # a model that just failed the clip test won't rank either
    for r in [r for r in order(rows, "rank") if r["id"] not in bad_json][:rank_tries]:
        if r["id"] == clip["id"]:
            rank = r
            break
        ok, message = llm.check_rank(conn, r["id"], retry=False)
        tried.append({"model": r["id"], "role": "rank", "ok": ok, "message": message})
        if ok:
            rank = r
            break
    rank = rank or clip
    message = f"Picked {clip['id']} for each clip ({clip['clip_note']})"
    message += f" and {rank['id']} for ranking ({rank['rank_note']})." if rank is not clip else \
        ", and for ranking too: no stronger model worked with this key."
    return {"ok": True, "model": clip["id"], "rank_model": rank["id"], "message": message, "tried": tried}


def across(items: list[dict]) -> dict:
    """The best and the cheapest model for clips and for ranking among all saved connectors."""
    entries, errors = [], []
    for c in items:
        if not c.get("api_key") and not connectors._local(urlparse(c["base_url"]).hostname or ""):
            continue
        try:
            rows = offer(c)
        except connectors.ConnectorError as e:
            errors.append({"connector": c["id"], "name": c["name"], "error": str(e)})
            continue
        entries += [{**r, "connector": c["id"], "connector_name": c["name"]} for r in with_checks(rows, c)]
    picks = {role: {"best": (order(entries, role) or [None])[0], "cheapest": cheapest(entries, role)}
             for role in ("clip", "rank")}
    return {"picks": picks, "errors": errors}
