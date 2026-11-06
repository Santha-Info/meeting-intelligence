"""HTTP API. Routes are thin: validate input, call MeetingService, shape the response."""

import json
import logging
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import iterate_in_threadpool, run_in_threadpool

from app import guardrails
from app.config import Settings, get_settings
from app.embeddings import build_embedder
from app.ingest.parser import TranscriptParseError
from app.llm import build_llm
from app.observability import metrics, new_trace_id, setup_logging
from app.schemas import ChatRequest
from app.service import MeetingService
from app.store import Store

log = logging.getLogger(__name__)


def create_app(settings: Settings | None = None, service: MeetingService | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        setup_logging()
        if service is None:
            app.state.service = MeetingService(
                settings,
                Store(settings.db_path),
                build_embedder(settings.embedder, settings.embedding_model),
                build_llm(
                    settings.llm_provider,
                    model=settings.llm_model,
                    answer_effort=settings.answer_effort,
                    extract_effort=settings.extract_effort,
                    max_tokens=settings.answer_max_tokens,
                    use_fallback=settings.use_refusal_fallback,
                ),
            )
        else:
            app.state.service = service
        log.info("started", extra={"llm": app.state.service.llm.mode, "embedder": settings.embedder})
        yield

    app = FastAPI(title="Meeting Intelligence Assistant", version="1.0.0", lifespan=lifespan)

    @app.middleware("http")
    async def trace(request: Request, call_next):
        tid = new_trace_id()
        t0 = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Trace-Id"] = tid
        if request.url.path.startswith("/api/"):
            metrics.incr(f"http_{response.status_code}")
            log.info(
                "request",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "ms": round((time.perf_counter() - t0) * 1000),
                },
            )
        return response

    def svc(request: Request) -> MeetingService:
        return request.app.state.service

    # ---- meetings -----------------------------------------------------------------------
    @app.get("/api/health")
    def health(request: Request):
        s = svc(request)
        return {
            "status": "ok",
            "llm_mode": s.llm.mode,
            "model": s.llm.model,
            "embedder": settings.embedding_model if settings.embedder == "fastembed" else settings.embedder,
            "meetings": len(s.store.list_meetings()),
        }

    @app.get("/api/meetings")
    def list_meetings(request: Request):
        return svc(request).store.list_meetings()

    @app.post("/api/meetings")
    async def upload(request: Request, files: list[UploadFile] = File(...)):
        results, errors = [], []
        for f in files:
            raw = await f.read()
            try:
                guardrails.check_upload(f.filename or "", len(raw), settings.max_upload_bytes)
                text = guardrails.decode_upload(raw)
                results.append(await run_in_threadpool(svc(request).ingest, f.filename, text))
            except (guardrails.GuardrailError, TranscriptParseError) as e:
                errors.append({"file": f.filename, "error": str(e)})
        if errors and not results:
            raise HTTPException(status_code=422, detail="; ".join(f"{e['file']}: {e['error']}" for e in errors))
        return {"ingested": results, "errors": errors}

    @app.post("/api/meetings/samples")
    async def load_samples(request: Request):
        return {"ingested": await run_in_threadpool(svc(request).ingest_samples, settings.samples_dir)}

    @app.get("/api/meetings/{meeting_id}")
    def get_meeting(request: Request, meeting_id: str):
        meeting = svc(request).store.get_meeting(meeting_id)
        if not meeting:
            raise HTTPException(status_code=404, detail="Meeting not found")
        return meeting

    @app.get("/api/meetings/{meeting_id}/transcript")
    def get_transcript(request: Request, meeting_id: str):
        if not svc(request).store.get_meeting(meeting_id):
            raise HTTPException(status_code=404, detail="Meeting not found")
        return {"meeting_id": meeting_id, "turns": svc(request).store.get_turns(meeting_id)}

    @app.post("/api/meetings/{meeting_id}/insights")
    async def regenerate_insights(request: Request, meeting_id: str):
        meeting = svc(request).store.get_meeting(meeting_id)
        if not meeting:
            raise HTTPException(status_code=404, detail="Meeting not found")
        insights = await run_in_threadpool(svc(request).refresh_insights, meeting_id, meeting.title)
        if insights is None:
            raise HTTPException(
                status_code=502, detail="Insight extraction failed; see server logs (trace id in header)"
            )
        return insights

    @app.delete("/api/meetings/{meeting_id}")
    def delete_meeting(request: Request, meeting_id: str):
        if not svc(request).store.delete_meeting(meeting_id):
            raise HTTPException(status_code=404, detail="Meeting not found")
        return {"deleted": meeting_id}

    @app.get("/api/action-items")
    def action_items(request: Request):
        """All action items across meetings, grouped by owner - the 'what do I owe' view."""
        rows = []
        for m in svc(request).store.list_meetings():
            for a in m.insights.action_items if m.insights else []:
                rows.append(a.model_dump() | {"meeting_id": m.id, "meeting_title": m.title, "meeting_date": m.date})
        return sorted(rows, key=lambda r: (r["owner"].lower(), r["meeting_date"] or ""))

    # ---- chat ---------------------------------------------------------------------------
    @app.post("/api/chat")
    def chat(request: Request, body: ChatRequest):
        try:
            guardrails.clean_question(body.question, settings.max_question_chars)
        except guardrails.GuardrailError as e:
            raise HTTPException(status_code=422, detail=str(e)) from e

        def events():
            try:
                for event, data in svc(request).answer(body):
                    yield f"event: {event}\ndata: {json.dumps(data)}\n\n"
            except Exception as e:  # stream is already open: report the error as an event, not a 500
                log.exception("chat failed")
                metrics.incr("chat_errors")
                yield f"event: error\ndata: {json.dumps({'message': f'Something went wrong: {type(e).__name__}'})}\n\n"

        return StreamingResponse(
            iterate_in_threadpool(events()),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ---- voice (optional bonus) ------------------------------------------------------------
    @app.post("/api/transcribe")
    async def transcribe(request: Request, file: UploadFile = File(...)):
        from app.transcribe import TranscriptionUnavailable, transcribe_to_text

        suffix = Path(file.filename or "audio").suffix.lower()
        if suffix not in (".mp3", ".wav", ".m4a", ".mp4", ".webm", ".ogg"):
            raise HTTPException(status_code=422, detail="Unsupported audio type")
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(await file.read())
        try:
            title = Path(file.filename or "Recorded meeting").stem
            text = await run_in_threadpool(transcribe_to_text, Path(tmp.name), title, settings.whisper_model)
        except TranscriptionUnavailable as e:
            raise HTTPException(status_code=501, detail=str(e)) from e
        finally:
            Path(tmp.name).unlink(missing_ok=True)
        return await run_in_threadpool(svc(request).ingest, f"{title}.txt", text)

    @app.get("/api/metrics")
    def get_metrics():
        return metrics.snapshot()

    # ---- static frontend (built by `npm run build`; the Docker image does this) ------------
    dist = settings.frontend_dist
    if (dist / "index.html").exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            if path.startswith("api/"):
                raise HTTPException(status_code=404, detail="Not found")
            return FileResponse(dist / "index.html")

    return app


app = create_app()
