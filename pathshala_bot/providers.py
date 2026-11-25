"""AI providers: Anthropic (Claude), OpenAI or OpenRouter, chosen with LLM_PROVIDER / the admin panel.

Every AI step in the app goes through two operations, implemented for both:

- web_research(prompt, max_searches) -> Research   (web search + cited sources)
- structured(system, user, model_cls, ...) -> model_cls instance   (validated JSON output)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .config import settings

log = logging.getLogger(__name__)

PROVIDERS = ("anthropic", "openai", "openrouter")


class ContentRefused(RuntimeError):
    pass


@dataclass
class Research:
    notes: str
    sources: list[dict] = field(default_factory=list)


def _b64(data: bytes) -> str:
    import base64

    return base64.b64encode(data).decode()


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

    def structured(self, system: str, user: str, model_cls, max_tokens: int = 32000, effort: str | None = None,
                   images: list[bytes] | None = None):
        content = [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": _b64(i)}} for i in images or []]
        with self.client.beta.messages.stream(
            model=self.model, max_tokens=max_tokens, betas=[self.FALLBACK_BETA], fallbacks="default",
            output_config={"effort": effort or settings.generation_effort},
            system=system, messages=[{"role": "user", "content": content + [{"type": "text", "text": user}] if images else user}],
            output_format=model_cls,
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

    def structured(self, system: str, user: str, model_cls, max_tokens: int = 32000, effort: str | None = None,
                   images: list[bytes] | None = None):
        inp = user if not images else [{"role": "user", "content": [
            *({"type": "input_image", "image_url": f"data:image/png;base64,{_b64(i)}"} for i in images),
            {"type": "input_text", "text": user}]}]
        resp = self._create(
            self.client.responses.parse,
            model=self.model, instructions=system, input=inp, text_format=model_cls,
            max_output_tokens=max_tokens, reasoning={"effort": effort or settings.generation_effort},
        )
        if (r := self._refusal(resp)) is not None:
            raise ContentRefused(f"OpenAI declined this request: {r}")
        if resp.status == "incomplete":
            raise RuntimeError(f"output truncated: {resp.incomplete_details}")
        if resp.output_parsed is None:
            raise RuntimeError("no structured output returned")
        return resp.output_parsed


# --------------------------------------------------------------------------- OpenRouter

class OpenRouterProvider:
    """Any model on OpenRouter (one key for Claude, GPT, Gemini...): web plugin for research, JSON schema for structure."""

    name = "openrouter"
    API = "https://openrouter.ai/api/v1/chat/completions"
    spent = 0.0  # USD reported by OpenRouter in this process (shown in logs / CLI)

    def __init__(self):
        import httpx

        if not settings.openrouter_api_key:
            raise RuntimeError("could not resolve authentication method: no OpenRouter API key")
        self.http = httpx.Client(timeout=httpx.Timeout(1200, connect=30))

    @property
    def model(self) -> str:
        return settings.openrouter_text_model

    def _post(self, body: dict) -> dict:
        import time

        import httpx

        last = None
        for attempt in range(5):
            try:
                r = self.http.post(self.API, json=body, headers={"Authorization": f"Bearer {settings.openrouter_api_key}",
                                                                 "X-Title": settings.brand_name})
            except httpx.HTTPError as e:  # network hiccup / timeout
                last = e
                time.sleep(3 * (attempt + 1))
                continue
            if r.status_code in (401, 403):
                raise RuntimeError(f"OpenRouter rejected the API key ({r.status_code}): {r.text[:200]} (invalid x-api-key)")
            if r.status_code in (408, 429, 500, 502, 503, 504):
                last = RuntimeError(f"OpenRouter {r.status_code}: {r.text[:200]}")
                time.sleep(5 * (attempt + 1))
                continue
            data = r.json()
            if r.status_code >= 400 or "error" in data and not data.get("choices"):
                raise RuntimeError(f"OpenRouter error {r.status_code}: {str(data.get('error') or data)[:300]}")
            cost = float((data.get("usage") or {}).get("cost") or 0)
            OpenRouterProvider.spent += cost
            return data
        raise RuntimeError(f"OpenRouter unavailable after retries: {last}")

    @staticmethod
    def _message(data: dict) -> dict:
        ch = (data.get("choices") or [{}])[0]
        if ch.get("finish_reason") == "length":
            raise RuntimeError("output truncated (max_tokens)")
        return ch.get("message") or {}

    def web_research(self, prompt: str, max_searches: int) -> Research:
        data = self._post({"model": self.model, "messages": [{"role": "user", "content": prompt}], "max_tokens": 16000,
                           "plugins": [{"id": "web", "max_results": max(5, min(20, max_searches * 2))}],
                           "reasoning": {"effort": settings.research_effort}})
        msg = self._message(data)
        sources: dict[str, str] = {}
        for a in msg.get("annotations") or []:
            c = a.get("url_citation") or {}
            if c.get("url"):
                sources.setdefault(c["url"], c.get("title") or c["url"])
        content = msg.get("content") or ""
        if isinstance(content, list):
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        return _finish_research([content], sources)

    def structured(self, system: str, user: str, model_cls, max_tokens: int = 32000, effort: str | None = None,
                   images: list[bytes] | None = None):
        import json as _json
        import re

        from pydantic import ValidationError

        schema = model_cls.model_json_schema()
        text = user + "\n\nReply with one JSON object that follows the given schema."
        content = [*({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{_b64(i)}"}} for i in images),
                   {"type": "text", "text": text}] if images else text
        messages = [{"role": "system", "content": system}, {"role": "user", "content": content}]
        error = ""
        for attempt in range(3):
            msgs = messages + ([{"role": "user", "content": f"Your previous reply was not valid: {error[:800]}. Reply again with valid JSON only."}]
                               if error else [])
            data = self._post({"model": self.model, "messages": msgs, "max_tokens": max_tokens,
                               "reasoning": {"effort": effort or settings.generation_effort},
                               "response_format": {"type": "json_schema",
                                                   "json_schema": {"name": model_cls.__name__, "strict": False, "schema": schema}}})
            msg = self._message(data)
            if msg.get("refusal"):
                raise ContentRefused(f"OpenRouter model declined: {msg['refusal']}")
            text = msg.get("content") or ""
            if isinstance(text, list):
                text = "".join(p.get("text", "") for p in text if isinstance(p, dict))
            m = re.search(r"\{.*\}", text, re.S)
            try:
                return model_cls.model_validate(_json.loads(m.group(0) if m else text))
            except (ValueError, ValidationError) as e:
                error = str(e)
                log.warning("OpenRouter structured output invalid (try %d): %s", attempt + 1, error[:200])
        raise RuntimeError(f"no valid structured output from OpenRouter: {error[:300]}")


# --------------------------------------------------------------------------- selection

_cache: dict[str, object] = {}


def get():
    name = (settings.llm_provider or "anthropic").lower()
    if name not in PROVIDERS:
        raise ValueError(f"Unknown LLM_PROVIDER '{name}' (use 'anthropic', 'openai' or 'openrouter')")
    if name not in _cache:
        _cache[name] = {"openai": OpenAIProvider, "openrouter": OpenRouterProvider}.get(name, AnthropicProvider)()
    return _cache[name]


def reset() -> None:
    """Drop cached clients (after keys or the provider change in the admin panel)."""
    _cache.clear()


def describe() -> str:
    p = (settings.llm_provider or "anthropic").lower()
    return f"{p}:" + {"openai": settings.openai_model, "openrouter": settings.openrouter_text_model}.get(p, settings.claude_model)


# exact messages of the Anthropic / OpenAI SDKs for a missing or rejected key (not other services' 401s)
_KEY_HINTS = ("could not resolve authentication method", "invalid x-api-key", "incorrect api key provided",
              "authentication_error", "the api_key client option must be set")


def friendly_error(message: str) -> str:
    """Plain-language version of the provider errors people hit most (a missing or wrong API key)."""
    from .config import settings

    low = (message or "").lower()
    if any(h in low for h in _KEY_HINTS):
        name, env = {"openai": ("OpenAI", "OPENAI_API_KEY"), "openrouter": ("OpenRouter", "OPENROUTER_API_KEY")}.get(
            settings.llm_provider, ("Anthropic", "ANTHROPIC_API_KEY"))
        return (f"The AI provider ({name}) has no valid API key. Add it in the panel under Settings -> Keys "
                f"(or {env} in .env, then recreate the container).")
    return message
