"""Thin Claude wrapper. Every call degrades to None so callers can fall back
to deterministic behaviour; the UI is told when the LLM is unavailable."""
from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping, Sequence
from typing import Protocol, TypeGuard, cast

logger = logging.getLogger(__name__)


class _TextBlock(Protocol):
    type: str
    text: str


class _AnthropicResponse(Protocol):
    content: Sequence[_TextBlock]


class _AnthropicMessages(Protocol):
    def create(
        self,
        *,
        model: str,
        max_tokens: int,
        system: str,
        messages: list[dict[str, str]],
    ) -> _AnthropicResponse: ...


class _AnthropicClient(Protocol):
    messages: _AnthropicMessages


class _HttpResponse(Protocol):
    status_code: int

    def json(self) -> object: ...


class _HttpSession(Protocol):
    def post(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout: float,
        json: Mapping[str, object],
    ) -> _HttpResponse: ...


def _is_string_key_dict(value: object) -> TypeGuard[dict[str, object]]:
    if not isinstance(value, dict):
        return False
    entries = cast(dict[object, object], value)
    return all(isinstance(key, str) for key in entries)


def _is_object_list(value: object) -> TypeGuard[list[object]]:
    return isinstance(value, list)


class Llm:
    def __init__(
        self,
        api_key: str | None = None,
        model: str = "claude-haiku-4-5-20251001",
        timeout: float = 25,
        client: object | None = None,
    ) -> None:
        self.model = model
        self._client: object | None = client
        if client is None and api_key:
            try:
                import anthropic
                self._client = cast(object, anthropic.Anthropic(api_key=api_key, timeout=timeout))
            except ImportError:
                logger.warning("anthropic package missing; LLM features are off")

    @property
    def available(self) -> bool:
        return self._client is not None

    def text(self, system: str, user: str, max_tokens: int = 700) -> str | None:
        if self._client is None:
            return None
        try:
            client = cast(_AnthropicClient, self._client)
            response = client.messages.create(
                model=self.model, max_tokens=max_tokens, system=system,
                messages=[{"role": "user", "content": user}],
            )
        except Exception as error:  # network, auth, rate limit...
            logger.warning("LLM call failed: %s", type(error).__name__)
            return None
        parts = [block.text for block in response.content if block.type == "text"]
        return " ".join(parts).strip() or None

    def json(self, system: str, user: str, max_tokens: int = 900) -> dict[str, object] | None:
        raw = self.text(system + "\nReturn ONLY one JSON object, no prose, no code fences.", user, max_tokens)
        if not raw:
            return None
        raw = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.M).strip()
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end < start:
            return None
        try:
            data: object = json.loads(raw[start:end + 1])
        except ValueError:
            return None
        if not _is_string_key_dict(data):
            return None
        return data


class OpenAICompatLlm(Llm):
    """Any service that speaks the OpenAI chat-completions format: Google Gemini (OpenAI-compatible endpoint),
    Groq, OpenRouter, or a local Ollama. Free tiers and limits are set by those providers."""

    def __init__(
        self,
        base_url: str | None,
        api_key: str | None = None,
        model: str = "",
        timeout: float = 30,
        session: object | None = None,
    ) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.api_key, self.model, self.timeout = api_key, model, timeout
        self._session = session
        self._client = True if (self.base_url and model) else None     # `available` keys off this

    def text(self, system: str, user: str, max_tokens: int = 700) -> str | None:
        if self._client is None:
            return None
        import requests
        session = cast(_HttpSession, self._session if self._session is not None else requests)
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            response = session.post(f"{self.base_url}/chat/completions", headers=headers, timeout=self.timeout, json={
                "model": self.model, "max_tokens": max_tokens, "temperature": 0.2,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]})
            if response.status_code != 200:
                logger.warning("LLM HTTP %s", response.status_code)
                return None
            payload = response.json()
            if not _is_string_key_dict(payload):
                return None
            choices = payload.get("choices")
            if not _is_object_list(choices) or not choices or not _is_string_key_dict(choices[0]):
                return None
            message = choices[0].get("message")
            if not _is_string_key_dict(message):
                return None
            content = message.get("content")
            if not isinstance(content, str):
                return None
            return content.strip() or None
        except Exception as error:      # network, bad JSON, unexpected shape
            logger.warning("LLM call failed: %s", type(error).__name__)
            return None


def _string_setting(settings: Mapping[str, object], key: str, default: str | None = None) -> str | None:
    value = settings.get(key)
    return value if isinstance(value, str) else default


def _timeout_setting(settings: Mapping[str, object], key: str, default: float) -> float:
    value = settings.get(key)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return default


def build_llm(settings: Mapping[str, object]) -> Llm:
    if not settings.get("AI_REPORT_ENABLED", True):
        return Llm()
    provider = _string_setting(settings, "LLM_PROVIDER", "anthropic") or "anthropic"
    if provider.lower() == "openai_compat":
        return OpenAICompatLlm(
            _string_setting(settings, "LLM_BASE_URL"),
            _string_setting(settings, "LLM_API_KEY"),
            _string_setting(settings, "LLM_MODEL", "") or "",
            timeout=_timeout_setting(settings, "HTTP_TIMEOUT_SECONDS", 15) + 20,
        )
    api_key = _string_setting(settings, "ANTHROPIC_API_KEY")
    if not api_key:
        return Llm()
    return Llm(
        api_key,
        _string_setting(settings, "ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
        or "claude-haiku-4-5-20251001",
        timeout=_timeout_setting(settings, "HTTP_TIMEOUT_SECONDS", 15) + 15,
    )
