"""faster-whisper wrapper: a small fast model for live listening, a large accurate model for final captions."""
from __future__ import annotations

import os
import re
import threading
from pathlib import Path

import numpy as np

from . import bus, config

_models: dict[str, object] = {}
_lock = threading.Lock()

HALLUCINATIONS = re.compile(
    r"(thank(s| you) for watching|subtitles? by|amara\.org|please subscribe|like and subscribe|"
    r"transcribed by|www\.|\.com\b)", re.I)


def _device() -> tuple[str, str]:
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() > 0:
            return "cuda", "float16"
    except Exception:
        pass
    return "cpu", "int8"


def model(name: str):
    with _lock:
        if name not in _models:
            from faster_whisper import WhisperModel
            device, compute = _device()
            cpu = os.cpu_count() or 8
            workers = int(config.get("render_workers") or 2)
            bus.note(f"Loading Whisper '{name}' on {device} (first run downloads the model)…")
            _models[name] = WhisperModel(name, device=device, compute_type=compute,
                                         cpu_threads=max(4, cpu // max(2, workers)),
                                         num_workers=max(1, workers), download_root=str(config.MODELS))
            bus.note(f"Whisper '{name}' ready")
        return _models[name]


def _clean_words(segments, offset: float) -> list[dict]:
    words: list[dict] = []
    for seg in segments:
        if getattr(seg, "no_speech_prob", 0) > 0.7 and getattr(seg, "avg_logprob", 0) < -0.9:
            continue
        if HALLUCINATIONS.search(seg.text or ""):
            continue
        for w in seg.words or []:
            text = (w.word or "").strip()
            if not text:
                continue
            words.append({"w": text, "s": round(float(w.start) + offset, 3), "e": round(float(w.end) + offset, 3),
                          "p": round(float(w.probability), 3)})
    # whisper sometimes gives zero-length or overlapping words: make them monotonic
    for i, w in enumerate(words):
        if i and w["s"] < words[i - 1]["e"]:
            w["s"] = words[i - 1]["e"]
        if w["e"] <= w["s"]:
            w["e"] = round(w["s"] + 0.08, 3)
    return words


def transcribe(audio, name: str | None = None, offset: float = 0.0, prompt: str = "",
               live: bool = False) -> list[dict]:
    """audio: file path or float32/int16 16 kHz mono array. Returns words with absolute times (+offset)."""
    name = name or config.get("whisper_live_model" if live else "whisper_final_model")
    m = model(name)
    if isinstance(audio, (str, Path)):
        audio = decode(audio)
    if isinstance(audio, np.ndarray) and audio.dtype == np.int16:
        audio = audio.astype(np.float32) / 32768.0
    lang = config.get("language") or None
    if name.endswith(".en"):
        lang = "en"
    hot = config.get("hotwords") or None
    segments, _info = m.transcribe(
        audio, language=lang, word_timestamps=True, vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 400, "speech_pad_ms": 200},
        beam_size=1 if live else 5, best_of=1 if live else 5, temperature=0.0 if live else (0.0, 0.2, 0.4),
        condition_on_previous_text=False, hotwords=hot, initial_prompt=prompt or None,
        no_speech_threshold=0.6, compression_ratio_threshold=2.4)
    return _clean_words(list(segments), offset)


def decode(path: str | Path) -> np.ndarray:
    """16 kHz mono int16 via ffmpeg (handles .ts/.mp4/anything, unlike PyAV-version-sensitive decoders)."""
    from . import tools
    p = tools.run([tools.ffmpeg(), "-hide_banner", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", "16000",
                   "-f", "s16le", "-"], timeout=600)
    return np.frombuffer(p.stdout, np.int16).copy()


def text_of(words: list[dict]) -> str:
    return " ".join(w["w"] for w in words)


def sentences(words: list[dict], gap: float = 0.7) -> list[tuple[float, float, str]]:
    """Group words into utterances split on punctuation or pauses."""
    out, cur = [], []
    for w in words:
        if cur and (w["s"] - cur[-1]["e"] > gap):
            out.append(cur)
            cur = []
        cur.append(w)
        if re.search(r"[.!?]$", w["w"]):
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return [(c[0]["s"], c[-1]["e"], text_of(c)) for c in out]
