"""Tests run fully offline: HashEmbedder (no model download) + OfflineLLM (no API key), temp SQLite per test."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.embeddings import HashEmbedder
from app.llm import OfflineLLM
from app.main import create_app
from app.service import MeetingService
from app.store import Store

SAMPLES = Path(__file__).resolve().parents[2] / "data" / "samples"


@pytest.fixture
def settings(tmp_path) -> Settings:
    # Hash embeddings produce lower cosines than bge, so the relevance gate is lowered for tests.
    return Settings(data_dir=tmp_path, embedder="hash", llm_provider="offline", min_relevance=0.10, samples_dir=SAMPLES)


@pytest.fixture
def service(settings) -> MeetingService:
    return MeetingService(settings, Store(settings.db_path), HashEmbedder(), OfflineLLM())


@pytest.fixture
def client(settings, service):
    with TestClient(create_app(settings, service)) as c:
        yield c


@pytest.fixture
def loaded_client(client):
    assert client.post("/api/meetings/samples").status_code == 200
    return client


def sse_events(response) -> list[tuple[str, dict]]:
    events = []
    for frame in response.text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in frame.split("\n") if ": " in line)
        events.append((lines["event"], json.loads(lines["data"])))
    return events
