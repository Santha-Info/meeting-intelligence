# Meeting Intelligence Assistant

Ask questions about your meetings and get answers grounded in the transcripts, with a clickable citation
to the exact moment each fact was said. The assistant also extracts **decisions, action items (owner +
due date) and open questions** from every meeting, and gives an "all action items by owner" view.

> Assignment option 3 (Meeting Intelligence System), plus the optional voice-to-transcript bonus.

![Answer with citations](docs/screenshots/02-answer-with-citations.png)

| Ask, with sources and retrieval trace | Jump to the cited moment | Extracted insights |
|---|---|---|
| ![](docs/screenshots/02-answer-with-citations.png) | ![](docs/screenshots/03-transcript-jump.png) | ![](docs/screenshots/04-insights.png) |

*(Screenshots: ✍️ retake them with `ANTHROPIC_API_KEY` set before submitting; the current ones show offline demo mode.)*

---

## Quick start

**Docker (recommended)**
```bash
cp .env.example .env          # optional: add ANTHROPIC_API_KEY for Claude answers
docker compose up --build     # http://localhost:8000  ->  "Load 3 sample meetings"
```

**Local development**
```bash
# backend (Python 3.11+)
cd backend
python -m venv .venv && . .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
export ANTHROPIC_API_KEY=sk-ant-...                 # optional; without it the app runs in offline demo mode
uvicorn app.main:app --reload                       # http://localhost:8000/docs for the API

# frontend (Node 20+), in a second terminal
cd frontend && npm install && npm run dev           # http://localhost:5173 (proxies /api to :8000)
```

**Tests, lint and the retrieval evaluation** (all offline, no API key)
```bash
cd backend
pytest                         # 30 tests: parser, chunker, guardrails, API end-to-end
ruff check . && ruff format --check .
python -m eval.run_eval        # retrieval hit@k / MRR on the golden set (downloads bge-small once)
```

Without an API key the app uses an **offline demo mode**: retrieval and the whole UI work, and answers and
insights come from a simple extractive heuristic that is clearly labelled in the UI. This keeps tests and CI
free of secrets and lets anyone run the demo.

---

## Architecture

```mermaid
flowchart LR
  subgraph Browser["Vue 3 SPA"]
    U[Upload / samples] --> A
    Q[Ask] -->|SSE stream| A
    I[Insights] --> A
    T[Transcript] --> A
  end
  A[FastAPI] --> P[Parser<br/>txt / WebVTT]
  P --> C[Speaker-turn chunker]
  C --> E[bge-small embeddings<br/>local ONNX]
  E --> S[(SQLite<br/>chunks + FTS5 + vectors)]
  P --> X[Claude: structured extraction<br/>summary / decisions / actions]
  X --> S
  A --> R[Hybrid retrieval<br/>BM25 + cosine, RRF]
  R --> S
  R --> G{Relevance gate}
  G -->|weak| N[“Couldn't find it”]
  G -->|ok| L[Claude: grounded answer<br/>streamed, cited]
  L --> V[Citation validation]
  V -->|SSE| Q
```

**Ingest:** parse → chunk by speaker turns → embed → store (+ FTS index) → extract insights with Claude
into a validated Pydantic schema. The meeting is searchable before extraction finishes, and a failed extraction
doesn't block search.

**Ask:** clean question → (follow-up? combine with the previous question) → hybrid retrieval → relevance
gate → prompt with numbered excerpts → stream Claude's answer over SSE → validate citations → `done` event with
latency, token usage and trace id.

| Path | Responsibility |
|---|---|
| `backend/app/ingest/parser.py` | `[hh:mm:ss] Speaker: text`, `hh:mm:ss Speaker: text`, `mm:ss`, wrapped lines, WebVTT `<v Speaker>` cues, `Meeting/Date/Attendees` headers |
| `backend/app/ingest/chunker.py` | Windows of whole turns (~180 words), 1-turn overlap, time range + speakers per chunk |
| `backend/app/embeddings.py` | `bge-small-en-v1.5` via fastembed (ONNX, CPU); hash embedder for tests |
| `backend/app/store.py` | SQLite: meetings, turns, chunks (+ vectors), FTS5 index; idempotent re-ingest |
| `backend/app/retrieval.py` | Dense + BM25, Reciprocal Rank Fusion |
| `backend/app/llm.py` | Claude (streaming answers, `messages.parse` extraction, prompt caching, refusal fallback) and the offline stand-in |
| `backend/app/prompts.py` | Byte-stable system prompts; excerpts delimited and escaped |
| `backend/app/guardrails.py` | Upload/question limits, injection heuristic, citation validation |
| `backend/app/service.py` | Orchestration of ingest and Q&A |
| `backend/app/observability.py` | JSON logs with trace id, counters + p50/p95 at `/api/metrics` |
| `backend/eval/` | Golden questions + retrieval evaluation script |
| `frontend/src/` | Vue 3: chat with citation chips, insights, transcript with jump-to-time |

### API

| Method | Path | |
|---|---|---|
| `POST` | `/api/meetings` | Upload `.txt` / `.vtt` transcripts (multipart, several at once) |
| `POST` | `/api/meetings/samples` | Load the three bundled sample meetings |
| `GET` | `/api/meetings`, `/api/meetings/{id}`, `/api/meetings/{id}/transcript` | Read |
| `POST` | `/api/meetings/{id}/insights` | Re-run extraction |
| `DELETE` | `/api/meetings/{id}` | Delete |
| `GET` | `/api/action-items` | All action items across meetings, by owner |
| `POST` | `/api/chat` | SSE stream: `retrieval` → `delta`* → `done` (or `error`) |
| `POST` | `/api/transcribe` | Optional: audio → transcript → ingest (`pip install -r requirements-voice.txt`) |
| `GET` | `/api/health`, `/api/metrics` | Ops |

---

## RAG / LLM approach

| Concern | What the code does |
|---|---|
| **Chunking** | Whole speaker turns grouped to ~180 words, last turn repeated in the next chunk. Each chunk keeps start/end time and speakers. |
| **Embeddings** | `BAAI/bge-small-en-v1.5` (384-dim) run locally with fastembed/ONNX, baked into the Docker image. |
| **Vector store** | SQLite: vectors as BLOBs with brute-force cosine, plus FTS5 for BM25. |
| **Retrieval** | Hybrid: top candidates from cosine and BM25, merged with Reciprocal Rank Fusion (k=60), top 6. Optional meeting filter. |
| **LLM** | Claude (`claude-opus-5-5` by default, `MIA_LLM_MODEL` to change). Answers stream at effort `low`; extraction uses effort `medium` with `messages.parse()` into a Pydantic schema. |
| **Prompt & context** | Byte-stable system prompt with `cache_control`. Excerpts are numbered, tagged with meeting/time/speakers and escaped. Last 6 chat turns are sent; short or pronoun-led follow-ups are searched together with the previous question. |
| **Guardrails** | File type/size and question length limits, control-char stripping, relevance gate before the LLM, "excerpts are data, not instructions" rule with delimiter escaping, citation validation, server-side refusal fallback. |
| **Quality** | Golden-set retrieval evaluation (below), ungrounded-answer flag, pytest suite. |
| **Observability** | JSON logs with trace id (also in the `X-Trace-Id` header and the UI), per-answer retrieval scores, token usage and latency; `/api/metrics` counters and p50/p95. |

### Retrieval evaluation (`python -m eval.run_eval`)

18 answerable questions about the samples, each labelled with the meeting and moment that should be
retrieved, plus 4 off-topic questions. Measured with bge-small, k=6:

| Retrieval | hit@1 | hit@6 | MRR |
|---|---|---|---|
| **Hybrid (BM25 + vector, RRF)** | **0.83** | 1.00 | **0.91** |
| Vector only | 0.50 | 1.00 | 0.71 |
| Keyword only (BM25) | 0.72 | 1.00 | 0.83 |

Best-chunk cosine: answerable questions 0.52–0.80, off-topic 0.43–0.60. The ranges overlap ("price of Bitcoin"
matches the maps-API *pricing* discussion), so the gate is set at **0.50**: it rejects clearly off-topic
questions with no false refusals on this set, and near-misses fall through to the prompt rule *"say you
couldn't find it"*. The sample set is small; see "Next steps".

---

## Productionizing on AWS

| Area | Change |
|---|---|
| Compute | Container on **ECS Fargate** (or App Runner) behind an ALB; long SSE connections need ALB idle timeout ≥ 120 s. The API is stateless once storage moves out. |
| Storage | **RDS PostgreSQL + pgvector** (HNSW index) replaces SQLite; Postgres full-text (`tsvector`) replaces FTS5, so hybrid search stays one query. Transcripts and audio in **S3**. |
| Ingestion | Upload to S3 → **SQS** → worker (Fargate/Lambda) for parse/embed/extract, so uploads return immediately and large files don't block the API. Whisper on a GPU worker if voice is used. |
| LLM | Claude via the Anthropic API or **Amazon Bedrock** (keeps traffic and billing in AWS; the SDK has a Bedrock client). Prompt caching for the system prompt; Message Batches for bulk re-extraction. |
| Secrets & auth | API key in **Secrets Manager**; Cognito / SSO in front; meetings scoped per tenant/user in every query. |
| Observability | OpenTelemetry → CloudWatch / X-Ray; alarms on error rate, p95 latency, refusal and ungrounded-answer rates, token spend. |
| Quality | Larger golden set from real (consented) transcripts, LLM-judged faithfulness/answer-quality evals in CI, feedback buttons in the UI feeding the eval set. |
| Delivery | IaC (CDK/Terraform), CI builds the image → ECR → staged deploy. |

---

## Key technical decisions and why

> ✍️ **Write in your own words.** Notes to draw from (the facts are in the code and the eval):
> - Meeting-aware chunking (speaker turns + timestamps) instead of fixed-size text chunks: why it matters for "who said what" and for citations.
> - Hybrid retrieval: the eval numbers above; exact tokens (names, dates, "Helm chart") vs paraphrases.
> - SQLite now vs pgvector later: zero services for a 2-day build; storage sits behind one class.
> - Local embeddings (bge-small) vs an embeddings API: free, offline, data stays local, good enough on the eval.
> - Relevance gate calibrated from data, not a guessed number, and why one threshold isn't enough.
> - Structured extraction at ingest (decisions/actions as data) instead of asking the LLM every time.
> - Offline mode: tests and CI without secrets, demo runs anywhere.
> - Follow-up handling without an extra LLM call: the trade-off (cheaper and faster, less robust than LLM query rewriting).

## Engineering standards

> ✍️ **Write in your own words.** What's in the repo: typed Pydantic models; thin routes, logic in a service layer;
> config only from env vars; 30 offline tests plus a retrieval eval; ruff lint and format; CI (lint, tests, eval
> smoke test, front-end build, Docker build); non-root container with a healthcheck and the model baked in;
> structured logs with trace ids. Skipped: auth, migrations, front-end tests, load testing, rate limiting. Say why
> you skipped each one.

## How I used AI tools

> ✍️ **Write in your own words; this section is explicitly about *your* process.** Be specific and honest:
> which tool(s) you used and for what (scaffolding, tests, CSS, reviewing), what you decided yourself
> (option choice, chunking strategy, hybrid retrieval, threshold calibration), what you rejected or rewrote,
> how you checked the output (tests, the eval, reading every diff), and your do's and don'ts.
> `CLAUDE.md` holds the project conventions given to the coding assistant, which keeps its output consistent.

## What I'd do differently with more time

> ✍️ **Write in your own words.** Candidates: speaker diarization for audio (Whisper has no speaker labels);
> LLM-judged answer-quality evals and a bigger, real golden set; LLM query rewriting for follow-ups; a cross-encoder
> re-ranker; pgvector and async ingestion; auth and multi-tenancy; feedback buttons; front-end tests;
> recurring-topic tracking across meetings ("what has been blocked for three weeks?").

## Known limitations

- Speaker labels and timestamps are required in text transcripts; free-form notes are rejected with a clear error.
- Voice transcription labels everything "Speaker" (no diarization).
- Brute-force vector search is fine to roughly 100k chunks; beyond that use pgvector/HNSW.
- The relevance threshold was tuned on a small synthetic set and should be re-tuned on real data.
- Offline mode is a demo aid, not an AI answer.
