"""Voice as a transport on the Ask conversation (Nexus Next item 4, SS11.1).

Voice is not a separate bot. A dictation is transcribed and sent through the
same office_ask.send() the typed composer uses (same request_id idempotency,
same queue, same session), and the reply is read back from the same store and
spoken. The endpoints:

  POST /api/ask/speak   {text}                  -> {audio_base64, mime, tts}
  POST /api/ask/voice   {audio_base64, format,  -> {transcript, reply_text,
                         request_id?, model?}      reply_id, audio_base64,
                                                     mime, stt, tts}

Providers are the estate assets, behind two small seams so they can be swapped
without touching the transport:

  STT  mlx_audio STT (the Matra voice stack shape: capture -> local STT ->
       session), model OFFICE_WHISPER_MODEL, default whisper-base-mlx.
  TTS  OFFICE_TTS=qwen reuses the podcast pipeline voice
       (nexus/podcast_render.py: Qwen3-TTS + the voice-lab reference clip).
       OFFICE_TTS=say is the macOS built-in voice: zero-download fallback and
       the provider the end-to-end proof below runs on a fresh machine.

Audio is capped like every other courier payload in this repo: shape and size
are checked, the bytes are decoded only by the provider that has to hear them.
"""

from __future__ import annotations

import base64
import os
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import office_ask

MAX_AUDIO_BYTES = 4 * 1024 * 1024
# Abuse cap only. Spoken replies are generated IN FULL (chunked, see
# _chunks): the earlier 4000-char truncation is gone on Aria's ruling
# (2026-10-14) — on-demand audio of an answer means the whole answer.
MAX_SPEAK_CHARS = 50000
SPEAK_CHUNK_CHARS = 800
AUDIO_SUFFIXES = ("wav", "mp3", "m4a", "aac", "ogg", "webm", "aif", "aiff", "flac")
WAIT_S = float(os.environ.get("OFFICE_VOICE_WAIT_S", "180"))

_stt_model = None
_stt_lock = threading.Lock()
_tts_model = None
_tts_lock = threading.Lock()


class VoiceUnavailable(RuntimeError):
    pass


# ── speech to text ──────────────────────────────────────────────────────────

def _load_stt():
    global _stt_model
    with _stt_lock:
        if _stt_model is None:
            try:
                from mlx_audio.stt.utils import load_model
            except ImportError as exc:
                raise VoiceUnavailable(f"mlx_audio STT is not installed: {exc}") from exc
            name = os.environ.get("OFFICE_WHISPER_MODEL", "mlx-community/whisper-base-mlx")
            _stt_model = load_model(name)
            if getattr(_stt_model, "_processor", None) is None:
                # The mlx snapshot ships weights without processor files, and
                # the loader swallows that into _processor=None; whisper then
                # dies inside generate(). Attach the upstream processor.
                try:
                    from transformers import WhisperProcessor
                    _stt_model._processor = WhisperProcessor.from_pretrained("openai/whisper-base")
                except Exception as exc:
                    raise VoiceUnavailable(f"whisper processor unavailable: {exc}") from exc
        return _stt_model


def _as_wav(raw: bytes, suffix: str) -> Path:
    # Normalize every capture to 16 kHz mono wav before the STT hears it.
    # afconvert bridges m4a captures; a 22050 Hz wav fed straight to whisper
    # transcribed its first sentence and then hallucinated a tail (proof run
    # 2026-10-13), so even wav input is resampled, never trusted as-is.
    tmp = Path(tempfile.mkdtemp(prefix="office-voice-"))
    src = tmp / f"in.{suffix}"
    src.write_bytes(raw)
    out = tmp / "in16.wav"
    try:
        subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1",
                        str(src), str(out)], check=True, timeout=60, capture_output=True)
        return out
    except (OSError, subprocess.SubprocessError):
        return src  # let the STT try the original bytes before giving up


def transcribe(raw: bytes, suffix: str = "wav") -> str:
    if not raw:
        raise ValueError("voice needs audio")
    if len(raw) > MAX_AUDIO_BYTES:
        raise ValueError("audio is at most 4 MB (about two minutes)")
    if suffix not in AUDIO_SUFFIXES:
        raise ValueError("audio format must be one of " + ", ".join(AUDIO_SUFFIXES))
    path = _as_wav(raw, suffix)
    model = _load_stt()
    # Decode settings earned on the proof bench (2026-10-13), not defaults:
    # chunk_duration 30 (the model default of 1.0 feeds 1 s slices into
    # previous-text conditioning and hallucinates) and greedy temperature
    # only (the fallback ladder re-decodes into garbage with this mlx_audio
    # build; greedy alone transcribed both test clips exactly).
    result = model.generate(str(path), language="en", chunk_duration=30.0,
                            temperature=0.0)
    text = getattr(result, "text", None)
    if text is None and isinstance(result, dict):
        text = result.get("text")
    text = (text or "").strip()
    if not text:
        raise VoiceUnavailable("the dictation transcribed to nothing")
    return text


# ── text to speech ──────────────────────────────────────────────────────────

def _speak_say(text: str) -> bytes:
    tmp = Path(tempfile.mkdtemp(prefix="office-say-"))
    aiff, wav = tmp / "reply.aiff", tmp / "reply.wav"
    try:
        subprocess.run(["say", "-o", str(aiff), text], check=True, timeout=120,
                       capture_output=True)
        subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@22050", str(aiff), str(wav)],
                       check=True, timeout=60, capture_output=True)
    except (OSError, subprocess.SubprocessError) as exc:
        raise VoiceUnavailable(f"macOS say failed: {exc}") from exc
    return wav.read_bytes()


def _chunks(text: str, limit: int = SPEAK_CHUNK_CHARS) -> list:
    """Pack sentences into <=limit-char chunks (the podcast pipeline's
    passage shape). A single over-long sentence is hard-split. Joining the
    chunks with a space reproduces the input, so nothing is dropped."""
    import re
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    out, cur = [], ""
    for sentence in sentences:
        if len(sentence) > limit:
            if cur:
                out.append(cur)
                cur = ""
            out.extend(sentence[i:i + limit] for i in range(0, len(sentence), limit))
            continue
        if cur and len(cur) + 1 + len(sentence) > limit:
            out.append(cur)
            cur = sentence
        else:
            cur = f"{cur} {sentence}" if cur else sentence
    if cur:
        out.append(cur)
    return out or [text]


def _speak_qwen(text: str) -> bytes:
    """The podcast voice: Qwen3-TTS cloned from the voice-lab reference.
    Long text is generated chunk by chunk and concatenated — in full."""
    global _tts_model
    lab = Path(os.environ.get("OFFICE_VOICE_LAB", "/Volumes/VaultsLive/_meta/podcasts/voice-lab-qwen"))
    # The exact pair podcast_render.render() uses: clip + its transcript,
    # both inside the voice dir the podcast pipeline is handed.
    ref_audio = lab / "qwen-wiry-professor.wav"
    ref_text_path = lab / "wiry-professor-audition.txt"
    if not ref_audio.exists() or not ref_text_path.exists():
        raise VoiceUnavailable(f"podcast voice lab not found at {lab}")
    try:
        import numpy as np
        import soundfile as sf
        from mlx_audio.tts.utils import load_model
    except ImportError as exc:
        raise VoiceUnavailable(f"mlx_audio TTS is not installed: {exc}") from exc
    with _tts_lock:
        if _tts_model is None:
            _tts_model = load_model("mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit")
        model = _tts_model
    ref_text = ref_text_path.read_text().strip()
    chunks, rate = [], None
    for passage in _chunks(text):
        for result in model.generate(text=passage, ref_audio=str(ref_audio),
                                     ref_text=ref_text,
                                     lang_code="English", temperature=0.75,
                                     max_tokens=1800, verbose=False):
            chunks.append(np.asarray(result.audio))
            rate = result.sample_rate
    if not chunks:
        raise VoiceUnavailable("the podcast voice produced no audio")
    import io
    buf = io.BytesIO()
    sf.write(buf, np.concatenate(chunks), rate, format="WAV")
    return buf.getvalue()


def _speakable(text: str) -> str:
    """Ask answers are markdown; spoken raw, the markup is noise ('asterisk
    asterisk', 'hash hash'). A conservative cleanup for the ear only — the
    chat keeps the original text untouched."""
    import re
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)          # links -> text
    text = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", text)             # headings
    text = re.sub(r"(?m)^\s*[-*+]\s+", "", text)                  # bullets
    text = re.sub(r"(\*\*|__|\*|_|`)", "", text)                  # emphasis/code
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", ". ", text)
    text = re.sub(r"\n", " ", text)
    return text.strip()


def synthesize(text: str):
    """(wav bytes, provider name). The text is generated IN FULL: the qwen
    path chunks long answers at sentence boundaries and concatenates (the
    podcast pipeline's long-form shape); nothing is truncated."""
    text = _speakable(text or "")
    if not text:
        raise ValueError("speak needs text")
    if len(text) > MAX_SPEAK_CHARS:
        raise ValueError(f"speak refuses more than {MAX_SPEAK_CHARS} characters")
    provider = os.environ.get("OFFICE_TTS", "say")
    if provider == "qwen":
        try:
            return _speak_qwen(text), "qwen3-tts (podcast voice)"
        except VoiceUnavailable:
            return _speak_say(text), "macOS say (qwen unavailable)"
    return _speak_say(text), "macOS say"


# ── on-demand audio cache ─────────────────────────────────────────────────
# Audio is generated on demand per answer (Aria, 2026-10-14) and cached on
# the Studio's local disk keyed by a hash of the provider + the speakable
# text, so replaying the same answer never regenerates. Eviction: when the
# cache passes OFFICE_VOICE_CACHE_MB (default 256 MB), oldest files by
# mtime are deleted until it fits. Location: OFFICE_VOICE_CACHE, default
# ~/.local/share/nexus-office/voice-cache (local disk, never the vault).

_cache_lock = threading.Lock()


def _cache_dir() -> Path:
    path = Path(os.environ.get(
        "OFFICE_VOICE_CACHE",
        str(Path.home() / ".local" / "share" / "nexus-office" / "voice-cache")))
    path.mkdir(parents=True, exist_ok=True)
    return path


def _cache_key(text: str) -> str:
    import hashlib
    seed = os.environ.get("OFFICE_TTS", "say") + "\n" + text
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def _evict_cache(directory: Path) -> None:
    cap = int(os.environ.get("OFFICE_VOICE_CACHE_MB", "256")) * 1024 * 1024
    files = sorted(directory.glob("*.wav"), key=lambda p: p.stat().st_mtime)
    total = sum(p.stat().st_size for p in files)
    for path in files:
        if total <= cap or len(files) <= 1:
            break
        total -= path.stat().st_size
        path.unlink(missing_ok=True)
        path.with_suffix(".json").unlink(missing_ok=True)
        files.remove(path)


def speak(body: dict) -> dict:
    """POST /api/ask/speak: on-demand audio for one answer, cached."""
    import json
    text = _speakable(str(body.get("text") or ""))
    if not text:
        raise ValueError("speak needs text")
    directory = _cache_dir()
    key = _cache_key(text)
    wav_path, meta_path = directory / f"{key}.wav", directory / f"{key}.json"
    with _cache_lock:
        if wav_path.exists() and meta_path.exists():
            meta = json.loads(meta_path.read_text())
            return {"audio_base64": base64.b64encode(wav_path.read_bytes()).decode(),
                    "mime": "audio/wav", "tts": meta.get("tts", ""),
                    "cached": True, "generate_ms": 0}
    started = time.time()
    audio, provider = synthesize(text)
    generate_ms = int((time.time() - started) * 1000)
    with _cache_lock:
        tmp = wav_path.with_suffix(".tmp")
        tmp.write_bytes(audio)
        os.replace(tmp, wav_path)
        meta_path.write_text(json.dumps({"tts": provider, "chars": len(text)}))
        _evict_cache(directory)
    return {"audio_base64": base64.b64encode(audio).decode(), "mime": "audio/wav",
            "tts": provider, "cached": False, "generate_ms": generate_ms}


# ── the whole flow: dictate -> answer spoken back ───────────────────────────

def _wait_reply(reply_id: int, timeout: float) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = office_ask.read()
        for row in state["messages"]:
            if row["id"] == reply_id and row["status"] not in ("queued", "working", "sending"):
                return row
        time.sleep(1.0)
    raise VoiceUnavailable("the answer did not land in time; it is still in the chat")


def converse(body: dict, wait_s: float | None = None) -> dict:
    """One hands-free turn on the Ask session. The transcript enters through
    office_ask.send with the caller request_id, so a retried dictation can
    never double-post, exactly like a retried typed send."""
    encoded = body.get("audio_base64")
    if not isinstance(encoded, str) or not encoded:
        raise ValueError("voice needs audio_base64")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except ValueError as exc:
        raise ValueError("audio_base64 is not base64") from exc
    text = transcribe(raw, str(body.get("format") or "wav").lower())
    payload = {"request_id": body.get("request_id"), "text": text}
    if body.get("model"):
        payload["model"] = body["model"]
    receipt = office_ask.send(payload)
    reply = _wait_reply(receipt["reply_id"], WAIT_S if wait_s is None else wait_s)
    audio, tts = synthesize(reply["text"] or "")
    return {"transcript": text, "reply_text": reply["text"], "reply_id": reply["id"],
            "reply_status": reply["status"],
            "audio_base64": base64.b64encode(audio).decode(), "mime": "audio/wav",
            "stt": os.environ.get("OFFICE_WHISPER_MODEL", "mlx-community/whisper-base-mlx"),
            "tts": tts}
