"""Phase-0 smoke test: one JSON-validated call per configured tier, then a cache replay.

Run: uv run python -m llm.smoke   (uses a throwaway cache so the first call is always live)
"""
from __future__ import annotations

import os
import sys
import tempfile

import openai
from pydantic import BaseModel

from llm.cache import ResponseCache
from llm.client import LLM, ModelNotConfigured, model_for
from llm.prompt import build_messages

SLOW_S = {"super": 8.0, "ultra": 20.0}


class Ping(BaseModel):
    answer: str
    number: int


def _err(e: Exception) -> str:
    if isinstance(e, openai.APIStatusError):
        body = (e.response.text if e.response is not None else str(e))[:300]
        return f"HTTP {e.status_code}: {body}"
    return f"{type(e).__name__}: {str(e)[:300]}"


def main() -> int:
    cache_path = os.path.join(tempfile.mkdtemp(prefix="llm_smoke_"), "cache.jsonl")
    llm = LLM(cache=ResponseCache(cache_path))
    msgs = build_messages("You are a test harness. Follow instructions exactly.", [], "(empty)",
                          "Reply with answer='pong' and number=42")
    for tier in ("ultra", "super", "nano"):
        try:
            model = model_for(tier)
        except ModelNotConfigured as e:
            print(f"[{tier}] SKIP: {e}")
            continue
        try:
            out, m = llm.complete(tier, msgs, Ping, role="smoke", run_id=None, reasoning=False)
            print(f"[{tier}] model={model} mode={m.mode} latency_ms={m.latency_ms} tokens_in={m.tokens_in} "
                  f"tokens_out={m.tokens_out} cached_tokens={m.cached_tokens} usd={m.usd:.6f} "
                  f"fallback_used={m.fallback_used} -> {out!r}")
            _, m2 = llm.complete(tier, msgs, Ping, role="smoke", run_id=None, reasoning=False)
            print(f"[{tier}] repeat: from_cache={m2.from_cache} latency_ms={m2.latency_ms} usd={m2.usd}")
            lat = m.latency_ms
            if tier == "ultra":
                out3, m3 = llm.complete(tier, msgs, Ping, role="smoke", run_id=None, reasoning=True,
                                        max_tokens=2000)
                print(f"[{tier}] reasoning=True latency_ms={m3.latency_ms} tokens_out={m3.tokens_out} "
                      f"usd={m3.usd:.6f} -> {out3!r}")
                lat = max(lat, m3.latency_ms)
            limit = SLOW_S.get(tier)
            if limit and lat / 1000 > limit:
                print(f"[{tier}] VERDICT: SLOW ({lat/1000:.1f}s > {limit:.0f}s): move roles to Nano")
            else:
                print(f"[{tier}] VERDICT: OK ({lat/1000:.1f}s" + (f" <= {limit:.0f}s)" if limit else ", no gate)"))
        except Exception as e:  # noqa: BLE001
            print(f"[{tier}] ERROR model={model}: {_err(e)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
