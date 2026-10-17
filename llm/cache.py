"""Append-only JSONL response cache (spec 11.2). Key = sha256 of the canonical request."""
from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path

from pydantic import BaseModel

DEFAULT_PATH = Path(__file__).resolve().parents[1] / "recordings" / "llm_cache.jsonl"


def cache_key(model: str, messages: list[dict], schema: type[BaseModel] | None,
              temperature: float, reasoning: bool = False) -> str:
    payload = {
        "model": model,
        "messages": messages,
        "schema": schema.model_json_schema() if schema else None,
        "temperature": temperature,
        "reasoning": reasoning,  # thinking on/off changes the answer, so it is part of the key
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


class ResponseCache:
    """Loads the JSONL into a dict at startup; `put` appends one line. Later lines win."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or os.environ.get("LLM_CACHE_PATH") or DEFAULT_PATH)
        self._data: dict[str, dict] = {}
        self._lock = threading.Lock()
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    try:
                        row = json.loads(line)
                        self._data[row["key"]] = row
                    except (json.JSONDecodeError, KeyError):
                        continue  # tolerate a torn last line

    @staticmethod
    def reads_disabled() -> bool:
        return os.environ.get("LLM_CACHE_DISABLE") == "1"

    def get(self, key: str) -> str | None:
        if self.reads_disabled():
            return None
        row = self._data.get(key)
        return row["response_text"] if row else None

    def get_row(self, key: str) -> dict | None:
        return None if self.reads_disabled() else self._data.get(key)

    def put(self, key: str, *, model: str, response_text: str, tokens_in: int, tokens_out: int) -> None:
        row = {"key": key, "model": model, "response_text": response_text,
               "tokens_in": tokens_in, "tokens_out": tokens_out}
        with self._lock:
            self._data[key] = row
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row) + "\n")
