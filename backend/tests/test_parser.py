import pytest

from app.ingest.parser import TranscriptParseError, format_ts, parse_transcript


def test_bracketed_hms_lines_with_metadata():
    text = (
        "Meeting: Weekly sync\nDate: 2026-10-01\nAttendees: Ann Lee (PM), Bob Roy\n\n"
        "[00:00:05] Ann Lee: Hello team.\n[00:01:10] Bob Roy: Status is green.\n"
    )
    p = parse_transcript(text, "sync.txt")
    assert p.title == "Weekly sync"
    assert p.date == "2026-10-01"
    assert p.attendees == ["Ann Lee (PM)", "Bob Roy"]
    assert [(t.start, t.speaker) for t in p.turns] == [(5, "Ann Lee"), (70, "Bob Roy")]


def test_unbracketed_and_mmss_and_continuation_lines():
    text = "00:00:04 Sofia: First line\n  wrapped onto a second line\n12:30 Kenji: mm:ss works too\n"
    p = parse_transcript(text)
    assert p.turns[0].text == "First line wrapped onto a second line"
    assert p.turns[1].start == 12 * 60 + 30
    assert p.speakers == ["Sofia", "Kenji"]


def test_webvtt_voice_tags_and_notes():
    vtt = (
        "WEBVTT\nNOTE Meeting: Retro\nNOTE Date: 2026-10-16\n\n"
        "00:00:03.000 --> 00:00:18.000\n<v Arjun Mehta>Welcome to the retro.\n\n"
        "1\n00:01:12.500 --> 00:01:41.000\nMei Lin: CI is slow.\n"
    )
    p = parse_transcript(vtt, "retro.vtt")
    assert p.title == "Retro"
    assert [(t.start, t.speaker, t.text) for t in p.turns] == [
        (3, "Arjun Mehta", "Welcome to the retro."),
        (72, "Mei Lin", "CI is slow."),
    ]


def test_turns_are_reindexed_in_order():
    p = parse_transcript("[00:00:01] A: one\n[00:00:02] B: two\n[00:00:03] A: three")
    assert [t.index for t in p.turns] == [0, 1, 2]


def test_rejects_text_without_speaker_turns():
    with pytest.raises(TranscriptParseError):
        parse_transcript("Just some notes from the meeting without timestamps.")


@pytest.mark.parametrize(("seconds", "expected"), [(5, "00:05"), (65, "01:05"), (3725, "01:02:05")])
def test_format_ts(seconds, expected):
    assert format_ts(seconds) == expected
