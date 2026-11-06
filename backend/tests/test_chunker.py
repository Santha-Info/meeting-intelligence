from app.ingest.chunker import chunk_turns
from app.schemas import Turn


def turns(word_counts: list[int]) -> list[Turn]:
    return [
        Turn(index=i, start=i * 10.0, speaker=f"S{i % 2}", text=" ".join(["w"] * n)) for i, n in enumerate(word_counts)
    ]


def test_never_splits_a_turn_and_covers_every_turn():
    ts = turns([50, 60, 70, 40, 90, 30])
    chunks = chunk_turns("m", ts, target_words=120, overlap_turns=1)
    rendered = "\n".join(c.text for c in chunks)
    for t in ts:
        assert t.text in rendered  # every turn appears whole somewhere


def test_overlap_repeats_last_turn_of_previous_chunk():
    chunks = chunk_turns("m", turns([60, 60, 60, 60]), target_words=130, overlap_turns=1)
    assert len(chunks) >= 2
    last_line_prev = chunks[0].text.split("\n")[-1]
    assert chunks[1].text.split("\n")[0] == last_line_prev


def test_oversized_turn_becomes_its_own_chunk_without_looping():
    chunks = chunk_turns("m", turns([20, 500, 20]), target_words=100, overlap_turns=1)
    assert any(len(c.text.split()) > 400 for c in chunks)
    assert len(chunks) <= 4


def test_time_ranges_and_ids():
    chunks = chunk_turns("meet", turns([80, 80, 80]), target_words=100, overlap_turns=0)
    assert [c.id for c in chunks] == ["meet:0", "meet:1", "meet:2"]
    assert chunks[0].start == 0 and chunks[0].end == 10
    assert chunks[-1].end >= chunks[-1].start


def test_empty_input():
    assert chunk_turns("m", []) == []
