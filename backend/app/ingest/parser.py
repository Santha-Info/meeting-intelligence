"""Transcript parsing.

Supported inputs (the formats real tools export):
  * Plain text, one turn per line:  "[00:01:20] Name: text"  or  "00:01:20 Name: text"  ([mm:ss] also works)
  * WebVTT (.vtt) cues with "<v Name>text" or "Name: text" payloads (Teams / Zoom / Meet exports)
Header lines "Meeting:", "Date:", "Attendees:" (or VTT "NOTE Meeting: ...") become metadata.
Lines without a timestamp are treated as a continuation of the previous turn.
"""

import re
from dataclasses import dataclass, field

from app.schemas import Turn

_TS = r"(?:(\d{1,2}):)?(\d{1,2}):(\d{2})(?:[.,]\d{1,3})?"
_TEXT_LINE = re.compile(rf"^\s*\[?{_TS}\]?\s+(?P<speaker>[^:\n]{{1,60}}?)\s*:\s*(?P<text>.+)$")
_VTT_CUE = re.compile(rf"^\s*{_TS}\s*-->\s*{_TS}")
_VTT_VOICE = re.compile(r"^<v\s+(?P<speaker>[^>]+)>(?P<text>.*?)(?:</v>)?$")
_SPEAKER_TEXT = re.compile(r"^(?P<speaker>[^:\n]{1,60}?)\s*:\s*(?P<text>.+)$")
_META = re.compile(r"^(?:NOTE\s+)?(?P<key>Meeting|Title|Date|Attendees)\s*:\s*(?P<value>.+)$", re.IGNORECASE)


class TranscriptParseError(ValueError):
    pass


@dataclass
class ParsedTranscript:
    title: str | None = None
    date: str | None = None
    attendees: list[str] = field(default_factory=list)
    turns: list[Turn] = field(default_factory=list)

    @property
    def speakers(self) -> list[str]:
        seen: dict[str, None] = {}
        for t in self.turns:
            seen.setdefault(t.speaker, None)
        return list(seen)


def _seconds(h: str | None, m: str, s: str) -> float:
    return int(h or 0) * 3600 + int(m) * 60 + int(s)


def format_ts(seconds: float) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _apply_meta(parsed: ParsedTranscript, key: str, value: str) -> None:
    key = key.lower()
    if key in ("meeting", "title"):
        parsed.title = value.strip()
    elif key == "date":
        parsed.date = value.strip()
    elif key == "attendees":
        # "Priya Raman (Product Manager), Arjun Mehta" -> ["Priya Raman (Product Manager)", "Arjun Mehta"]
        parsed.attendees = [a.strip() for a in value.split(",") if a.strip()]


def parse_transcript(content: str, filename: str = "") -> ParsedTranscript:
    content = content.replace("\r\n", "\n").lstrip("﻿")
    is_vtt = filename.lower().endswith(".vtt") or content.lstrip().startswith("WEBVTT")
    parsed = _parse_vtt(content) if is_vtt else _parse_text(content)
    if not parsed.turns:
        raise TranscriptParseError(
            "No speaker turns found. Expected lines like '[00:01:20] Name: text' or a WebVTT file with speaker labels."
        )
    for i, turn in enumerate(parsed.turns):
        turn.index = i
    return parsed


def _parse_text(content: str) -> ParsedTranscript:
    parsed = ParsedTranscript()
    for raw in content.split("\n"):
        line = raw.strip()
        if not line:
            continue
        if m := _TEXT_LINE.match(line):
            h, mm, ss = m.group(1), m.group(2), m.group(3)
            parsed.turns.append(
                Turn(index=0, start=_seconds(h, mm, ss), speaker=m["speaker"].strip(), text=m["text"].strip())
            )
        elif not parsed.turns and (meta := _META.match(line)):
            _apply_meta(parsed, meta["key"], meta["value"])
        elif parsed.turns:
            parsed.turns[-1].text += " " + line  # wrapped line belongs to the previous speaker
    return parsed


def _parse_vtt(content: str) -> ParsedTranscript:
    parsed = ParsedTranscript()
    cue_start: float | None = None
    for raw in content.split("\n"):
        line = raw.strip()
        if not line or line == "WEBVTT":
            cue_start = None if not line else cue_start
            continue
        if meta := _META.match(line):
            _apply_meta(parsed, meta["key"], meta["value"])
            continue
        if line.startswith("NOTE"):
            continue
        if m := _VTT_CUE.match(line):
            cue_start = _seconds(m.group(1), m.group(2), m.group(3))
            continue
        if cue_start is None:
            continue  # cue identifiers or stray lines
        voice = _VTT_VOICE.match(line) or _SPEAKER_TEXT.match(line)
        if voice:
            speaker, text = voice["speaker"].strip(), re.sub(r"<[^>]+>", "", voice["text"]).strip()
            last = parsed.turns[-1] if parsed.turns else None
            if last and last.speaker == speaker and last.start == cue_start:
                last.text += " " + text
            else:
                parsed.turns.append(Turn(index=0, start=cue_start, speaker=speaker, text=text))
        elif parsed.turns:
            parsed.turns[-1].text += " " + re.sub(r"<[^>]+>", "", line)
    return parsed
