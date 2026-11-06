"""Runtime configuration. Every knob is an env var (prefix MIA_) so the same image runs locally and in the cloud."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MIA_", env_file=".env", extra="ignore")

    # Storage
    data_dir: Path = Path("./var")
    samples_dir: Path = Path(__file__).resolve().parents[2] / "data" / "samples"
    frontend_dist: Path = Path(__file__).resolve().parents[2] / "frontend" / "dist"

    # Optional voice-to-transcript (pip install -r requirements-voice.txt)
    whisper_model: str = "small"

    # LLM. "anthropic" calls Claude; "offline" is a deterministic extractive stand-in so the app,
    # tests and CI run without an API key. The ANTHROPIC_API_KEY itself is read by the SDK, not here.
    llm_provider: str = "auto"  # auto | anthropic | offline
    llm_model: str = "claude-opus-5-5"
    answer_effort: str = "low"  # chat answers are short and latency-sensitive
    extract_effort: str = "medium"  # one-off structured extraction per meeting
    answer_max_tokens: int = 4000
    use_refusal_fallback: bool = True

    # Embeddings. "fastembed" runs BAAI/bge-small-en-v1.5 locally on CPU (ONNX, no torch).
    # "hash" is a tiny deterministic embedder used by tests.
    embedder: str = "fastembed"
    embedding_model: str = "BAAI/bge-small-en-v1.5"

    # Chunking / retrieval
    chunk_target_words: int = 180
    chunk_overlap_turns: int = 1
    top_k: int = 6
    # Below this best-chunk cosine the assistant says it can't find the answer, without calling the LLM.
    # Calibrated with eval/run_eval.py on bge-small: lowest answerable question 0.52, off-topic 0.43-0.60
    # (they overlap), so 0.50 blocks clearly off-topic questions with no false refusals; the prompt rule
    # "say you couldn't find it" is the second layer for near-misses.
    min_relevance: float = 0.50
    history_turns: int = 6

    # Guardrails
    max_upload_bytes: int = 2_000_000
    max_question_chars: int = 1_000

    @property
    def db_path(self) -> Path:
        return self.data_dir / "mia.sqlite3"


@lru_cache
def get_settings() -> Settings:
    return Settings()
