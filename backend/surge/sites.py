"""Campaign sites: the 10 boards and agencies Surge works with (plus up to 2 of your own, 12 in total), which of
your accounts are linked on each, and opening a site in a Surge window where you sign in yourself. Sessions are
kept between runs. Surge never types your passwords or clicks around these sites for you."""
from __future__ import annotations

import webbrowser

from . import db

MAX_SITES = 12

SEED = [
    {"key": "vyro", "name": "Vyro", "url": "https://vyro.com",
     "notes": "MrBeast-backed. $1.00–$2.50 per 1K on the public board; most campaigns pay nothing under 5,000 views a post. Paid 7–10 days after a campaign ends."},
    {"key": "clipster", "name": "Clipster", "url": "https://www.clipster.com",
     "notes": "Gaming and streamer campaigns, $1 per 1K most common. 5% withdrawal fee, $50 minimum. English captions required."},
    {"key": "clippingnet", "name": "Clipping.net", "url": "https://clipping.net",
     "notes": "Streamer and celebrity campaigns. View minimums are high (often 100K a post); bounties pay up to $6 per 1K."},
    {"key": "kick", "name": "Kick Bounties", "url": "https://bounties.kick.com",
     "notes": "Kick pays clippers directly, about $40–$50 per 100K views. Kick streamers only."},
    {"key": "findclout", "name": "FindClout", "url": "https://findclout.com",
     "notes": "Curated: about 19 of 20 applicants are declined. No fee to clippers."},
    {"key": "vues", "name": "Vues", "url": "https://vues.app",
     "notes": "Rates only shown in the app. About 10% fee at withdrawal."},
    {"key": "reach", "name": "Reach Clipping", "url": "https://reachclipping.com",
     "notes": "Agency behind the Lacy, Clix and YourRAGE GTA VI pools. Each brief has its own rules page."},
    {"key": "tryshoot", "name": "Try Shoot", "url": "https://tryshoot.com",
     "notes": "Small board, $0.30–$2 per 1K. 7% withdrawal fee plus transfer cost."},
    {"key": "elev8or", "name": "Elev8or Bounties", "url": "https://www.elev8or.io/bounty/clipping",
     "notes": "Small board, $0.25–$2 per 1K. Check the fee on each bounty."},
    {"key": "discord", "name": "Streamer clip programs", "url": "",
     "notes": "Put a streamer's Discord invite here. The least crowded way in: clip them for 2–4 weeks, then apply with your links."},
]

_gui = False


def set_gui(on: bool) -> None:
    global _gui
    _gui = on


def seed() -> None:
    have = {r["key"] for r in db.all("SELECT key FROM sites")}
    for s in SEED:
        if s["key"] not in have:
            db.insert("sites", {**s, "enabled": 0, "accounts": [], "custom": 0})


def open_url(url: str, title: str) -> str:
    """A Surge window (shares one saved browser profile, so you stay signed in), or the default browser."""
    if _gui:
        import webview
        webview.create_window(title, url, width=1240, height=880)
        return "window"
    webbrowser.open(url)
    return "browser"
