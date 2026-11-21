"""AI providers: Anthropic (Claude) or OpenAI, chosen with LLM_PROVIDER / the admin panel.

Every AI step in the app goes through two operations, implemented for both:

- web_research(prompt, max_searches) -> Research   (web search + cited sources)
- structured(system, user, model_cls, ...) -> model_cls instance   (validated JSON output)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .config import settings

log = logging.getLogger(__name__)

PROVIDERS = ("anthropic", "openai")


class ContentRefused(RuntimeError):
    pass


@dataclass
class Research:
    notes: str
    sources: list[dict] = field(default_factory=list)


def _finish_research(text_parts: list[str], sources: dict[str, str]) -> Research:
    notes = "".join(text_parts).strip()
    if not notes:
        raise RuntimeError("Research step returned no text")
    log.info("research done: %d chars, %d sources", len(notes), len(sources))
    return Research(notes=notes, sources=[{"title": t, "url": u} for u, t in sources.items()])


# --------------------------------------------------------------------------- Anthropic

class AnthropicProvider:
    name = "anthropic"
    # Server-side fallback: if a safety classifier declines (false positives happen, e.g. on
    # law / medicine topics) the API re-runs the request on a recommended fallback model.
    FALLBACK_BETA = "server-side-fallback-2026-07-01"

    def __init__(self):
        import anthropic

        self.client = anthropic.Anthropic(max_retries=4)

    @property
    def model(self) -> str:
        return settings.claude_model

    @staticmethod
    def _check_refusal(resp) -> None:
        if resp.stop_reason == "refusal":
            d = getattr(resp, "stop_details", None)
            raise ContentRefused(f"Claude declined this request: {getattr(d, 'category', None)} {getattr(d, 'explanation', '')}")

    def web_research(self, prompt: str, max_searches: int) -> Research:
        tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": max_searches}]
        messages: list[dict] = [{"role": "user", "content": prompt}]
        sources: dict[str, str] = {}
        text_parts: list[str] = []
        for _ in range(6):  # pause_turn continuations
            with self.client.beta.messages.stream(
                model=self.model, max_tokens=32000, betas=[self.FALLBACK_BETA], fallbacks="default",
                output_config={"effort": settings.research_effort}, tools=tools, messages=messages,
            ) as stream:
                resp = stream.get_final_message()
            self._check_refusal(resp)
            for block in resp.content:
                if block.type == "text":
                    text_parts.append(block.text)
                    for c in getattr(block, "citations", None) or []:
                        if getattr(c, "url", None):
                            sources.setdefault(c.url, getattr(c, "title", "") or c.url)
                elif block.type == "web_search_tool_result" and isinstance(block.content, list):  # errors are an object
                    for r in block.content:
                        if getattr(r, "url", None):
                            sources.setdefault(r.url, getattr(r, "title", "") or r.url)
            if resp.stop_reason == "pause_turn":
                messages.append({"role": "assistant", "content": resp.content})
                continue
            break
        return _finish_research(text_parts, sources)

    def structured(self, system: str, user: str, model_cls, max_tokens: int = 32000, effort: str | None = None):
        with self.client.beta.messages.stream(
            model=self.model, max_tokens=max_tokens, betas=[self.FALLBACK_BETA], fallbacks="default",
            output_config={"effort": effort or settings.generation_effort},
            system=system, messages=[{"role": "user", "content": user}], output_format=model_cls,
        ) as stream:
            resp = stream.get_final_message()
        self._check_refusal(resp)
        if resp.stop_reason == "max_tokens":
            raise RuntimeError("output truncated (max_tokens)")
        if resp.parsed_output is None:
            raise RuntimeError("no structured output returned")
        return resp.parsed_output


# --------------------------------------------------------------------------- OpenAI

class OpenAIProvider:
    name = "openai"

    def __init__(self):
        import openai

        self.openai = openai
        self.client = openai.OpenAI(max_retries=4, timeout=1800)

    @property
    def model(self) -> str:
        return settings.openai_model

    def _create(self, method, **kw):
        """Call with reasoning effort; models without reasoning support get a retry without it."""
        try:
            return method(**kw)
        except self.openai.BadRequestError as e:
            if "reasoning" in str(e).lower() and "reasoning" in kw:
                log.warning("model %s rejected reasoning settings; retrying without them", self.model)
                kw.pop("reasoning")
                return method(**kw)
            raise

    @staticmethod
    def _refusal(resp) -> str | None:
        for item in resp.output or []:
            for c in getattr(item, "content", None) or []:
                if getattr(c, "type", "") == "refusal":
                    return getattr(c, "refusal", "") or "refused"
        return None

    def web_research(self, prompt: str, max_searches: int) -> Research:
        resp = self._create(
            self.client.responses.create,
            model=self.model, input=prompt, tools=[{"type": "web_search"}], max_tool_calls=max_searches,
            include=["web_search_call.action.sources"], max_output_tokens=32000,
            reasoning={"effort": settings.research_effort},
        )
        if (r := self._refusal(resp)) is not None:
            raise ContentRefused(f"OpenAI declined this request: {r}")
        sources: dict[str, str] = {}
        for item in resp.output or []:
            if item.type == "message":
                for c in item.content or []:
                    for a in getattr(c, "annotations", None) or []:
                        if getattr(a, "type", "") == "url_citation" and a.url:
                            sources.setdefault(a.url, a.title or a.url)
            elif item.type == "web_search_call":
                for s in getattr(getattr(item, "action", None), "sources", None) or []:
                    if getattr(s, "url", None):
                        sources.setdefault(s.url, s.url)
        if resp.status == "incomplete" and not resp.output_text:
            raise RuntimeError(f"OpenAI research incomplete: {resp.incomplete_details}")
        return _finish_research([resp.output_text or ""], sources)

    def structured(self, system: str, user: str, model_cls, max_tokens: int = 32000, effort: str | None = None):
        resp = self._create(
            self.client.responses.parse,
            model=self.model, instructions=system, input=user, text_format=model_cls,
            max_output_tokens=max_tokens, reasoning={"effort": effort or settings.generation_effort},
        )
        if (r := self._refusal(resp)) is not None:
            raise ContentRefused(f"OpenAI declined this request: {r}")
        if resp.status == "incomplete":
            raise RuntimeError(f"output truncated: {resp.incomplete_details}")
        if resp.output_parsed is None:
            raise RuntimeError("no structured output returned")
        return resp.output_parsed


# --------------------------------------------------------------------------- selection

_cache: dict[str, object] = {}


def get():
    name = (settings.llm_provider or "anthropic").lower()
    if name not in PROVIDERS:
        raise ValueError(f"Unknown LLM_PROVIDER '{name}' (use 'anthropic' or 'openai')")
    if name not in _cache:
        _cache[name] = OpenAIProvider() if name == "openai" else AnthropicProvider()
    return _cache[name]


def reset() -> None:
    """Drop cached clients (after keys or the provider change in the admin panel)."""
    _cache.clear()


def describe() -> str:
    p = (settings.llm_provider or "anthropic").lower()
    return f"{p}:{settings.openai_model if p == 'openai' else settings.claude_model}"
