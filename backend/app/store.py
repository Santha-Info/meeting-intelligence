"""Persistence: SQLite for metadata, turns, chunks, an FTS5 index (keyword search) and embeddings (BLOBs).

One file, zero services - right for a single-instance demo and for tests. The production path
(Postgres + pgvector, or a managed vector store) is described in the README; the rest of the code
only talks to this class, so swapping it is contained.
"""

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

import numpy as np

from app.schemas import Chunk, MeetingInfo, MeetingInsights, Turn

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meetings (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    date TEXT,
    attendees TEXT NOT NULL,
    filename TEXT NOT NULL,
    duration_seconds REAL NOT NULL,
    turn_count INTEGER NOT NULL,
    insights TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS turns (
    meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    idx INTEGER NOT NULL,
    start REAL NOT NULL,
    speaker TEXT NOT NULL,
    text TEXT NOT NULL,
    PRIMARY KEY (meeting_id, idx)
);
CREATE TABLE IF NOT EXISTS chunks (
    id TEXT PRIMARY KEY,
    meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    start REAL NOT NULL,
    end_ REAL NOT NULL,
    speakers TEXT NOT NULL,
    text TEXT NOT NULL,
    embedding BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS chunks_meeting ON chunks(meeting_id);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    text, chunk_id UNINDEXED, meeting_id UNINDEXED, tokenize='porter'
);
"""


class Store:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._path = str(db_path)
        self._lock = threading.Lock()  # SQLite allows one writer; serialize writes from worker threads
        with self._conn() as c:
            c.executescript(_SCHEMA)

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ---- writes -------------------------------------------------------------------------
    def save_meeting(
        self,
        meeting: MeetingInfo,
        filename: str,
        turns: list[Turn],
        chunks: list[Chunk],
        embeddings: np.ndarray,
    ) -> None:
        with self._lock, self._conn() as c:
            self._delete(c, meeting.id)  # re-uploading the same file replaces it (idempotent ingest)
            c.execute(
                "INSERT INTO meetings (id, title, date, attendees, filename, duration_seconds, turn_count, insights)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    meeting.id,
                    meeting.title,
                    meeting.date,
                    json.dumps(meeting.attendees),
                    filename,
                    meeting.duration_seconds,
                    meeting.turn_count,
                    meeting.insights.model_dump_json() if meeting.insights else None,
                ),
            )
            c.executemany(
                "INSERT INTO turns (meeting_id, idx, start, speaker, text) VALUES (?, ?, ?, ?, ?)",
                [(meeting.id, t.index, t.start, t.speaker, t.text) for t in turns],
            )
            for chunk, vec in zip(chunks, embeddings, strict=True):
                c.execute(
                    "INSERT INTO chunks (id, meeting_id, start, end_, speakers, text, embedding)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        chunk.id,
                        chunk.meeting_id,
                        chunk.start,
                        chunk.end,
                        json.dumps(chunk.speakers),
                        chunk.text,
                        vec.astype(np.float32).tobytes(),
                    ),
                )
                c.execute(
                    "INSERT INTO chunks_fts (text, chunk_id, meeting_id) VALUES (?, ?, ?)",
                    (chunk.text, chunk.id, chunk.meeting_id),
                )

    def save_insights(self, meeting_id: str, insights: MeetingInsights) -> None:
        with self._lock, self._conn() as c:
            c.execute("UPDATE meetings SET insights = ? WHERE id = ?", (insights.model_dump_json(), meeting_id))

    def delete_meeting(self, meeting_id: str) -> bool:
        with self._lock, self._conn() as c:
            return self._delete(c, meeting_id)

    @staticmethod
    def _delete(c: sqlite3.Connection, meeting_id: str) -> bool:
        c.execute("DELETE FROM chunks_fts WHERE meeting_id = ?", (meeting_id,))
        return c.execute("DELETE FROM meetings WHERE id = ?", (meeting_id,)).rowcount > 0

    # ---- reads --------------------------------------------------------------------------
    def list_meetings(self) -> list[MeetingInfo]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM meetings ORDER BY COALESCE(date, created_at) DESC").fetchall()
        return [self._meeting(r) for r in rows]

    def get_meeting(self, meeting_id: str) -> MeetingInfo | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM meetings WHERE id = ?", (meeting_id,)).fetchone()
        return self._meeting(row) if row else None

    def get_turns(self, meeting_id: str) -> list[Turn]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT idx, start, speaker, text FROM turns WHERE meeting_id = ? ORDER BY idx", (meeting_id,)
            ).fetchall()
        return [Turn(index=r["idx"], start=r["start"], speaker=r["speaker"], text=r["text"]) for r in rows]

    def load_vectors(self, meeting_ids: list[str] | None) -> tuple[list[str], np.ndarray]:
        """All chunk embeddings for the scope. Brute-force cosine is fine up to ~100k chunks."""
        sql, args = "SELECT id, embedding FROM chunks", ()
        if meeting_ids:
            sql += f" WHERE meeting_id IN ({','.join('?' * len(meeting_ids))})"
            args = tuple(meeting_ids)
        with self._conn() as c:
            rows = c.execute(sql, args).fetchall()
        if not rows:
            return [], np.zeros((0, 0), dtype=np.float32)
        return [r["id"] for r in rows], np.stack([np.frombuffer(r["embedding"], dtype=np.float32) for r in rows])

    def keyword_search(self, query: str, meeting_ids: list[str] | None, limit: int) -> list[tuple[str, float]]:
        """BM25 over FTS5. Returns (chunk_id, bm25) - lower bm25 is better in SQLite."""
        terms = [t for t in "".join(ch if ch.isalnum() else " " for ch in query.lower()).split() if len(t) > 1]
        if not terms:
            return []
        match = " OR ".join(f'"{t}"' for t in terms)  # quoted terms: user text can't inject FTS syntax
        sql = "SELECT chunk_id, bm25(chunks_fts) AS score FROM chunks_fts WHERE chunks_fts MATCH ?"
        args: list = [match]
        if meeting_ids:
            sql += f" AND meeting_id IN ({','.join('?' * len(meeting_ids))})"
            args += meeting_ids
        sql += " ORDER BY score LIMIT ?"
        args.append(limit)
        with self._conn() as c:
            return [(r["chunk_id"], r["score"]) for r in c.execute(sql, args).fetchall()]

    def get_chunks(self, chunk_ids: list[str]) -> dict[str, Chunk]:
        if not chunk_ids:
            return {}
        with self._conn() as c:
            rows = c.execute(
                "SELECT id, meeting_id, start, end_, speakers, text FROM chunks"
                f" WHERE id IN ({','.join('?' * len(chunk_ids))})",
                chunk_ids,
            ).fetchall()
        return {
            r["id"]: Chunk(
                id=r["id"],
                meeting_id=r["meeting_id"],
                start=r["start"],
                end=r["end_"],
                speakers=json.loads(r["speakers"]),
                text=r["text"],
            )
            for r in rows
        }

    @staticmethod
    def _meeting(r: sqlite3.Row) -> MeetingInfo:
        return MeetingInfo(
            id=r["id"],
            title=r["title"],
            date=r["date"],
            attendees=json.loads(r["attendees"]),
            duration_seconds=r["duration_seconds"],
            turn_count=r["turn_count"],
            insights=MeetingInsights.model_validate_json(r["insights"]) if r["insights"] else None,
        )
