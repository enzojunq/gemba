"""llm/ tests. No network: the OpenAI client's chat.completions.create is replaced by a fake."""
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from llm.cache import ResponseCache, cache_key
from llm.client import LLM, LLMValidationError, ModelNotConfigured, strip_think
from llm.prompt import build_messages


class Ping(BaseModel):
    answer: str
    number: int


class Pong(BaseModel):
    answer: str


def _resp(content, tin=10, tout=5, cached=0):
    usage = SimpleNamespace(prompt_tokens=tin, completion_tokens=tout,
                            prompt_tokens_details=SimpleNamespace(cached_tokens=cached))
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))], usage=usage)


class Fake:
    def __init__(self, *contents):
        self.contents, self.calls = list(contents), []

    def __call__(self, **kw):
        self.calls.append(kw)
        return _resp(self.contents[min(len(self.calls), len(self.contents)) - 1])


@pytest.fixture
def make_llm(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_SUPER", "test/super")
    monkeypatch.setenv("CRUSOE_API_KEY", "test")
    monkeypatch.delenv("LLM_CACHE_DISABLE", raising=False)
    LLM._mode_by_model.clear()

    def make(*contents):
        rows = []
        llm = LLM(on_call=rows.append, cache=ResponseCache(tmp_path / "cache.jsonl"))
        fake = Fake(*contents)
        monkeypatch.setattr(llm.client.chat.completions, "create", fake)
        return llm, fake, rows
    return make


MSGS = build_messages("role", [], "board", "task")


def test_cache_hit_on_second_identical_call(make_llm):
    llm, fake, rows = make_llm('{"answer":"pong","number":42}')
    out1, m1 = llm.complete("super", MSGS, Ping, role="r", run_id="x")
    out2, m2 = llm.complete("super", MSGS, Ping, role="r", run_id="x")
    assert out1 == out2 == Ping(answer="pong", number=42)
    assert len(fake.calls) == 1
    assert not m1.from_cache and m1.usd > 0 and m1.mode == "json_schema"
    assert m2.from_cache and m2.usd == 0 and m2.latency_ms == 0
    assert [r["from_cache"] for r in rows] == [False, True]
    # persisted: a fresh cache on the same file also hits
    assert ResponseCache(llm.cache.path).get(m1.prompt_hash) is not None


def test_cache_key_changes_with_schema():
    assert cache_key("m", MSGS, Ping, 0.0) != cache_key("m", MSGS, Pong, 0.0)
    assert cache_key("m", MSGS, Ping, 0.0) == cache_key("m", MSGS, Ping, 0.0)


def test_validation_retry_then_success(make_llm):
    llm, fake, _ = make_llm("not json", '{"answer":"pong","number":42}')
    out, meta = llm.complete("super", MSGS, Ping, role="r", run_id=None)
    assert out == Ping(answer="pong", number=42)
    assert len(fake.calls) == 2
    assert "failed validation" in fake.calls[1]["messages"][-1]["content"]
    assert meta.tokens_in == 20 and not meta.fallback_used


def test_fallback_after_two_failures(make_llm):
    llm, fake, rows = make_llm("nope", "still nope")
    fb = Ping(answer="default", number=0)
    out, meta = llm.complete("super", MSGS, Ping, role="r", run_id=None, fallback=fb)
    assert out is fb and meta.fallback_used and len(fake.calls) == 2
    assert rows[-1]["kind"] == "llm_fallback"
    with pytest.raises(LLMValidationError):
        make_llm("x", "y")[0].complete("super", MSGS, Ping, role="r", run_id=None)


def test_build_messages_order_is_fixed():
    msgs = build_messages("ROLE", ["rule a"], "BOARD", "TASK")
    assert msgs[0] == {"role": "system", "content": "ROLE"}
    u = msgs[1]["content"]
    idx = [u.index(h) for h in ("## Playbook rules for your role", "## Board", "## Task")]
    assert idx == sorted(idx)
    assert "none" in build_messages("R", [], "B", "T")[1]["content"]


def test_think_stripping(make_llm):
    assert strip_think("<think>hmm {\"a\":1}</think>\n{\"b\":2}") == '{"b":2}'
    llm, _, _ = make_llm('<think>let me think</think>```json\n{"answer":"pong","number":42}\n```')
    out, _ = llm.complete("super", MSGS, Ping, role="r", run_id=None)
    assert out == Ping(answer="pong", number=42)


def test_model_not_configured(make_llm, monkeypatch):
    llm, fake, _ = make_llm("{}")
    monkeypatch.setenv("MODEL_ULTRA", "")
    with pytest.raises(ModelNotConfigured, match="MODEL_ULTRA is empty in .env"):
        llm.complete("ultra", MSGS, Ping, role="r", run_id=None)
    assert fake.calls == []


def test_response_format_fallback_is_remembered(make_llm):
    import openai

    llm, _, _ = make_llm()
    calls = []

    def create(**kw):
        calls.append(kw.get("response_format", {}).get("type"))
        if kw.get("response_format", {}).get("type") == "json_schema":
            err = openai.BadRequestError.__new__(openai.BadRequestError)
            Exception.__init__(err, "response_format not supported")
            err.status_code = 400
            raise err
        return _resp('{"answer":"pong","number":42}')

    llm.client.chat.completions.create = create
    llm.complete("super", MSGS, Ping, role="r", run_id=None)
    llm.complete("super", build_messages("role", [], "board", "other task"), Ping, role="r", run_id=None)
    assert calls == ["json_schema", "json_object", "json_object"]
