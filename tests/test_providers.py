"""Both AI providers, with fake API clients (no network)."""

from __future__ import annotations

from types import SimpleNamespace as NS

import pytest
from pydantic import BaseModel

from pathshala_bot import providers
from pathshala_bot.config import settings


class Out(BaseModel):
    answer: str


@pytest.fixture
def use(monkeypatch):
    def _use(name):
        monkeypatch.setattr(settings.__class__, "llm_provider", name, raising=False)
        object.__setattr__(settings, "llm_provider", name)
        providers.reset()
    yield _use
    object.__setattr__(settings, "llm_provider", "anthropic")
    providers.reset()


def _openai(monkeypatch, create=None, parse=None):
    p = providers.OpenAIProvider.__new__(providers.OpenAIProvider)
    import openai

    p.openai = openai
    p.client = NS(responses=NS(create=create, parse=parse))
    return p


def test_selection(use, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    use("openai")
    assert isinstance(providers.get(), providers.OpenAIProvider)
    use("anthropic")
    assert isinstance(providers.get(), providers.AnthropicProvider)
    use("gemini")
    with pytest.raises(ValueError):
        providers.get()


def test_openai_web_research_collects_citations_and_sources(monkeypatch):
    seen = {}

    def create(**kw):
        seen.update(kw)
        msg = NS(type="message", content=[NS(type="output_text", text="notes",
                 annotations=[NS(type="url_citation", url="https://pib.gov.in/a", title="PIB")])])
        search = NS(type="web_search_call", action=NS(sources=[NS(url="https://example.org/b")]))
        return NS(output=[search, msg], output_text="STORY: x", status="completed", incomplete_details=None)

    r = _openai(monkeypatch, create=create).web_research("find news", 7)
    assert r.notes == "STORY: x"
    assert {s["url"] for s in r.sources} == {"https://pib.gov.in/a", "https://example.org/b"}
    assert seen["tools"] == [{"type": "web_search"}] and seen["max_tool_calls"] == 7
    assert "web_search_call.action.sources" in seen["include"]


def test_openai_structured_and_refusal(monkeypatch):
    ok = _openai(monkeypatch, parse=lambda **kw: NS(output=[], status="completed", output_parsed=Out(answer="42"), incomplete_details=None))
    assert ok.structured("sys", "user", Out).answer == "42"
    refused = NS(output=[NS(type="message", content=[NS(type="refusal", refusal="no")])], status="completed", output_parsed=None)
    with pytest.raises(providers.ContentRefused):
        _openai(monkeypatch, parse=lambda **kw: refused).structured("s", "u", Out)
    trunc = NS(output=[], status="incomplete", output_parsed=None, incomplete_details={"reason": "max_output_tokens"})
    with pytest.raises(RuntimeError, match="truncated"):
        _openai(monkeypatch, parse=lambda **kw: trunc).structured("s", "u", Out)


def test_openai_retries_without_reasoning(monkeypatch):
    import httpx
    import openai

    calls = []

    def parse(**kw):
        calls.append("reasoning" in kw)
        if "reasoning" in kw:
            req = httpx.Request("POST", "https://api.openai.com/v1/responses")
            raise openai.BadRequestError("Unsupported parameter: 'reasoning.effort'", response=httpx.Response(400, request=req), body=None)
        return NS(output=[], status="completed", output_parsed=Out(answer="ok"), incomplete_details=None)

    assert _openai(monkeypatch, parse=parse).structured("s", "u", Out).answer == "ok"
    assert calls == [True, False]


def test_anthropic_research_handles_pause_turn():
    p = providers.AnthropicProvider.__new__(providers.AnthropicProvider)
    turns = [
        NS(stop_reason="pause_turn", content=[NS(type="web_search_tool_result", content=[NS(url="https://a.gov.in", title="A")])]),
        NS(stop_reason="end_turn", content=[NS(type="text", text="final notes", citations=[NS(url="https://b.org", title="B")])]),
    ]

    class Stream:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get_final_message(self):
            return turns.pop(0)

    p.client = NS(beta=NS(messages=NS(stream=lambda **kw: Stream())))
    r = p.web_research("q", 5)
    assert r.notes == "final notes" and {s["url"] for s in r.sources} == {"https://a.gov.in", "https://b.org"}


def test_pipeline_uses_selected_provider(monkeypatch):
    from pathshala_bot import llm

    called = {}

    class Fake:
        def web_research(self, prompt, max_searches):
            called["research"] = max_searches
            return providers.Research(notes="n", sources=[])

        def structured(self, system, user, model_cls, max_tokens=0, effort=None):
            called["structured"] = model_cls.__name__
            return model_cls(answer="x")

    monkeypatch.setattr(providers, "get", lambda: Fake())
    assert llm.web_research("p", 3).notes == "n" and called["research"] == 3
    assert llm.structured("s", "u", Out).answer == "x" and called["structured"] == "Out"


def test_output_models_are_openai_strict_compatible():
    import json

    from openai.lib._pydantic import to_strict_json_schema

    from pathshala_bot.akhbar import AkhbarEdition
    from pathshala_bot.llm import MnemonicReview
    from pathshala_bot.models import ContentPackage

    def objects(s):
        if isinstance(s, dict):
            if "properties" in s:
                yield s
            for v in s.values():
                yield from objects(v)
        elif isinstance(s, list):
            for v in s:
                yield from objects(v)

    for model in (ContentPackage, AkhbarEdition, MnemonicReview):
        schema = to_strict_json_schema(model)
        assert '"default"' not in json.dumps(schema), model.__name__
        for obj in objects(schema):
            assert set(obj["required"]) == set(obj["properties"]) and obj["additionalProperties"] is False
