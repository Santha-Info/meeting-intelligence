"""Optional bonus: audio -> timestamped transcript with faster-whisper (CTranslate2, CPU int8).

Limitation (stated, not hidden): Whisper does not identify speakers, so every turn is labelled "Speaker".
Real speaker diarization (e.g. pyannote) is listed under "next steps" in the README. Segments are written in
the same "[hh:mm:ss] Speaker: text" format the parser already understands, so audio reuses the whole pipeline.
"""

from functools import lru_cache
from pathlib import Path

from app.ingest.parser import format_ts


class TranscriptionUnavailable(RuntimeError):
    pass


@lru_cache(maxsize=1)
def _model(name: str):
    try:
        from faster_whisper import WhisperModel
    except ImportError as e:  # optional dependency
        raise TranscriptionUnavailable(
            "Voice transcription is optional. Install it with: pip install -r requirements-voice.txt"
        ) from e
    return WhisperModel(name, device="cpu", compute_type="int8")


def transcribe_to_text(audio_path: Path, title: str, model_name: str = "small") -> str:
    segments, info = _model(model_name).transcribe(str(audio_path), vad_filter=True)
    lines = [f"Meeting: {title}", f"Attendees: Speaker (auto-transcribed, language={info.language})", ""]
    lines += [f"[{format_ts(s.start)}] Speaker: {s.text.strip()}" for s in segments if s.text.strip()]
    return "\n".join(lines)
