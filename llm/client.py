"""Crusoe (OpenAI-compatible) LLM client: tiers, structured output, validation retry, cache, cost (spec 12)."""
from __future__ import annotations

import copy
import json
import os
import re
import time
import uuid
from typing import Callable, Literal

import openai
from dotenv import load_dotenv
from pydantic import BaseModel, ValidationError

from llm.cache import ResponseCache, cache_key

load_dotenv()

Tier = Literal["ultra", "super", "nano"]
Mode = Literal["json_schema", "json_object", "text"]
MODES: tuple[Mode, ...] = ("json_schema", "json_object", "text")

# USD per 1M tokens: (input, output, cached input or None = billed as input). Spec 12.1.
PRICES_PER_M: dict[str, tuple[float, float, float | None]] = {
    "ultra": (1.00, 3.20, 0.25),
    "super": (0.60, 1.80, None),  # TODO confirm in the Crusoe console
    "nano": (0.30, 1.83, None),
}

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


class ModelNotConfigured(RuntimeError):
    pass


class LLMValidationError(RuntimeError):
    pass


class CallMeta(BaseModel):
    model: str
    prompt_hash: str
    tokens_in: int = 0
    tokens_out: int = 0
    cached_tokens: int = 0
    latency_ms: int = 0
    usd: float = 0.0
    from_cache: bool = False
    fallback_used: bool = False
    mode: str | None = None  # structured-output mode used (json_schema | json_object | text)


def strip_think(text: str) -> str:
    text = _THINK_RE.sub("", text or "")
    if "</think>" in text:  # opening tag consumed by the chat template
        text = text.rsplit("</think>", 1)[1]
    return text.strip()


def clean_json(text: str) -> str:
    text = strip_think(text)
    m = _FENCE_RE.match(text)
    if m:
        text = m.group(1)
    if not text.startswith("{"):
        i, j = text.find("{"), text.rfind("}")
        if i != -1 and j > i:
            text = text[i:j + 1]
    return text.strip()


def model_for(tier: Tier) -> str:
    var = f"MODEL_{tier.upper()}"
    model = (os.environ.get(var) or "").strip()
    if not model:
        raise ModelNotConfigured(f"{var} is empty in .env")
    return model


def usd_for(tier: str, tokens_in: int, tokens_out: int, cached: int) -> float:
    p_in, p_out, p_cached = PRICES_PER_M[tier]
    p_cached = p_in if p_cached is None else p_cached
    return ((tokens_in - cached) * p_in + cached * p_cached + tokens_out * p_out) / 1e6


def _is_format_rejection(e: Exception) -> bool:
    """A 4xx that may mean 'this response_format is not supported' (not auth / rate limit)."""
    return isinstance(e, openai.APIStatusError) and e.status_code in (400, 404, 415, 422)


DEFAULT_TIMEOUT_S = 20.0
DEFAULT_REASONING_TIMEOUT_S = 90.0  # thinking-on calls (chair, director, coach) routinely exceed 20 s
DEFAULT_MAX_RETRIES = 3  # the SDK retries 429/5xx/connection errors with backoff; parallel runs hit Crusoe's request cap


def reasoning_timeout() -> float:
    t = (os.environ.get("LLM_REASONING_TIMEOUT_S") or "").strip()
    return float(t) if t else DEFAULT_REASONING_TIMEOUT_S


def client_options() -> dict:
    """OpenAI client timeout / retries: short by default so a hung endpoint costs one standup
    seconds, not minutes (callers fall back). Override with $LLM_TIMEOUT_S / $LLM_MAX_RETRIES."""
    t = (os.environ.get("LLM_TIMEOUT_S") or "").strip()
    r = (os.environ.get("LLM_MAX_RETRIES") or "").strip()
    return {"timeout": float(t) if t else DEFAULT_TIMEOUT_S,
            "max_retries": int(r) if r else DEFAULT_MAX_RETRIES}


class LLM:
    _mode_by_model: dict[str, Mode] = {}  # process-wide memory of which structured-output mode worked

    def __init__(self, on_call: Callable[[dict], None] | None = None,
                 cache: ResponseCache | None = None, client: openai.OpenAI | None = None):
        self.on_call = on_call
        self.cache = cache if cache is not None else ResponseCache()
        self.client = client or openai.OpenAI(
            api_key=os.environ.get("CRUSOE_API_KEY") or "unset",
            base_url=(os.environ.get("CRUSOE_BASE_URL") or "").strip() or None,
            **client_options())

    def mode_for(self, model: str) -> Mode | None:
        return self._mode_by_model.get(model)

    # ------------------------------------------------------------------ public
    def complete(self, tier: Tier, messages: list[dict], schema: type[BaseModel] | None = None, *,
                 role: str, run_id: str | None, reasoning: bool = False, max_tokens: int = 800,
                 temperature: float = 0.0, fallback: BaseModel | None = None) -> tuple[BaseModel | str, CallMeta]:
        model = model_for(tier)
        key = cache_key(model, messages, schema, temperature, reasoning)
        meta = CallMeta(model=model, prompt_hash=key)

        row = self.cache.get_row(key)
        if row is not None:
            try:
                out = self._parse(row["response_text"], schema)
                meta.from_cache = True
                meta.tokens_in, meta.tokens_out = row.get("tokens_in", 0), row.get("tokens_out", 0)
                self._log(meta, role, run_id, tier, messages, schema, reasoning, row["response_text"])
                return out, meta
            except (ValidationError, ValueError):
                pass  # stale entry (schema changed shape): go live

        t0 = time.perf_counter()
        text = self._call(model, messages, schema, reasoning, max_tokens, temperature, meta)
        error = None
        out: BaseModel | str | None
        if schema is None:
            out = strip_think(text)
        else:
            try:
                out = self._parse(text, schema)
            except (ValidationError, ValueError) as e:
                retry = copy.deepcopy(messages) + [
                    {"role": "assistant", "content": text},
                    {"role": "user", "content": f"Your previous answer failed validation: {e}. "
                                                "Reply with only the corrected JSON."}]
                text = self._call(model, retry, schema, reasoning, max_tokens, temperature, meta)
                try:
                    out = self._parse(text, schema)
                except (ValidationError, ValueError) as e2:
                    error, out = str(e2)[:500], None
        meta.latency_ms = int((time.perf_counter() - t0) * 1000)
        meta.usd = round(usd_for(tier, meta.tokens_in, meta.tokens_out, meta.cached_tokens), 8)

        if out is None:  # second validation failure: safe default (spec 10.6), never cached
            meta.fallback_used = fallback is not None
            self._log(meta, role, run_id, tier, messages, schema, reasoning, text, error=error)
            if fallback is None:
                raise LLMValidationError(f"{schema.__name__} failed validation twice: {error}")
            return fallback, meta

        stored = strip_think(text) if schema is None else clean_json(text)
        self.cache.put(key, model=model, response_text=stored,
                       tokens_in=meta.tokens_in, tokens_out=meta.tokens_out)
        self._log(meta, role, run_id, tier, messages, schema, reasoning, text)
        return out, meta

    # ----------------------------------------------------------------- private
    @staticmethod
    def _parse(text: str, schema: type[BaseModel] | None):
        if schema is None:
            return strip_think(text)
        return schema.model_validate_json(clean_json(text))

    def _call(self, model, messages, schema, reasoning, max_tokens, temperature, meta: CallMeta) -> str:
        kwargs = dict(model=model, max_tokens=max_tokens, temperature=temperature,
                      extra_body={"chat_template_kwargs": {"enable_thinking": bool(reasoning)}})
        if reasoning:  # per-request override; the first real-twin run lost 6 chair/director calls to 20 s timeouts
            kwargs["timeout"] = reasoning_timeout()
        if schema is None:
            return self._account(self.client.chat.completions.create(messages=messages, **kwargs), meta)

        known = self._mode_by_model.get(model)
        last: Exception | None = None
        for mode in (MODES[MODES.index(known):] if known else MODES):
            msgs, extra = messages, {}
            if mode == "json_schema":
                extra["response_format"] = {"type": "json_schema", "json_schema": {
                    "name": schema.__name__, "schema": schema.model_json_schema(), "strict": True}}
            else:
                msgs = copy.deepcopy(messages)
                note = ("Respond with ONLY a JSON object matching this schema: "
                        + json.dumps(schema.model_json_schema()))
                last_user = next((m for m in reversed(msgs) if m["role"] == "user"), None)
                if last_user is None:
                    msgs.append({"role": "user", "content": note})
                else:
                    last_user["content"] += "\n\n" + note
                if mode == "json_object":
                    extra["response_format"] = {"type": "json_object"}
            try:
                resp = self.client.chat.completions.create(messages=msgs, **kwargs, **extra)
            except Exception as e:  # noqa: BLE001
                if mode != "text" and _is_format_rejection(e):
                    last = e
                    continue
                raise
            self._mode_by_model[model] = mode
            meta.mode = mode
            return self._account(resp, meta)
        raise last  # pragma: no cover

    @staticmethod
    def _account(resp, meta: CallMeta) -> str:
        u = getattr(resp, "usage", None)
        if u is not None:
            meta.tokens_in += getattr(u, "prompt_tokens", 0) or 0
            meta.tokens_out += getattr(u, "completion_tokens", 0) or 0
            details = getattr(u, "prompt_tokens_details", None)
            meta.cached_tokens += (getattr(details, "cached_tokens", 0) or 0) if details else 0
        return resp.choices[0].message.content or ""

    def _log(self, meta: CallMeta, role, run_id, tier, messages, schema, reasoning, text, error=None):
        """Row shaped like spec 8.2 `llm_calls`, plus `kind` and `fallback_used`."""
        if self.on_call is None:
            return
        self.on_call({
            "call_id": uuid.uuid4().hex, "run_id": run_id, "role": role, "model": meta.model,
            "prompt_hash": meta.prompt_hash,
            "request": {"tier": tier, "messages": messages, "schema": schema.__name__ if schema else None,
                        "reasoning": reasoning, "mode": meta.mode},
            "response": {"text": text, **({"llm_fallback": True, "error": error} if error else {})},
            "tokens_in": meta.tokens_in, "tokens_out": meta.tokens_out, "cached_tokens": meta.cached_tokens,
            "latency_ms": meta.latency_ms, "usd": meta.usd, "from_cache": meta.from_cache,
            "fallback_used": meta.fallback_used, "kind": "llm_fallback" if error else "llm_call",
        })
