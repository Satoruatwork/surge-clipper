"""The AI writer writes the hook, title, description, hashtags and edit plan, and judges/ranks moments, through the
active connector (Anthropic or any OpenAI-compatible service, see connectors.py). Works without a connector too
(heuristic writer), so the pipeline never blocks on AI."""
from __future__ import annotations

import json
import random
import re
import time

from . import bus, config, connectors, hype

SYSTEM = """You are the head editor of a top short-form clipping channel. You turn live-stream moments into
YouTube Shorts / Instagram Reels / TikToks that hold viewers past the first 2 seconds.
You know the GTA 6 clipping format: 9:16, facecam over gameplay, word captions, a 3-7 word hook on screen,
30-50 seconds, setup -> payoff -> streamer reaction. Hooks tease the payoff without spoiling it
("He thought the cops were gone", "Worst getaway driver in Leonida"). Titles say plainly what happens, no fake
claims, no clickbait the clip can't back up. Never pick isolated cutscenes, in-game TV shows, story spoilers,
cheats/exploits, or private info. Always reply with ONE JSON object and nothing else."""

CLIP_PROMPT = """Streamer: {streamer} ({platform}) · Game: {game}
Clip window: {duration:.0f}s. Chat reaction type: {chat_class}. Signal strength: {strength}.
Top chat messages around the peak (message, count): {chat}
{viewer}
Transcript (seconds from clip start):
{transcript}

Return JSON:
{{"score": 1-10 how well this works as a short (be strict; 8+ only for clearly great moments),
  "keep": true/false,
  "kind": "chase|fail|funny|reaction|skill|clutch|rage|wholesome|cutscene|in-game-tv|other",
  "spoiler": true/false,
  "trim_start": seconds to cut from the start (0 if the setup starts right away),
  "trim_end": seconds to cut from the end (0 if it ends on the reaction),
  "hook": "3-7 word on-screen hook, no emojis, no quotes",
  "title": "Shorts title under 80 chars, may end with one emoji",
  "description": "2 short sentences, natural, mentions the streamer",
  "hashtags": ["#gta6", ...8-12 tags mixing broad (#gaming #shorts) and specific (#{streamer_tag})],
  "variants": [{{"hook": "...", "title": "...", "description": "..."}}, ... 4 more distinct angles for other accounts],
  "fx": {{"cues": [{{"t": seconds from clip start, "kind": "sound|zoom|shake|flash|sticker",
                     "tags": [sound tags, for kind sound] or "name": exact sound or sticker name,
                     "why": "few words"}}],
          "music": null or {{"mood": one of the music moods}}}}}}

Editing rules for "fx" (the meme edit layer):
- The peak is at about {peak:.1f}s. The biggest hit (sound + zoom) lands exactly on the payoff.
- {budget} cues at most. Fewer is better than noisy. Effects must match the moment: funny -> comic sound + laughing
  sticker; fail -> scratch/sad sound + skull or clown; shock -> build-up before, impact + flash on the reveal;
  hype -> horn/drop + shake + fire.
- Meme sounds go on a punchline or in a pause, never over important words. A sticker reacts ~0.3s after the beat.
- If the setup drags, one small zoom in a pause keeps people watching.
- Music only when it adds something (chases, fails, goofy moments); otherwise null.
- Sound tags you can use: {sound_tags}
- Sound names you can use: {sound_names}
- Stickers: {stickers}
- Music moods: {music_moods}"""

RANK_PROMPT = """These are {n} candidate moments from one {game} stream by {streamer}. Pick the {k} that make
the best standalone shorts, best first. Prefer: clear setup and payoff, loud streamer reaction, chat exploding,
moments understandable without context. Reject menus, loading screens, dead air, cutscenes, spoilers.

{items}

Return JSON: {{"picks": [{{"id": "c03", "score": 1-10, "reason": "short"}}, ...]}}"""


_THINK = re.compile(r"<think>.*?</think>", re.S | re.I)


def parse_json(text: str) -> dict:
    """The JSON object in a model's reply. Tolerates what models add around it: thinking blocks, code fences, a
    sentence before or after, trailing commas."""
    text = _THINK.sub("", text or "").strip()
    start = text.find("{")
    if start < 0:
        raise ValueError("no JSON object in the reply")
    depth, in_str, esc = 0, False, False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    out = json.loads(re.sub(r",\s*([}\]])", r"\1", text[start:i + 1]))
                except ValueError as e:
                    raise ValueError(f"invalid JSON ({e})") from e
                if not isinstance(out, dict):
                    raise ValueError("the reply is not a JSON object")
                return out
    raise ValueError("the JSON in the reply is cut off")


def enabled() -> bool:
    """Is an AI connector set up? Without one Surge writes with its built-in writer."""
    return connectors.active() is not None


def _ask(prompt: str, rank: bool = False, max_tokens: int = 1500) -> dict | None:
    conn = connectors.active()
    if conn is None:
        return None
    model = (conn["rank_model"] if rank else conn["model"]) or conn["model"]
    try:
        return parse_json(connectors.chat(conn, prompt, SYSTEM, model, max_tokens))
    except Exception as e:
        bus.note(f"AI writer unavailable, using built-in writer ({str(e)[:160]})", "warn")
        return None


TEST_CLIP = """Streamer: Testy (Twitch) · Game: GTA 6
Clip window: 32s. Chat reaction type: funny. Signal strength: 5.2.
Top chat messages around the peak (message, count): [["KEKW", 14], ["NO WAY", 9]]

Transcript (seconds from clip start):
[  0.0] okay okay I think the cops lost me.
[  4.5] wait no they did not lose me! oh no oh no.
[  9.0] I drove straight into the water, I can't believe this.

Return JSON: {"score": 1-10, "hook": "3-7 word on-screen hook, no emojis", "title": "Shorts title under 80 chars",
"hashtags": ["#gta6", "#shorts", "#funny"]}"""

TEST_RANK = """These are 3 candidate moments from one GTA 6 stream by Testy. Pick the 2 that make the best standalone shorts,
best first.

id c01 · signal 4.1 · chat: funny
[ 0.0] I drove straight into the water.

id c02 · signal 2.0 · chat: -
[ 0.0] okay chat let's check the map.

id c03 · signal 5.5 · chat: shock
[ 0.0] NO WAY he just jumped the whole bridge!

Return JSON: {"picks": [{"id": "c03", "score": 1-10, "reason": "short"}]}"""


def test(conn: dict) -> dict:
    """Run a real (small) clip prompt through a connector and check the reply is what Surge needs. Never raises."""
    model, rank_model = conn["model"], conn.get("rank_model") or conn["model"]
    t0 = time.time()
    try:
        raw = connectors.chat(conn, TEST_CLIP, SYSTEM, model, 600)
    except connectors.ConnectorError as e:
        return {"ok": False, "message": str(e)}
    except Exception as e:
        return {"ok": False, "message": f"Test failed: {str(e)[:200]}"}
    secs = time.time() - t0
    try:
        out = parse_json(raw)
    except ValueError as e:
        return {"ok": False, "message": f"{model} answered in {secs:.1f} s but not with clean JSON ({e}). Surge would use "
                                        "its built-in writer for every clip. Try a larger model."}
    if not (out.get("hook") and out.get("title")):
        return {"ok": False, "message": f"{model} answered with JSON but without the hook and title Surge asks for. "
                                        "Try a larger model."}
    message = f"{model} works: it answered in {secs:.1f} s with a hook ({str(out['hook'])[:40]}) and a title."
    if rank_model != model:
        t1 = time.time()
        try:
            picks = parse_json(connectors.chat(conn, TEST_RANK, SYSTEM, rank_model, 400)).get("picks")
        except (connectors.ConnectorError, ValueError) as e:
            return {"ok": False, "message": f"{message} The ranking model {rank_model} did not work: {e}"}
        if not isinstance(picks, list) or not picks:
            return {"ok": False, "message": f"{message} The ranking model {rank_model} answered without any picks. "
                                            "Try a stronger model for ranking."}
        message += f" Ranking model {rank_model} works too ({time.time() - t1:.1f} s)."
    return {"ok": True, "message": message}


def test_active() -> dict:
    conn = connectors.active()
    if conn is None:
        return {"ok": False, "message": "No AI connector yet. Add one under Settings, AI writer."}
    return test(conn)


def transcript_lines(words: list[dict], every: float = 2.5) -> str:
    lines, cur, t0 = [], [], None
    for w in words:
        if t0 is None:
            t0 = w["s"]
        cur.append(w["w"])
        if w["e"] - t0 >= every or re.search(r"[.!?]$", w["w"]):
            lines.append(f"[{t0:5.1f}] {' '.join(cur)}")
            cur, t0 = [], None
    if cur:
        lines.append(f"[{t0:5.1f}] {' '.join(cur)}")
    return "\n".join(lines) or "(no speech)"


def tag_of(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower()) or "streamer"


def write_clip(ctx: dict) -> dict:
    """ctx: streamer, platform, game, duration, chat_class, chat(list), words(rel), strength"""
    prompt = CLIP_PROMPT.format(
        streamer=ctx.get("streamer") or "the streamer", platform=ctx.get("platform") or "Twitch",
        game=ctx.get("game") or "GTA 6", duration=ctx.get("duration", 30),
        chat_class=ctx.get("chat_class") or "unknown", strength=ctx.get("strength", "?"),
        chat=json.dumps(ctx.get("chat", [])[:8], ensure_ascii=False),
        viewer=(f"Viewers already clipped this moment and titled it {json.dumps(ctx['viewer_titles'][:4], ensure_ascii=False)}"
                f": it's proven. Use their titles to understand what happens, then write a better hook of your own.\n"
                if ctx.get("viewer_titles") else ""),
        transcript=transcript_lines(ctx.get("words", [])), streamer_tag=tag_of(ctx.get("streamer")),
        peak=float(ctx.get("peak_rel", 0)), budget=ctx.get("fx_budget", 5),
        sound_tags=", ".join(ctx.get("fx_vocab", {}).get("sound_tags", [])),
        sound_names=", ".join(ctx.get("fx_vocab", {}).get("sound_names", [])),
        stickers=", ".join(ctx.get("fx_vocab", {}).get("stickers", [])),
        music_moods=", ".join(ctx.get("fx_vocab", {}).get("music_moods", [])))
    out = _ask(prompt, max_tokens=2500)
    if out and out.get("hook") and out.get("title"):
        out["hashtags"] = clean_tags(out.get("hashtags", []), ctx)
        out["source"] = "ai"
        return out
    return heuristic(ctx)


def rank(cands: list[dict], k: int, streamer: str, game: str) -> list[dict] | None:
    items = "\n\n".join(
        f"id {c['id']} · at {int(c['peak'] // 60)}:{int(c['peak'] % 60):02d} · signal {c['score']:.1f} · "
        f"chat: {c.get('chat_class') or '-'} {json.dumps(c.get('chat', [])[:5], ensure_ascii=False)}\n"
        f"{transcript_lines(c.get('words', []), 4)[:900]}" for c in cands)
    out = _ask(RANK_PROMPT.format(n=len(cands), k=k, game=game or "GTA 6", streamer=streamer, items=items),
               rank=True, max_tokens=3000)
    if not out or not isinstance(out.get("picks"), list):
        return None
    return out["picks"]


# ---------------------------------------------------------------- heuristic writer (no API key)

HOOKS = {
    "funny": ["Chat could not stop laughing", "This is why I watch him", "He did not mean to do that",
              "The timing on this", "I can't believe this happened"],
    "shock": ["Nobody saw this coming", "Wait for it", "He was NOT ready", "What just happened",
              "This changed everything"],
    "hype": ["This was actually insane", "Cleanest play of the stream", "Chat went crazy for this",
             "He really pulled this off"],
    "fail": ["This went so wrong", "He was so close", "Worst timing ever", "It was going so well"],
    "clip": ["Chat begged for this clip", "Everyone clipped this", "This had to be clipped"],
    None: ["Wait for the ending", "He did not expect this", "Watch till the end", "This got out of hand"],
}
BASE_TAGS = ["#gta6", "#gtavi", "#gta", "#gaming", "#shorts", "#streamer", "#twitch", "#funny", "#viral",
             "#gtaonline", "#leonida", "#vicecity"]


def clean_tags(tags: list, ctx: dict) -> list[str]:
    out = []
    for t in list(tags) + [f"#{tag_of(ctx.get('streamer'))}"]:
        t = "#" + re.sub(r"[^\w]", "", str(t).lstrip("#"))
        if len(t) > 2 and t.lower() not in {x.lower() for x in out}:
            out.append(t)
    for t in [x.strip() for x in (config.get("campaign_hashtags") or "").replace(",", " ").split() if x.strip()]:
        t = t if t.startswith("#") else "#" + t
        if t.lower() not in {x.lower() for x in out}:
            out.insert(0, t)
    return out[:15]


def heuristic(ctx: dict) -> dict:
    words = ctx.get("words", [])
    streamer = ctx.get("streamer") or "streamer"
    cls = ctx.get("chat_class")
    best, best_w = "", 0.0
    for s, e, text in _sents(words):
        w = hype.voice_weight(text) + 0.1 * len(text.split())
        if w > best_w and 3 <= len(text.split()) <= 14:
            best, best_w = text, w
    rnd = random.Random(hash((streamer, ctx.get("peak", 0))))
    hook = rnd.choice(HOOKS.get(cls, HOOKS[None]))
    quote = best.strip().rstrip(".!?,")
    # an already-viral moment comes with the titles viewers gave it: a descriptive one beats a random quote
    viewer = next((t for t in ctx.get("viewer_titles") or [] if 3 <= len(re.findall(r"[A-Za-z']+", t)) <= 14), "")
    viewer = re.sub(r"\s+", " ", viewer).strip()
    if viewer:
        title = f"{viewer[0].upper()}{viewer[1:]} | {streamer} GTA 6"[:95]
    else:
        title = (f"\"{quote[:60]}\" 😭 | {streamer}" if quote else f"{hook} | {streamer} GTA 6")[:95]
    tags = clean_tags(BASE_TAGS[:9] + [f"#{tag_of(streamer)}"], ctx)
    desc = f"{streamer} {'had chat in tears' if cls == 'funny' else 'had chat going crazy'} on stream. " \
           f"Full stream on {ctx.get('platform') or 'Twitch'}."
    variants = []
    for h in rnd.sample(HOOKS.get(cls, HOOKS[None]), k=min(4, len(HOOKS.get(cls, HOOKS[None])))):
        variants.append({"hook": h, "title": f"{h} 😳 | {streamer}"[:95], "description": desc})
    strength = float(ctx.get("strength") or 0)
    return {"score": round(min(9.0, 4.5 + strength * 0.8), 1), "keep": True, "kind": cls or "reaction",
            "spoiler": False, "trim_start": 0, "trim_end": 0, "hook": hook, "title": title, "description": desc,
            "hashtags": tags, "variants": variants, "source": "builtin"}


def _sents(words):
    from .transcribe import sentences
    return sentences(words)
