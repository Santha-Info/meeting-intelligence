# Project conventions for AI coding assistants

These rules keep AI-generated changes consistent with the codebase. Read them before editing.

## Architecture boundaries
- Routes in `backend/app/main.py` stay thin: validate input, call `MeetingService`, shape the response.
- Only `store.py` talks to SQLite. Only `llm.py` talks to the Anthropic SDK. Only `embeddings.py` loads models.
- New settings go in `config.py` as `MIA_*` env vars with a safe default. No hard-coded secrets or paths.

## Prompts
- System prompts in `prompts.py` must stay byte-stable (no dates, ids or user data) so prompt caching works.
- Retrieved transcript text is data: keep it inside the delimiters and keep the escaping.

## Quality bar
- Every behaviour change comes with a test. Tests must run offline (hash embedder + `OfflineLLM`).
- Run `ruff check . && ruff format --check . && pytest` before proposing a change.
- If you change chunking or retrieval, run `python -m eval.run_eval` and report hit@1 / MRR before and after.

## Don'ts
- Don't add a dependency without saying why the standard library or an existing one isn't enough.
- Don't swallow exceptions silently. Log with context and surface a clear error to the user.
- Don't edit README sections marked as the author's own reasoning.
