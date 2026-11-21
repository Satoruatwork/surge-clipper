"""Word-synced captions as ASS (rendered by libass inside ffmpeg): pop-in active word, hook label, streamer tag."""
from __future__ import annotations

import re
from pathlib import Path

W, H = 1080, 1920


def ass_color(hex_rgb: str, alpha: int = 0) -> str:
    h = hex_rgb.lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


STYLES: dict[str, dict] = {
    "punch": {"label": "Punch", "font": "Montserrat Black", "size": 114, "upper": True, "fill": "#FFFFFF",
              "hi": "#FFE500", "outline": 7, "shadow": 3, "pop": 118, "words": 3, "chars": 18},
    "neon": {"label": "Neon", "font": "Montserrat Black", "size": 100, "upper": True, "fill": "#FFFFFF",
             "hi": "#3BFF6E", "outline": 6, "shadow": 0, "pop": 112, "words": 3, "chars": 18, "glow": True},
    "comic": {"label": "Comic", "font": "Luckiest Guy", "size": 116, "upper": True, "fill": "#FFFFFF",
              "hi": "#FF5A1F", "outline": 8, "shadow": 4, "pop": 122, "words": 2, "chars": 14},
    "tall": {"label": "Tall", "font": "Anton", "size": 132, "upper": True, "fill": "#FFFFFF", "hi": "#FFE500",
             "outline": 6, "shadow": 2, "pop": 110, "words": 3, "chars": 20},
    "clean": {"label": "Clean", "font": "Montserrat ExtraBold", "size": 76, "upper": False, "fill": "#FFFFFF",
              "hi": "#FFFFFF", "outline": 4, "shadow": 2, "pop": 100, "words": 5, "chars": 28},
}

# average advance of an uppercase glyph, in em: used to shrink a line that would overflow the frame
EM = {"Montserrat Black": 0.74, "Montserrat ExtraBold": 0.66, "Luckiest Guy": 0.62, "Anton": 0.46}
MAX_LINE = 960

# y centre of the caption line and hook label for each layout (inside every platform's safe zone)
POSITIONS = {
    "split": {"caption": 1250, "hook": None, "tag": None},   # hook/tag sit on the seam: computed from cam share
    "full": {"caption": 1290, "hook": 380, "tag": 1420},
    "blur": {"caption": 1430, "hook": 470, "tag": 1310},
    "pip": {"caption": 1300, "hook": 380, "tag": 1430},
}

PROFANE = {"fuck", "fucking", "fucked", "fucker", "motherfucker", "shit", "shitty", "bullshit", "bitch",
           "bitches", "asshole", "cunt", "dick", "pussy", "nigga", "nigger", "faggot", "fag", "retard", "retarded",
           "slut", "whore", "cock", "bastard", "damn", "goddamn"}
_SOFT = {"damn", "goddamn", "bastard"}  # masked in captions only when profanity mode is strict


def is_profane(word: str) -> bool:
    w = re.sub(r"[^a-z]", "", word.lower())
    return w in PROFANE and w not in _SOFT


def mask(word: str) -> str:
    core = re.sub(r"[^A-Za-z]", "", word)
    if len(core) < 3:
        return word
    masked = core[0] + "*" * (len(core) - 2) + core[-1]
    return word.replace(core, masked)


def chunk(words: list[dict], max_words: int, max_chars: int, max_gap: float = 0.55) -> list[list[dict]]:
    chunks, cur = [], []
    for w in words:
        if cur:
            chars = sum(len(x["w"]) + 1 for x in cur) + len(w["w"])
            if (len(cur) >= max_words or chars > max_chars or w["s"] - cur[-1]["e"] > max_gap
                    or re.search(r"[.!?,]$", cur[-1]["w"])):
                chunks.append(cur)
                cur = []
        cur.append(w)
    if cur:
        chunks.append(cur)
    return chunks


def _ts(t: float) -> str:
    t = max(0.0, t)
    h = int(t // 3600)
    m = int(t % 3600 // 60)
    s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _esc(text: str) -> str:
    return text.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def build_ass(words: list[dict], duration: float, layout: str, style_name: str = "punch", hook: str = "",
              hook_seconds: float = 3.0, tag: str = "", seam_y: int | None = None,
              profanity: str = "mask", max_words: int | None = None) -> str:
    st = STYLES.get(style_name, STYLES["punch"])
    pos = dict(POSITIONS.get(layout, POSITIONS["full"]))
    if layout == "split" and seam_y:
        pos["hook"] = seam_y
        pos["tag"] = seam_y - 70
    cap_y = pos["caption"]
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{st['font']},{st['size']},{ass_color(st['fill'])},{ass_color(st['fill'])},&H00000000,&H64000000,0,0,0,0,100,100,1,0,1,{st['outline']},{st['shadow']},5,60,60,0,1
Style: Glow,{st['font']},{st['size']},&HFF000000,&HFF000000,{ass_color(st['hi'], 0x40)},&HFF000000,0,0,0,0,100,100,1,0,1,14,0,5,60,60,0,1
Style: Hook,Montserrat Black,66,&H00111111,&H00111111,&H00FFFFFF,&H00000000,0,0,0,0,100,100,0,0,3,20,0,5,90,90,0,1
Style: Tag,Montserrat ExtraBold,34,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,0,0,0,0,100,100,1,0,1,3,1,5,60,60,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines: list[str] = []
    hi = ass_color(st["hi"])
    pop = st["pop"]
    groups = chunk(words, max_words or st["words"], st["chars"])
    for gi, grp in enumerate(groups):
        nxt = groups[gi + 1][0]["s"] if gi + 1 < len(groups) else duration
        grp_end = min(nxt, grp[-1]["e"] + 0.45, duration)
        shown = []
        for w in grp:
            text = w["w"].upper() if st["upper"] else w["w"]
            if profanity != "off" and is_profane(w["w"]):
                text = mask(text)
            shown.append(_esc(text))
        est = ((sum(len(t) for t in shown) + 0.4 * (len(shown) - 1)) * st["size"] * EM.get(st["font"], 0.7)
               * (pop / 100 if len(shown) == 1 else 1 + (pop - 100) / 100 / len(shown)))
        fit = f"\\fs{int(st['size'] * MAX_LINE / est)}" if est > MAX_LINE else ""
        fill = ass_color(st["fill"])
        for i, w in enumerate(grp):
            s = grp[0]["s"] if i == 0 else w["s"]
            e = grp[i + 1]["s"] if i + 1 < len(grp) else grp_end
            if e - s < 0.04:
                continue
            parts = []
            for j, t in enumerate(shown):
                if j == i:
                    anim = (f"{{\\c{hi}\\fscx{pop}\\fscy{pop}\\t(0,110,\\fscx100\\fscy100)}}" if pop != 100
                            else f"{{\\c{hi}}}")
                    parts.append(f"{anim}{t}{{\\c{fill}\\fscx100\\fscy100}}")
                else:
                    parts.append(t)
            intro = "{\\fscx92\\fscy92\\t(0,90,\\fscx100\\fscy100)}" if i == 0 else ""
            body = " ".join(parts)
            lines.append(f"Dialogue: 2,{_ts(s)},{_ts(e)},Cap,,0,0,0,,{{\\an5\\pos({W // 2},{cap_y}){fit}}}{intro}{body}")
            if st.get("glow"):
                glow = []
                for j, t in enumerate(shown):
                    if j == i:
                        glow.append(f"{{\\3a&H30&\\fscx{pop}\\fscy{pop}\\t(0,110,\\fscx100\\fscy100)}}{t}"
                                    "{\\3a&HFF&\\fscx100\\fscy100}")
                    else:
                        glow.append(t)
                lines.append(f"Dialogue: 1,{_ts(s)},{_ts(e)},Glow,,0,0,0,,{{\\an5\\pos({W // 2},{cap_y})\\blur9{fit}"
                             f"\\1a&HFF&\\3a&HFF&}}{intro}" + " ".join(glow))
    if hook and pos.get("hook"):
        wrapped = _wrap(_esc(hook.upper()), 20)
        lines.append(f"Dialogue: 5,{_ts(0)},{_ts(min(hook_seconds, duration))},Hook,,0,0,0,,"
                     f"{{\\an5\\pos({W // 2},{pos['hook']})\\fscx70\\fscy70\\t(0,140,\\fscx100\\fscy100)"
                     f"\\fad(0,180)}}{wrapped}")
    if tag and pos.get("tag"):
        lines.append(f"Dialogue: 4,{_ts(0)},{_ts(duration)},Tag,,0,0,0,,{{\\an5\\pos({W // 2},{pos['tag']})"
                     f"\\alpha&H30&}}{_esc(tag)}")
    return header + "\n".join(lines) + "\n"


def _wrap(text: str, width: int) -> str:
    out, cur = [], ""
    for word in text.split():
        if cur and len(cur) + 1 + len(word) > width:
            out.append(cur)
            cur = word
        else:
            cur = f"{cur} {word}".strip()
    if cur:
        out.append(cur)
    return "\\N".join(out[:3])


def profane_spans(words: list[dict]) -> list[tuple[float, float]]:
    return [(w["s"], w["e"]) for w in words if is_profane(w["w"])]


def write(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path
