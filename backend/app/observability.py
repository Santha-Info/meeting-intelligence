"""Observability without extra infrastructure: JSON logs with a per-request trace id, and in-process counters
exposed at /api/metrics. In production the same events map 1:1 onto OpenTelemetry spans / CloudWatch metrics
(see README); the code calls `record(...)` and `log`, so the exporter can change without touching callers."""

import contextvars
import json
import logging
import sys
import threading
import time
import uuid
from collections import defaultdict

trace_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id", default="-")


class JsonFormatter(logging.Formatter):
    _std = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "trace_id": trace_id_var.get(),
            "msg": record.getMessage(),
        }
        payload.update({k: v for k, v in record.__dict__.items() if k not in self._std})
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)


def new_trace_id() -> str:
    tid = uuid.uuid4().hex[:12]
    trace_id_var.set(tid)
    return tid


class Metrics:
    """Counters and simple latency stats. Thread-safe; reset on restart (by design for a demo)."""

    def __init__(self):
        self._lock = threading.Lock()
        self.counters: dict[str, int] = defaultdict(int)
        self.latency_ms: dict[str, list[float]] = defaultdict(list)

    def incr(self, name: str, by: int = 1) -> None:
        with self._lock:
            self.counters[name] += by

    def observe(self, name: str, ms: float) -> None:
        with self._lock:
            values = self.latency_ms[name]
            values.append(ms)
            del values[:-500]  # keep a rolling window

    def snapshot(self) -> dict:
        with self._lock:
            lat = {}
            for name, values in self.latency_ms.items():
                if values:
                    s = sorted(values)
                    lat[name] = {
                        "count": len(s),
                        "p50_ms": round(s[len(s) // 2], 1),
                        "p95_ms": round(s[min(len(s) - 1, int(len(s) * 0.95))], 1),
                    }
            return {"counters": dict(self.counters), "latency": lat}


metrics = Metrics()
