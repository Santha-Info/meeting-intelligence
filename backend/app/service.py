"""Application service: ingestion and question answering. FastAPI routes stay thin and call into this."""

import hashlib
import logging
import re
import time
from collections.abc import Iterator
from pathlib import Path

from app import guardrails
from app.config import Settings
from app.embeddings import Embedder
from app.ingest.chunker import chunk_turns
from app.ingest.parser import parse_transcript
from app.llm import LLM, AnswerResult
from app.observability import metrics, trace_id_var
from app.retrieval import Retriever
from app.schemas import ChatRequest, Citation, MeetingInfo
from app.store import Store

log = logging.getLogger(__name__)

_FOLLOW_UP = re.compile(r"\b(it|that|this|they|them|those|he|she|his|her|their|what about|and)\b", re.IGNORECASE)


def meeting_id_for(filename: str, content: str) -> str:
    stem = re.sub(r"[^a-z0-9]+", "-", Path(filename).stem.lower()).strip("-")[:48] or "meeting"
    return f"{stem}-{hashlib.sha256(content.encode()).hexdigest()[:8]}"


class MeetingService:
    def __init__(self, settings: Settings, store: Store, embedder: Embedder, llm: LLM):
        self.settings = settings
        self.store = store
        self.embedder = embedder
        self.llm = llm
        self.retriever = Retriever(store, embedder)

    # ---- ingestion ----------------------------------------------------------------------
    def ingest(self, filename: str, content: str) -> MeetingInfo:
        t0 = time.perf_counter()
        parsed = parse_transcript(content, filename)
        meeting_id = meeting_id_for(filename, content)
        title = parsed.title or Path(filename).stem.replace("_", " ").replace("-", " ")
        attendees = parsed.attendees or parsed.speakers
        chunks = chunk_turns(
            meeting_id, parsed.turns, self.settings.chunk_target_words, self.settings.chunk_overlap_turns
        )
        embeddings = self.embedder.embed_documents([c.text for c in chunks])
        meeting = MeetingInfo(
            id=meeting_id,
            title=title,
            date=parsed.date,
            attendees=attendees,
            duration_seconds=parsed.turns[-1].start,
            turn_count=len(parsed.turns),
        )
        # Save first so the meeting is searchable even if insight extraction fails or is slow.
        self.store.save_meeting(meeting, filename, parsed.turns, chunks, embeddings)
        meeting.insights = self.refresh_insights(meeting_id, title, parsed.turns)
        metrics.incr("meetings_ingested")
        metrics.observe("ingest", (time.perf_counter() - t0) * 1000)
        log.info("ingested", extra={"meeting_id": meeting_id, "turns": len(parsed.turns), "chunks": len(chunks)})
        return meeting

    def refresh_insights(self, meeting_id: str, title: str, turns=None):
        turns = turns or self.store.get_turns(meeting_id)
        try:
            insights = self.llm.extract_insights(title, turns)
        except Exception:
            log.exception("insight extraction failed", extra={"meeting_id": meeting_id})
            metrics.incr("insight_extraction_failed")
            return None
        self.store.save_insights(meeting_id, insights)
        return insights

    def ingest_samples(self, samples_dir: Path) -> list[MeetingInfo]:
        files = sorted(p for p in samples_dir.glob("*") if p.suffix in guardrails.ALLOWED_EXTENSIONS)
        return [self.ingest(p.name, p.read_text(encoding="utf-8")) for p in files]

    # ---- question answering --------------------------------------------------------------
    def retrieval_query(self, req: ChatRequest) -> str:
        """Cheap follow-up handling: a short or pronoun-y question is searched together with the previous
        user question ("and who owns that?"). Avoids an extra LLM round-trip for query rewriting."""
        q = req.question
        previous = next((m.content for m in reversed(req.history) if m.role == "user"), None)
        if previous and (len(q.split()) < 8 or _FOLLOW_UP.search(q)):
            return f"{previous} {q}"
        return q

    def answer(self, req: ChatRequest) -> Iterator[tuple[str, dict]]:
        """Yields SSE events: retrieval -> delta* -> done (or error)."""
        t0 = time.perf_counter()
        metrics.incr("questions")
        question = guardrails.clean_question(req.question, self.settings.max_question_chars)
        if guardrails.looks_like_injection(question):
            metrics.incr("injection_suspected")
            log.warning("possible prompt injection in question")

        query = self.retrieval_query(req)
        hits = self.retriever.search(query, req.meeting_ids, self.settings.top_k)
        titles = {m.id: m for m in self.store.list_meetings()}
        citations = [
            Citation(
                chunk_id=h.chunk.id,
                meeting_id=h.chunk.meeting_id,
                meeting_title=titles[h.chunk.meeting_id].title if h.chunk.meeting_id in titles else h.chunk.meeting_id,
                start=h.chunk.start,
                end=h.chunk.end,
                speakers=h.chunk.speakers,
                snippet=h.chunk.text[:280],
                score=round(h.cosine, 4),
            )
            for h in hits
        ]
        yield (
            "retrieval",
            {
                "citations": [
                    c.model_dump() | {"keyword_rank": h.keyword_rank, "dense_rank": h.dense_rank}
                    for c, h in zip(citations, hits, strict=True)
                ]
            },
        )

        best = max((h.cosine for h in hits), default=0.0)
        refused = best < self.settings.min_relevance
        result = AnswerResult(model=self.llm.model)
        if refused:
            # Relevance gate: don't send weak context to the LLM - it invites confident hallucination.
            metrics.incr("answers_refused_low_relevance")
            raw = guardrails.NOT_FOUND
            yield "delta", {"text": raw}
        else:
            excerpts = [
                {
                    "n": i + 1,
                    "meeting": c.meeting_title,
                    "date": titles[c.meeting_id].date if c.meeting_id in titles else None,
                    "start": c.start,
                    "end": c.end,
                    "speakers": c.speakers,
                    "text": h.chunk.text,
                }
                for i, (c, h) in enumerate(zip(citations, hits, strict=True))
            ]
            history = req.history[-self.settings.history_turns :]
            parts: list[str] = []
            for text in self.llm.stream_answer(question, excerpts, history, result):
                parts.append(text)
                yield "delta", {"text": text}
            raw = "".join(parts)

        answer, cited, ungrounded = guardrails.validate_citations(raw, len(citations))
        if ungrounded:
            metrics.incr("answers_ungrounded")
        latency = (time.perf_counter() - t0) * 1000
        metrics.observe("answer", latency)
        log.info(
            "answered",
            extra={
                "retrieval_query": query,
                "best_cosine": round(best, 3),
                "hits": len(hits),
                "cited": cited,
                "ungrounded": ungrounded,
                "refused": refused,
                "latency_ms": round(latency),
                "usage": result.usage,
            },
        )
        yield (
            "done",
            {
                "answer": answer,
                "cited": cited,
                "ungrounded": ungrounded,
                "refused": refused,
                "retrieval_query": query,
                "best_score": round(best, 4),
                "latency_ms": round(latency),
                "model": result.model,
                "usage": result.usage,
                "trace_id": trace_id_var.get(),
            },
        )
