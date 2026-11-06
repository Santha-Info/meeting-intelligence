from tests.conftest import sse_events


def test_health_reports_offline_mode(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok" and body["llm_mode"] == "offline"


def test_samples_ingest_with_insights(loaded_client):
    meetings = loaded_client.get("/api/meetings").json()
    assert len(meetings) == 3
    incident = next(m for m in meetings if "Incident" in m["title"])
    assert incident["date"] == "2026-10-09"
    assert incident["insights"]["action_items"], "offline extractor should find 'I will ... by <date>' commitments"


def test_reingesting_same_file_is_idempotent(loaded_client):
    loaded_client.post("/api/meetings/samples")
    assert len(loaded_client.get("/api/meetings").json()) == 3


def test_chat_streams_retrieval_then_deltas_then_done(loaded_client):
    r = loaded_client.post("/api/chat", json={"question": "What was the root cause of the payment failures?"})
    assert r.status_code == 200
    events = sse_events(r)
    kinds = [e for e, _ in events]
    assert kinds[0] == "retrieval" and kinds[-1] == "done" and "delta" in kinds
    citations = events[0][1]["citations"]
    assert any("Incident" in c["meeting_title"] for c in citations[:2]), "incident review should rank top"
    done = events[-1][1]
    assert done["cited"] and all(1 <= n <= len(citations) for n in done["cited"])


def test_meeting_scope_filter(loaded_client):
    meetings = loaded_client.get("/api/meetings").json()
    retro = next(m for m in meetings if "Retrospective" in m["title"])
    events = sse_events(
        loaded_client.post("/api/chat", json={"question": "What did the team decide?", "meeting_ids": [retro["id"]]})
    )
    assert {c["meeting_id"] for c in events[0][1]["citations"]} == {retro["id"]}


def test_follow_up_question_reuses_previous_question_for_retrieval(loaded_client):
    history = [
        {"role": "user", "content": "Who owns the canary deployment?"},
        {"role": "assistant", "content": "Sofia [1]."},
    ]
    response = loaded_client.post("/api/chat", json={"question": "and when is it due?", "history": history})
    done = sse_events(response)[-1][1]
    assert "canary" in done["retrieval_query"]


def test_question_validation(client):
    assert client.post("/api/chat", json={"question": "   "}).status_code == 422


def test_upload_rejects_bad_files_and_accepts_good_ones(client):
    bad = client.post("/api/meetings", files=[("files", ("notes.txt", b"no timestamps here", "text/plain"))])
    assert bad.status_code == 422
    good = client.post(
        "/api/meetings",
        files=[("files", ("standup.txt", b"[00:00:01] Ann: I will send the report by October 3rd.", "text/plain"))],
    )
    assert good.status_code == 200 and len(good.json()["ingested"]) == 1


def test_transcript_action_items_and_delete(loaded_client):
    m = loaded_client.get("/api/meetings").json()[0]
    assert loaded_client.get(f"/api/meetings/{m['id']}/transcript").json()["turns"]
    assert loaded_client.get("/api/action-items").json()
    assert loaded_client.delete(f"/api/meetings/{m['id']}").status_code == 200
    assert loaded_client.get(f"/api/meetings/{m['id']}").status_code == 404


def test_metrics_and_trace_header(loaded_client):
    r = loaded_client.get("/api/metrics")
    assert "X-Trace-Id" in r.headers
    assert r.json()["counters"]["meetings_ingested"] >= 3
