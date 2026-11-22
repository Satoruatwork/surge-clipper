"""Core logic checks: signals, captions, boundaries, slotting, URL parsing, secret sealing."""
from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from surge import captions, chat, distribute, hype, pipeline, vault  # noqa: E402


def test_chat_classes():
    assert hype.message_weight("KEKW KEKW")[1] == "funny"
    assert hype.message_weight("CLIP IT")[1] == "clip"
    assert hype.message_weight("no way bro what")[1] == "shock"
    plain, cls = hype.message_weight("what are we doing today")
    assert cls == "shock" and plain < 3  # mild
    assert hype.message_weight("hello there")[0] == 1.0


def test_fuse_ignores_negative_evidence():
    quiet_chat = hype.fuse(-3.0, -3.0, 6.0, 0.0, True)
    assert quiet_chat > 1.5  # a scream still counts when chat is quiet
    assert hype.fuse(0.0, 0.0, 0.0, 0.0, True) == 0.0


def test_robust_z_flags_spike():
    base = np.ones(200, np.float32) * 10 + np.random.default_rng(1).normal(0, 1, 200).astype(np.float32)
    assert hype.robust_z(30.0, base) > 5
    assert abs(hype.robust_z(10.0, base)) < 1.5


def test_pick_peaks_respects_gap():
    s = np.zeros(1000, np.float32)
    s[[100, 110, 400, 800]] = [5, 6, 4, 3]
    assert hype.pick_peaks(s, 10, min_gap=30) == [110, 400, 800]


def _words(text: str, start: float = 0.0, step: float = 0.4):
    return [{"w": w, "s": round(start + i * step, 3), "e": round(start + i * step + 0.3, 3), "p": 0.9}
            for i, w in enumerate(text.split())]


def test_captions_mask_and_structure():
    ass = captions.build_ass(_words("bro what the fuck was that"), 4.0, "split", "punch", hook="He was not ready",
                             seam_y=692, profanity="mask")
    assert "F**K" in ass and "fuck" not in ass.lower().replace("f**k", "")
    assert ass.count("Dialogue: 2,") >= 6  # one event per word
    assert "HE WAS NOT READY" in ass


def test_caption_overflow_shrinks_font():
    ass = captions.build_ass([{"w": "UNBELIEVABLEEEEEEEE", "s": 0, "e": 1}], 2.0, "full", "punch")
    assert "\\fs" in ass


def test_refine_snaps_and_keeps_peak():
    words = _words("okay chat watch this", 2.0) + _words("no way no way he actually did it", 14.0) + \
        _words("that was insane", 24.0)
    start, end = pipeline.refine(words, peak=20.0, pre=16, post=6, lo=10, hi=40, total=40)
    assert start <= 20 - 4 and end >= 23
    assert any(abs(start - (w["s"] - 0.25)) < 0.3 or abs(start - (w["s"] - 0.15)) < 0.3 for w in words)
    assert 10 <= end - start <= 40


def test_next_slot_windows_cap_and_gap(monkeypatch):
    taken: list[float] = []
    monkeypatch.setattr(distribute, "_day_posts", lambda _aid, _d0: list(taken))
    monkeypatch.setattr(distribute.config, "get", lambda k: {"min_gap_minutes": 60, "slot_jitter_minutes": 0}.get(k))
    acc = {"id": 1, "windows": ["10:00", "18:00"], "daily_cap": 2}
    morning = datetime.now().replace(hour=6, minute=0, second=0, microsecond=0) + timedelta(days=1)
    t1 = distribute.next_slot(acc, morning.timestamp())
    assert datetime.fromtimestamp(t1).hour == 10
    taken.append(t1)
    t2 = distribute.next_slot(acc, morning.timestamp())
    assert datetime.fromtimestamp(t2).hour == 18
    taken.append(t2)
    t3 = distribute.next_slot(acc, morning.timestamp())  # cap reached: next day
    assert datetime.fromtimestamp(t3).date() > datetime.fromtimestamp(t1).date()


def test_a_hole_in_a_recording_stays_real_time(tmp_path):
    """A live capture with a dropout: audio, picture and hole detection must all keep the hole, so the voice and
    captions after it stay in step with the picture (they used to slide 4 to 10 seconds early)."""
    from surge import tools, transcribe
    parts = []
    for i, offset in enumerate((0, 5)):  # 2 s, then a 3 s hole, then 2 s more
        part = tmp_path / f"p{i}.ts"
        tools.run([tools.ffmpeg(), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=s=160x90:r=30:d=2",
                   "-f", "lavfi", "-i", "sine=f=440:d=2", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
                   "-output_ts_offset", str(offset), "-f", "mpegts", str(part)], timeout=60)
        parts.append(part.read_bytes())
    raw = tmp_path / "raw.ts"
    raw.write_bytes(b"".join(parts))
    holes = tools.holes(raw)
    assert len(holes) == 1 and abs(holes[0][0] - 2) < 0.2 and abs(holes[0][1] - 5) < 0.2
    seconds = len(transcribe.decode(raw)) / 16000
    assert abs(seconds - 7) < 0.25  # 2 + 3 of silence + 2, not 4


def test_clips_keep_off_stream_holes():
    off = pipeline.off_holes
    assert off([(40.0, 44.0)], 20, 50, 30, 30, 120) == pytest.approx((9.9, 39.9))  # end cut, start grows back
    assert off([(18.0, 22.0)], 15, 45, 30, 30, 120) == pytest.approx((22.1, 52.1))  # start moved, end grows
    assert off([(28.0, 31.0)], 15, 45, 30, 30, 120) is None                        # the moment fell in the hole
    assert off([(3.0, 9.0), (36.0, 40.0)], 10, 45, 30, 30, 120) == pytest.approx((9.1, 35.9))  # boxed in
    assert off([(5.0, 9.0), (30.5, 40.0)], 10, 45, 28, 30, 120, grow=False) is None  # too short, may not grow
    assert off([], 10, 40, 25, 30, 120) == (10, 40)


def test_spread_fills_the_day_evenly(monkeypatch):
    taken: list[float] = []
    monkeypatch.setattr(distribute, "_day_posts", lambda _aid, _d0: list(taken))
    monkeypatch.setattr(distribute.config, "get", lambda k: {"min_gap_minutes": 75, "slot_jitter_minutes": 8}.get(k))
    acc = {"id": 7, "windows": ["00:00-00:00"], "daily_cap": 30}
    day = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    for _ in range(31):
        taken.append(distribute.next_slot(acc, day.timestamp()))
    first = sorted(t for t in taken if t < (day + timedelta(days=1)).timestamp())
    assert len(first) == 30  # the cap sets the pace, not the 75 min gap meant for set times
    gaps = [(b - a) / 60 for a, b in zip(first, first[1:])]
    assert 32 <= min(gaps) and max(gaps) <= 64  # one about every 48 min, each shifted up to 8 min
    assert datetime.fromtimestamp(taken[-1]).date() == (day + timedelta(days=1)).date()


def test_night_span_is_one_posting_day(monkeypatch):
    taken: list[float] = []
    monkeypatch.setattr(distribute, "_day_posts", lambda _aid, _d0: list(taken))
    monkeypatch.setattr(distribute.config, "get", lambda k: {"min_gap_minutes": 60, "slot_jitter_minutes": 0}.get(k))
    acc = {"id": 3, "windows": ["20:00-04:00"], "daily_cap": 4}
    noon = datetime.now().replace(hour=12, minute=0, second=0, microsecond=0) + timedelta(days=1)
    for _ in range(5):
        taken.append(distribute.next_slot(acc, noon.timestamp()))
    at = [datetime.fromtimestamp(t) for t in taken]
    assert [d.strftime("%H:%M") for d in at] == ["21:00", "23:00", "01:00", "03:00", "21:00"]
    assert at[4].date() == at[0].date() + timedelta(days=1)  # 4 a night: the fifth waits for the next night
    start, end = distribute.day_bounds(acc, taken[2])  # 01:00 counts toward the night it started
    assert start <= taken[0] < end and start <= taken[3] < end


def test_parse_source():
    assert chat.parse_source("https://www.twitch.tv/timthetatman") == \
        {"platform": "twitch", "channel": "timthetatman", "video": None, "kind_hint": "live"}
    assert chat.parse_source("twitch.tv/videos/2933741083")["kind_hint"] == "vod"
    assert chat.parse_source("https://kick.com/xqc")["channel"] == "xqc"
    yt = chat.parse_source("https://www.youtube.com/watch?v=abc123&t=5")
    assert yt["platform"] == "youtube" and yt["video"] == "abc123"
    assert chat.parse_source("https://example.com/x")["platform"] == "other"


def test_vault_roundtrip():
    sealed = vault.seal("sk-secret-123")
    assert sealed.startswith("dpapi:") and "secret" not in sealed
    assert vault.unseal(sealed) == "sk-secret-123"


def test_compose_variants_differ(monkeypatch):
    monkeypatch.setattr(distribute.config, "get", lambda k: "")
    clip = {"id": 7, "title": "Main title", "hook": "h", "description": "d", "streamer": "Tim", "platform": "twitch",
            "hashtags": ["#gta6", "#gtavi", "#gaming", "#shorts", "#a", "#b", "#c", "#d", "#e"],
            "variants": [{"title": "Angle two", "description": "d2"}]}
    a = distribute.compose(clip, {"platform": "youtube"}, 0)
    b = distribute.compose(clip, {"platform": "instagram"}, 1)
    assert a["title"] == "Main title" and b["title"] == "Angle two"
    assert "twitch.tv/Tim" in a["description"] and "#shorts" in " ".join(a["hashtags"]).lower()


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))


def test_one_clip_per_moment():
    s = "test-session"
    pipeline.claim(s, 1, 100.0, 127.0)
    assert pipeline.owner_of(s, 110.0) == 1
    assert pipeline.owner_of(s, 140.0) is None
    # a later spike whose window would start inside clip 1 gets trimmed to start after it
    got = pipeline.settle(s, 2, 118.0, 150.0, peak=140.0, min_len=10)
    assert got == (127.05, 150.0)
    # a spike inside clip 1 is a duplicate
    assert pipeline.settle(s, 3, 105.0, 130.0, peak=120.0, min_len=10) == 1
    # an earlier clip can't run into a later one either
    got = pipeline.settle(s, 4, 60.0, 105.0, peak=90.0, min_len=10)
    assert got == (60.0, 99.95)
    pipeline.release(s, 1)
    assert pipeline.owner_of(s, 110.0) is None


def test_game_crop_avoids_facecam():
    from surge import render
    chain = render.video_chain("split", 1920, 1080, [8, 684, 600, 392], focus=0.5)
    crop = chain.split("[g]crop=")[1].split(",")[0]
    cw, ch, cx, cy = (int(v) for v in crop.split(":"))
    assert cx >= 8 + 600  # the game band starts right of the webcam


def test_split_band_takes_the_shape_of_the_facecam_box():
    from surge import render
    box = [2, 434, 386, 216]
    assert render.cam_share(box) == pytest.approx(1080 * 216 / 386 / 1920)  # about 31%: the whole box shows
    assert render.cam_share([0, 0, 800, 100]) == 0.22 and render.cam_share([0, 0, 100, 800]) == 0.55
    assert render.cam_share(None) == render.CAM_SHARE
    chain = render.video_chain("split", 1920, 1080, box, 0.5, render.cam_share(box))
    assert "crop=386:216:2:434" in chain and f"scale=1080:{render.seam_y(render.cam_share(box))}" in chain


def test_meme_sound_autotags():
    from surge import library
    assert "impact" in library.auto_sound_tags("Vine Boom Sound Effect.mp3")[0]
    tags, cue = library.auto_sound_tags("bruh.wav")
    assert "fail" in tags and cue == "hit"
    assert library.auto_sound_tags("whoosh_fast.mp3")[1] == "lead"
    assert library.auto_song_meta("INTERWORLD - METAMORPHOSIS (slowed).mp3")["moods"] == ["hype", "phonk"]
    assert "chase" in library.auto_song_meta("Running in the 90s.mp3")["moods"]


def test_fx_plan_hits_the_peak_and_respects_level():
    from surge import fx
    words = _words("okay okay watch this one", 1.0) + _words("no way bro", 12.0)
    plan = fx.heuristic(words, 20.0, 12.5, "shock", "no way bro", level="medium", salt=1)
    kinds_at_peak = {c["kind"] for c in plan["cues"] if abs(c["t"] - 12.5) < 0.6}
    assert {"sound", "zoom"} <= kinds_at_peak
    assert len(plan["cues"]) <= fx.LEVEL_CUES["medium"] + 1
    assert fx.heuristic(words, 20.0, 12.5, "shock", level="off")["cues"] == []


def test_fx_clean_drops_unknown_and_out_of_range():
    from surge import fx
    plan = {"cues": [{"t": 3, "kind": "sticker", "name": "skull"}, {"t": 3, "kind": "sticker", "name": "nope"},
                     {"t": 99, "kind": "zoom"}, {"t": 2, "kind": "laser"}, {"t": 4, "kind": "sound", "tags": ["impact"]}]}
    out = fx.clean(plan, 20.0, "max")
    assert [(c["kind"], c.get("name")) for c in out["cues"]] == [("sticker", "skull"), ("sound", None)]


def test_warmup_ramps_posting(monkeypatch):
    from surge import growth
    now = time.time()
    acc = {"daily_cap": 5, "warmup": 1, "warmup_started": now - 1 * 86400}
    assert growth.effective_cap(acc) == 1
    acc["warmup_started"] = now - 15 * 86400
    assert growth.effective_cap(acc) == 3
    acc["warmup"] = 0
    assert growth.effective_cap(acc) == 5


def test_campaign_matching_and_tags(monkeypatch):
    from surge import campaigns
    camp = {"id": 9, "name": "Kai GTA", "accounts": [3], "platforms": ["youtube"], "streamers": ["KaiCenat"],
            "hashtags": ["kaiclips"], "mentions": ["@kaicenat"], "min_seconds": 10, "max_seconds": 60, "status": "active"}
    monkeypatch.setattr(campaigns, "active", lambda: [camp])
    clip = {"id": 1, "streamer": "kaicenat", "title": "t", "description": "d", "hashtags": ["#gta6"], "platform": "twitch"}
    assert campaigns.for_post(clip, {"id": 3, "platform": "youtube"}) is camp
    assert campaigns.for_post(clip, {"id": 4, "platform": "youtube"}) is None
    assert campaigns.for_post(clip, {"id": 3, "platform": "instagram"}) is None
    assert campaigns.for_post({**clip, "streamer": "xqc"}, {"id": 3, "platform": "youtube"}) is None
    assert campaigns.fits_length(camp, 30) and not campaigns.fits_length(camp, 75)
    monkeypatch.setattr(distribute.config, "get", lambda k: "")
    copy = distribute.compose(clip, {"platform": "youtube"}, 0, camp)
    assert copy["hashtags"][0] == "#kaiclips" and "@kaicenat" in copy["description"]


def test_onset_and_decay_find_the_real_moment():
    from surge import moment
    sr = 16000
    t = np.arange(sr * 20) / sr
    rng = np.random.default_rng(3)
    x = 0.01 * rng.normal(size=len(t))
    burst = (t >= 12.3) & (t < 15.0)
    x[burst] += 0.5 * np.sin(2 * np.pi * 300 * t[burst])
    env = moment.envelope((x * 32767).astype(np.int16))
    assert abs(moment.onset(env, 13.5) - 12.3) < 0.15   # coarse estimate 1.2 s late, snapped back
    assert abs(moment.onset(env, 11.0) - 12.3) < 0.15   # coarse estimate early, snapped forward
    assert 15.0 <= moment.decay(env, 12.3) <= 16.6
    quiet = moment.envelope((0.01 * rng.normal(size=sr * 10) * 32767).astype(np.int16))
    assert moment.onset(quiet, 5.0) == 5.0               # nothing to snap to: keep the estimate


def test_chat_delay_is_measured():
    from surge import moment
    rng = np.random.default_rng(5)
    audio = rng.normal(0, 0.3, 900)
    events = rng.choice(np.arange(20, 870), 25, replace=False)
    audio[events] += 6
    chat = rng.normal(0, 0.3, 900)
    chat[events + 6] += 5
    lag, r = moment.best_chat_lag(chat, audio, 15)
    assert lag == 6 and r > 0.3


def test_refine_fills_30_to_50_on_word_boundaries():
    # 90 s of talk: a 6-word sentence every 4 s, so every cut has to pick a boundary
    words = []
    for k in range(22):
        words += _words("we go right here then left.", 2.0 + k * 4.0, 0.45)
    for peak, settle in ((40.0, 44.0), (20.0, 22.5), (70.0, 80.0), (12.0, None)):
        start, end = pipeline.refine(words, peak, pre=22, post=14, lo=30, hi=50, total=90, settle=settle)
        assert 30 <= end - start <= 50.6, (peak, start, end)
        assert start <= peak - 4 and end >= peak + 2.5
        assert not any(w["s"] < start < w["e"] for w in words), ("start mid-word", start)
        assert not any(w["s"] < end < w["e"] for w in words), ("end mid-word", end)


def test_campaign_routing_is_exclusive(monkeypatch):
    from surge import campaigns
    kai = {"id": 1, "name": "Kai", "streamers": ["KaiCenat"], "accounts": [10, 11], "dedicated": 1, "status": "active"}
    lacy = {"id": 2, "name": "Lacy", "streamers": ["lacy"], "accounts": [12], "dedicated": 0, "status": "active"}
    monkeypatch.setattr(campaigns, "active", lambda: [kai, lacy])
    assert [c["id"] for c in campaigns.matching({"streamer": "kaicenat"})] == [1]
    assert campaigns.matching({"streamer": "xqc"}) == []
    assert campaigns.dedicated_ids() == {10, 11}   # Lacy's account isn't reserved, Kai's two are


def test_campaign_code_lands_in_caption(monkeypatch):
    monkeypatch.setattr(distribute.config, "get", lambda k: "")
    camp = {"id": 3, "name": "x", "code": "VYRO-7731", "hashtags": [], "mentions": []}
    clip = {"id": 4, "title": "t", "description": "d", "streamer": "kaicenat", "platform": "twitch", "hashtags": ["#gta6"]}
    assert "VYRO-7731" in distribute.compose(clip, {"platform": "instagram"}, 0, camp)["description"]


def test_whisper_loops_collapse_but_real_repeats_stay():
    from surge.transcribe import collapse_loops
    loop = [{"w": "oh" if i % 2 == 0 else "shit,", "s": 40 + i * 0.08, "e": 40 + i * 0.08 + 0.05} for i in range(42)]
    assert [w["w"] for w in collapse_loops(loop)] == ["oh", "shit,"]          # 21x in 3.3 s: one copy
    real = [{"w": x, "s": i * 0.5, "e": i * 0.5 + 0.3} for i, x in enumerate("no no no no no no hold my hand".split())]
    assert len(collapse_loops(real)) == 9                                     # spoken at a human pace: kept


def test_viral_moments_group_clips_of_one_moment():
    from surge import viral
    clips = [
        {"title": "KAI WTF", "views": 321, "duration": 12, "offset": 14747, "video": "v2", "game": "Grand Theft Auto VI"},
        {"title": "niko", "views": 74, "duration": 38, "offset": 14764, "video": "v2", "game": "Grand Theft Auto VI"},
        {"title": "other broadcast", "views": 500, "duration": 30, "offset": 14750, "video": "v1", "game": "Grand Theft Auto VI"},
        {"title": "chatting", "views": 900, "duration": 30, "offset": 100, "video": "v1", "game": "Just Chatting"},
        {"title": "deleted vod", "views": 999, "duration": 30, "offset": None, "video": None, "game": "Grand Theft Auto VI"},
    ]
    ms = viral.moments(clips, "Grand Theft Auto VI")
    assert [(m["video"], m["views"], len(m["clips"])) for m in ms] == [("v1", 500, 1), ("v2", 395, 2)]
    assert ms[1]["a"] == 14747                       # the most-viewed clip sets the window


def test_viral_peak_is_the_loud_part_inside_the_clip():
    from surge import viral
    m = {"a": 100.0, "b": 130.0, "clips": [{"views": 300, "offset": 100.0, "duration": 30.0}]}
    loud = np.full(65, -40.0, np.float32)            # audio from t0=80 s to 145 s
    loud[42:45] = -8.0                               # Kai yells at 122-125 s
    loud[5] = 0.0                                    # a louder bang at 85 s, outside the clip
    assert 121 <= viral.peak_in(m, loud, 80.0) <= 126


def test_viral_gate_drops_muted_and_silent_moments():
    viral_clip = {"signals": {"viral": {"views": 10}}}
    talk = [{"w": "word", "s": 1 + i * 0.5, "e": 1.3 + i * 0.5} for i in range(40)]
    env = np.full(2000, -20.0, np.float32)           # 40 s of audio at 20 ms
    assert pipeline.viral_gate(viral_clip, env, talk, 0, 30) is None
    muted = env.copy()
    muted[500:900] = -100.0                          # 8 s of Twitch-muted silence
    assert "muted" in pipeline.viral_gate(viral_clip, muted, talk, 0, 30)
    assert "nothing" in pipeline.viral_gate(viral_clip, env, talk[:5], 0, 30)
    assert pipeline.viral_gate({"signals": {}}, muted, [], 0, 30) is None   # live/VOD clips aren't gated


def test_profanity_mask_catches_every_form():
    for w in ("niggas", "fuckin'", "Motherfuckers,", "dipshit", "bitches", "shitting", "Fuck!"):
        assert captions.is_profane(w), w
    for w in ("damn", "shiitake", "Dickens", "cocktail", "class", "snigger"[:0] or "night"):
        assert not captions.is_profane(w), w


def test_cam_box_set_on_one_row_carries_to_the_streamers_other_rows(monkeypatch):
    rows = [{"id": 3, "channel": "KaiCenat", "settings": {"cam": [20, 427, 354, 227], "frame": [1920, 1080]}}]
    monkeypatch.setattr(pipeline.db, "all", lambda sql, args=(): [r for r in rows if r["id"] != args[0]
                                                                 and r["channel"].lower() == args[1]])
    viral_row = {"id": 4, "channel": "KaiCenat", "settings": {}}
    assert pipeline.saved_cam(viral_row, (1920, 1080)) == [20, 427, 354, 227]
    assert pipeline.saved_cam(viral_row, (1280, 720)) is None          # another frame size: the box doesn't fit
    assert pipeline.saved_cam({"id": 5, "channel": "xqc", "settings": {}}, (1920, 1080)) is None


def test_known_cam_box_is_checked_against_the_overlay_border():
    from surge import facecam
    rng = np.random.default_rng(1)
    frames = []
    for _ in range(4):
        f = np.full((540, 960, 3), int(rng.integers(0, 120)), np.uint8)      # the game: changes every frame
        x, y = int(rng.integers(300, 800)), int(rng.integers(50, 450))
        f[y:y + 60, x:x + 90] = 255                                           # something moving in it
        f[217:327, 10:187] = 200                                              # the webcam overlay: stays put
        frames.append(f)
    edge = facecam.persistent_edges(frames, 960)
    right, moved = [10, 217, 177, 110], [10, 176, 177, 110]                   # same size, 41 px higher
    assert facecam.box_fit(edge, right) > 0.8
    assert facecam.box_fit(edge, moved) < 0.6
