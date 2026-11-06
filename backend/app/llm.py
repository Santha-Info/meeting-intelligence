"""LLM layer: one interface, two implementations.

ClaudeLLM   - Anthropic SDK. Streaming answers (low effort: short, latency-sensitive) and structured
              extraction via messages.parse() into a Pydantic schema (validated, no JSON-in-prose parsing).
              The system prompt is cached (stable prefix), and server-side refusal fallback is enabled so a
              policy decline on the primary model is retried on Anthropic's recommended fallback model.
OfflineLLM  - deterministic, extractive. Lets the whole app, the tests and CI run with no API key, and
              makes the UI demo-able anywhere. It is clearly labelled in the UI; it is not "AI".
"""

import logging
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Protocol

from app.ingest.chunker import render_turn
from app.ingest.parser import format_ts
from app.prompts import ANSWER_SYSTEM, EXTRACT_SYSTEM, build_answer_user_message, build_extract_user_message
from app.schemas import ActionItem, ChatMessage, Decision, MeetingInsights, Turn

log = logging.getLogger(__name__)
FALLBACK_BETA = "server-side-fallback-2026-07-01"


@dataclass
class AnswerResult:
    """Filled in while streaming; read after the iterator is exhausted."""

    usage: dict = field(default_factory=dict)
    model: str = ""
    stop_reason: str | None = None


class LLM(Protocol):
    mode: str
    model: str

    def stream_answer(
        self, question: str, excerpts: list[dict], history: list[ChatMessage], result: AnswerResult
    ) -> Iterator[str]: ...

    def extract_insights(self, title: str, turns: list[Turn]) -> MeetingInsights: ...


class ClaudeLLM:
    mode = "anthropic"

    def __init__(self, model: str, answer_effort: str, extract_effort: str, max_tokens: int, use_fallback: bool):
        import anthropic

        self._anthropic = anthropic
        self.client = anthropic.Anthropic()  # credentials from ANTHROPIC_API_KEY / ant profile
        self.model = model
        self.answer_effort = answer_effort
        self.extract_effort = extract_effort
        self.max_tokens = max_tokens
        self._fallback = {"betas": [FALLBACK_BETA], "fallbacks": "default"} if use_fallback else {}

    def stream_answer(self, question, excerpts, history, result):
        messages = [{"role": m.role, "content": m.content} for m in history if m.role in ("user", "assistant")]
        messages.append({"role": "user", "content": build_answer_user_message(question, excerpts)})
        with self.client.beta.messages.stream(
            model=self.model,
            max_tokens=self.max_tokens,
            system=[{"type": "text", "text": ANSWER_SYSTEM, "cache_control": {"type": "ephemeral"}}],
            messages=messages,
            output_config={"effort": self.answer_effort},
            **self._fallback,
        ) as stream:
            yield from stream.text_stream
            final = stream.get_final_message()
        result.model = final.model
        result.stop_reason = final.stop_reason
        result.usage = {
            "input_tokens": final.usage.input_tokens,
            "output_tokens": final.usage.output_tokens,
            "cache_read_input_tokens": getattr(final.usage, "cache_read_input_tokens", 0) or 0,
        }
        if final.stop_reason == "refusal":
            log.warning("answer refused", extra={"stop_details": str(getattr(final, "stop_details", None))})

    def extract_insights(self, title, turns):
        transcript = "\n".join(render_turn(t) for t in turns)
        response = self.client.beta.messages.parse(
            model=self.model,
            max_tokens=16000,
            system=EXTRACT_SYSTEM,
            messages=[{"role": "user", "content": build_extract_user_message(title, transcript)}],
            output_config={"effort": self.extract_effort},
            output_format=MeetingInsights,
            **self._fallback,
        )
        if response.stop_reason == "refusal" or response.parsed_output is None:
            raise RuntimeError(f"Insight extraction did not complete (stop_reason={response.stop_reason})")
        return response.parsed_output


class OfflineLLM:
    """Heuristic stand-in: picks the best-matching sentences from the excerpts and cites them."""

    mode = "offline"
    model = "offline-extractive"

    _ACTION = re.compile(r"\b(I will|I'll|I can|I am going to)\b", re.IGNORECASE)
    _DECISION = re.compile(r"\b(decision|decided|agreed|we will commit)\b", re.IGNORECASE)
    _DUE = re.compile(
        r"\b(?:by|before|until)\s+((?:tomorrow,?\s*)?(?:(?:January|February|March|April|May|June|July|August|"
        r"September|October|November|December)\s+\d{1,2}(?:st|nd|rd|th)?|end of [a-z ]+|this week|next sprint))",
        re.IGNORECASE,
    )

    def stream_answer(self, question, excerpts, history, result):
        q_terms = {t for t in re.findall(r"[a-z0-9]+", question.lower()) if len(t) > 3}
        scored: list[tuple[int, int, str, str]] = []
        for e in excerpts:
            for line in e["text"].split("\n"):
                overlap = len(q_terms & set(re.findall(r"[a-z0-9]+", line.lower())))
                if overlap:
                    scored.append((overlap, -e["n"], line, f"[{e['n']}]"))
        scored.sort(reverse=True)
        seen: set[str] = set()  # overlapping chunks repeat a turn; show each line once
        scored = [s for s in scored if not (s[2] in seen or seen.add(s[2]))]
        if not scored:
            text = "I couldn't find that in the selected meetings."
        else:
            lines = [f"- {line} {cite}" for _, _, line, cite in scored[:4]]
            text = (
                "**Offline demo mode** - extractive answer (set ANTHROPIC_API_KEY for generated answers):\n\n"
                + "\n".join(lines)
            )
        result.model = self.model
        result.stop_reason = "end_turn"
        yield from re.split(r"(\s+)", text)

    def extract_insights(self, title, turns):
        decisions = [
            Decision(decision=t.text, made_by=t.speaker, timestamp=format_ts(t.start))
            for t in turns
            if self._DECISION.search(t.text)
        ]
        actions = []
        for t in turns:
            if self._ACTION.search(t.text) and (due := self._DUE.search(t.text)):
                actions.append(ActionItem(task=t.text, owner=t.speaker, due=due.group(1), timestamp=format_ts(t.start)))
        opening = " ".join(t.text for t in turns[:2])
        summary = (
            f"{title}: {opening[:400]}"
            f"{'...' if len(opening) > 400 else ''} (Offline mode: heuristic summary - first turns of the meeting.)"
        )
        return MeetingInsights(summary=summary, decisions=decisions, action_items=actions, open_questions=[])


def build_llm(provider: str, **kwargs) -> LLM:
    import os

    if provider == "offline" or (provider == "auto" and not os.environ.get("ANTHROPIC_API_KEY")):
        log.info("LLM provider: offline (no ANTHROPIC_API_KEY)")
        return OfflineLLM()
    return ClaudeLLM(**kwargs)
