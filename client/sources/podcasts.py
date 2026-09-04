"""Locally generated briefing podcasts, with proven playable destinations.

The automation owns ``_meta/podcasts/manifest.json``.  This source is read-only:
it exposes every manifest episode, but only gives a row a ``file://`` URL after
the audio path resolves beneath the podcast directory and names an existing,
regular MP3.  A claimed path is not evidence that a playable file exists.
"""

from __future__ import annotations

import json
import pathlib

from sources import _card

KEY = "podcasts"
TITLE = "Podcasts"
ROOT = pathlib.Path("/Users/slowember/Developer/Vaults/_meta/podcasts")
MANIFEST = ROOT / "manifest.json"

TROUBLE = {
    "missing": ("Podcast manifest is missing", 1),
    "malformed": ("Podcast manifest is unreadable", 1),
}

TYPE_NAMES = {
    "morning-briefing": "Morning briefing",
    "midday-pulse": "Midday pulse",
    "evening-reflection": "Evening reflection",
}


def _audio(path) -> tuple[str, str]:
    """Return (file URI, problem), proving containment and existence first."""
    raw = pathlib.Path(str(path or "")).expanduser()
    if not raw.is_absolute():
        return "", "audio_path is not absolute"
    try:
        root = ROOT.resolve()
        resolved = raw.resolve()
        resolved.relative_to(root)
    except (OSError, RuntimeError, ValueError):
        return "", "audio_path is outside the podcast directory"
    if resolved.suffix.lower() != ".mp3":
        return "", "audio_path is not an MP3"
    if not resolved.is_file():
        return "", "audio file is missing"
    return resolved.as_uri(), ""


def _text(value) -> str:
    return str(value if value is not None else "").strip()


def _nonnegative(value, convert):
    try:
        return max(0, convert(value if value is not None else 0))
    except (TypeError, ValueError):
        return 0


def _episode_id(raw: dict, date: str, kind: str, index: int) -> str:
    declared = _text(raw.get("id"))
    derived = "/".join(filter(None, (date, kind)))
    return declared or derived or f"episode-{index + 1}"


def _episode(raw: dict, index: int) -> dict:
    url, problem = _audio(raw.get("audio_path"))
    kind = _text(raw.get("type"))
    date = _text(raw.get("date"))
    episode_id = _episode_id(raw, date, kind, index)
    title = _text(raw.get("title")) or TYPE_NAMES.get(kind) or episode_id
    return {
        "id": episode_id,
        "date": date,
        "type": kind,
        "title": title,
        "audio_path": _text(raw.get("audio_path")),
        "audio_url": url,
        "audio_problem": problem,
        "script_path": _text(raw.get("script_path")),
        "duration_s": _nonnegative(raw.get("duration_s"), float),
        "word_count": _nonnegative(raw.get("word_count"), int),
        "generated_at": _card.zulu(raw.get("generated_at")),
        "source_email": _text(raw.get("source_email")),
    }


def read() -> dict:
    try:
        raw = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"state": "missing", "detail": str(MANIFEST)}
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return {"state": "malformed", "detail": f"{type(exc).__name__}: {exc}"[:300]}

    if not isinstance(raw, dict) or not isinstance(raw.get("episodes"), list):
        return {"state": "malformed", "detail": "manifest episodes must be a list"}
    if any(not isinstance(item, dict) for item in raw["episodes"]):
        return {"state": "malformed", "detail": "every manifest episode must be an object"}

    episodes = [_episode(item, i) for i, item in enumerate(raw["episodes"])]
    return {
        "state": "ok" if episodes else "empty",
        "version": raw.get("version"),
        "as_of": _card.zulu(raw.get("updated_at")),
        "episodes": episodes,
    }


def _headline(episodes: list, playable: list, broken: list) -> str:
    if not episodes:
        return "No podcast episodes yet"
    if broken:
        return f"{len(playable)} playable, {len(broken)} missing audio"
    return f"{len(playable)} {_card.plural(len(playable), 'episode')} ready to play"


def _facts(playable: list, broken: list) -> list:
    return [
        _card.fact("playable", _card.count(len(playable)), "ok" if playable else "dim"),
        _card.fact("missing audio", _card.count(len(broken)), "bad" if broken else "dim"),
    ]


def _row(episode: dict) -> dict:
    kind = TYPE_NAMES.get(episode.get("type"), episode.get("type") or "Podcast")
    seconds = episode.get("duration_s")
    duration = _card.human(seconds) if seconds else ""
    words = episode.get("word_count")
    detail = episode.get("audio_problem") or (f"{_card.count(words)} words" if words else "")
    return _card.row(
        episode.get("id"), episode.get("title"),
        subtitle=" · ".join(filter(None, (kind, episode.get("date")))),
        detail=detail, badge=duration,
        tone="ok" if episode.get("audio_url") else "bad",
        group=episode.get("date"), url=episode.get("audio_url"),
    )


def card(data: dict) -> dict:
    state = data.get("state")
    if state in TROUBLE:
        return _card.trouble(TITLE, state, data.get("detail"), TROUBLE)

    episodes = data.get("episodes") or []
    playable = [episode for episode in episodes if episode.get("audio_url")]
    broken = [episode for episode in episodes if not episode.get("audio_url")]
    rows = [_row(episode) for episode in episodes]
    return _card.build(TITLE, _headline(episodes, playable, broken), len(broken),
                       data.get("as_of") or "", _facts(playable, broken), rows)
