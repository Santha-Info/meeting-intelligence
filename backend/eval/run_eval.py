"""Retrieval evaluation - runs locally, no API key, no cost.

For each golden question: does the expected moment (meeting + timestamp) appear in the top-k chunks?
Reports hit@1, hit@k and MRR for hybrid vs dense-only vs keyword-only, plus the best-cosine distribution for
answerable vs off-topic questions, which is how the relevance threshold (MIA_MIN_RELEVANCE) was chosen.

    python -m eval.run_eval              # bge-small (downloads ~70 MB once)
    python -m eval.run_eval --embedder hash
"""

import argparse
import json
import statistics
import tempfile
from pathlib import Path

from app.config import Settings
from app.embeddings import build_embedder
from app.llm import OfflineLLM
from app.service import MeetingService
from app.store import Store

HERE = Path(__file__).parent
MEETING_KEYS = {"incident": "incident", "roadmap": "roadmap", "retro": "retrospective"}


def ts(s: str) -> float:
    m, sec = s.split(":")
    return int(m) * 60 + int(sec)


def rank_of_expected(hits, meeting_id: str, at: float) -> int | None:
    for rank, h in enumerate(hits, start=1):
        if h.chunk.meeting_id == meeting_id and h.chunk.start <= at <= max(h.chunk.end, h.chunk.start):
            return rank
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--embedder", default="fastembed")
    ap.add_argument("-k", type=int, default=6)
    args = ap.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        settings = Settings(data_dir=Path(tmp), embedder=args.embedder, llm_provider="offline")
        embedder = build_embedder(settings.embedder, settings.embedding_model)
        service = MeetingService(settings, Store(settings.db_path), embedder, OfflineLLM())
        meetings = service.ingest_samples(settings.samples_dir)
        ids = {key: next(m.id for m in meetings if word in m.title.lower()) for key, word in MEETING_KEYS.items()}
        golden = [json.loads(line) for line in (HERE / "golden.jsonl").read_text().splitlines() if line.strip()]

        modes = {"hybrid": {}, "dense": {"keyword": False}, "keyword": {"dense": False}}
        report = {}
        for mode in modes:
            ranks = []
            for g in (g for g in golden if not g.get("negative")):
                hits = search(service, g["q"], args.k, mode)
                ranks.append(rank_of_expected(hits, ids[g["meeting"]], ts(g["at"])))
            found = [r for r in ranks if r]
            report[mode] = {
                "hit@1": round(sum(r == 1 for r in found) / len(ranks), 3),
                f"hit@{args.k}": round(len(found) / len(ranks), 3),
                "mrr": round(sum(1 / r for r in found) / len(ranks), 3),
            }

        pos = [
            max(h.cosine for h in service.retriever.search(g["q"], None, args.k))
            for g in golden
            if not g.get("negative")
        ]
        neg = [
            max(h.cosine for h in service.retriever.search(g["q"], None, args.k)) for g in golden if g.get("negative")
        ]
        report["best_cosine"] = {
            "answerable": {"min": round(min(pos), 3), "median": round(statistics.median(pos), 3)},
            "off_topic": {"max": round(max(neg), 3), "median": round(statistics.median(neg), 3)},
            "suggested_threshold": round((min(pos) + max(neg)) / 2, 3),
        }
        print(json.dumps({"embedder": args.embedder, "k": args.k, "questions": len(pos), **report}, indent=2))


def search(service: MeetingService, q: str, k: int, mode: str):
    """Dense-only / keyword-only ablations reuse the retriever's pieces to show what hybrid adds."""
    if mode == "hybrid":
        return service.retriever.search(q, None, k)
    from app.retrieval import Hit

    store = service.store
    if mode == "keyword":
        ranked = [cid for cid, _ in store.keyword_search(q, None, k)]
    else:
        ids, vectors = store.load_vectors(None)
        cos = vectors @ service.embedder.embed_query(q)
        ranked = [ids[i] for i in cos.argsort()[::-1][:k]]
    chunks = store.get_chunks(ranked)
    return [Hit(chunk=chunks[c], rrf=0, cosine=0, keyword_rank=None, dense_rank=None) for c in ranked if c in chunks]


if __name__ == "__main__":
    main()
