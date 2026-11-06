"""Hybrid retrieval: dense (cosine on bge embeddings) + sparse (BM25 via FTS5), fused with Reciprocal Rank Fusion.

Why hybrid for meetings: people ask with exact tokens ("Helm chart", "Oct 23rd", a person's name) where
BM25 is strong, and with paraphrases ("why did checkout fail?") where embeddings are strong. RRF merges the
two rankings without having to calibrate their very different score scales.

The relevance gate uses the dense cosine of the best hit: if nothing is semantically close, we refuse to
answer rather than let the LLM improvise.
"""

from dataclasses import dataclass

import numpy as np

from app.embeddings import Embedder
from app.schemas import Chunk
from app.store import Store

RRF_K = 60  # standard constant from the RRF paper; dampens the weight of top ranks


@dataclass
class Hit:
    chunk: Chunk
    rrf: float
    cosine: float
    keyword_rank: int | None
    dense_rank: int | None


class Retriever:
    def __init__(self, store: Store, embedder: Embedder):
        self.store = store
        self.embedder = embedder

    def search(self, query: str, meeting_ids: list[str] | None, top_k: int) -> list[Hit]:
        ids, vectors = self.store.load_vectors(meeting_ids)
        if not ids:
            return []
        q = self.embedder.embed_query(query)
        cos = vectors @ q
        candidates = max(top_k * 4, 20)
        dense_order = np.argsort(-cos)[:candidates]
        dense_rank = {ids[i]: r for r, i in enumerate(dense_order)}
        cosine = {ids[i]: float(cos[i]) for i in range(len(ids))}

        keyword_rank = {cid: r for r, (cid, _) in enumerate(self.store.keyword_search(query, meeting_ids, candidates))}

        scores: dict[str, float] = {}
        for cid, r in dense_rank.items():
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (RRF_K + r + 1)
        for cid, r in keyword_rank.items():
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (RRF_K + r + 1)

        best = sorted(scores, key=scores.get, reverse=True)[:top_k]
        chunks = self.store.get_chunks(best)
        return [
            Hit(
                chunk=chunks[cid],
                rrf=scores[cid],
                cosine=cosine.get(cid, 0.0),
                keyword_rank=keyword_rank.get(cid),
                dense_rank=dense_rank.get(cid),
            )
            for cid in best
            if cid in chunks
        ]
