"""Speaker-turn-aware chunking.

Why not fixed-size character chunks: a meeting's unit of meaning is the exchange ("A proposes, B objects,
A decides"). Splitting mid-turn loses who said what; splitting every turn loses the context of the
question being answered. So a chunk is a window of whole consecutive turns up to ~target_words, and the
last `overlap_turns` turns are repeated at the start of the next window so an exchange that straddles a
boundary is retrievable from either side. Every chunk keeps its time range and speakers for citations.
"""

from app.ingest.parser import format_ts
from app.schemas import Chunk, Turn


def render_turn(turn: Turn) -> str:
    return f"[{format_ts(turn.start)}] {turn.speaker}: {turn.text}"


def chunk_turns(meeting_id: str, turns: list[Turn], target_words: int = 180, overlap_turns: int = 1) -> list[Chunk]:
    if not turns:
        return []
    chunks: list[Chunk] = []
    window: list[Turn] = []
    fresh = 0  # turns in the window that are not overlap carried from the previous chunk
    words = 0
    i = 0
    while i < len(turns):
        turn = turns[i]
        turn_words = len(turn.text.split())
        if window and words + turn_words > target_words:
            if fresh:
                chunks.append(_make_chunk(meeting_id, len(chunks), window, turns, i))
                window = window[-overlap_turns:] if overlap_turns else []
            else:
                window = []  # only overlap left and the next turn doesn't fit: drop the overlap
            fresh = 0
            words = sum(len(t.text.split()) for t in window)
            continue
        window.append(turn)
        fresh += 1
        words += turn_words
        i += 1
    if window and fresh:
        chunks.append(_make_chunk(meeting_id, len(chunks), window, turns, len(turns)))
    return chunks


def _make_chunk(meeting_id: str, n: int, window: list[Turn], all_turns: list[Turn], next_index: int) -> Chunk:
    # A chunk ends where the next turn starts (or at its own last turn for the final chunk).
    end = all_turns[next_index].start if next_index < len(all_turns) else window[-1].start
    speakers = list(dict.fromkeys(t.speaker for t in window))
    return Chunk(
        id=f"{meeting_id}:{n}",
        meeting_id=meeting_id,
        start=window[0].start,
        end=max(end, window[-1].start),
        speakers=speakers,
        text="\n".join(render_turn(t) for t in window),
    )
