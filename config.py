import os
from collections.abc import Callable, Mapping
from datetime import timedelta
from typing import Any, overload

from dotenv import load_dotenv

load_dotenv()

def _text(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)

    if value is None:
        return default

    return value.strip().lower() in {"1", "true", "yes", "on"}


@overload
def _number(
    name: str,
    default: str,
    cast: Callable[[str | int], int] = int,
) -> int | str: ...


@overload
def _number(
    name: str,
    default: int,
    cast: Callable[[str | int], int] = int,
) -> int: ...


def _number(
    name: str,
    default: str | int,
    cast: Callable[[str | int], int] = int,
) -> int | str:
    try:
        value = os.getenv(name)
        return cast(default if value is None else value)
    except (TypeError, ValueError):
        return default


class Config:
    SECRET_KEY = _text("SECRET_KEY") or None
    DEBUG = _flag("FLASK_DEBUG", False)

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = _text("SESSION_COOKIE_SAMESITE", "Lax")
    SESSION_COOKIE_SECURE = _flag("SESSION_COOKIE_SECURE", False)
    PERMANENT_SESSION_LIFETIME = timedelta(days=7)
    DB_HOST = os.getenv("DB_HOST", "localhost")
    DB_PORT = _number("DB_PORT", "3306")
    DB_USER = os.getenv("DB_USER")
    DB_PASSWORD = os.getenv("DB_PASSWORD")
    DB_NAME = os.getenv("DB_NAME")

    SERPAPI_API_KEY = _text("SERPAPI_API_KEY") or None
    SERPAPI_COUNTRY = _text("SERPAPI_COUNTRY", "in").lower()
    SERPAPI_LANGUAGE = _text("SERPAPI_LANGUAGE", "en").lower()

    AI_REPORT_ENABLED = _flag("AI_REPORT_ENABLED", True)
    ANTHROPIC_API_KEY = _text("ANTHROPIC_API_KEY") or None
    LLM_PROVIDER = _text("LLM_PROVIDER", "anthropic").lower()          # anthropic | openai_compat
    LLM_BASE_URL = _text("LLM_BASE_URL")
    LLM_API_KEY = _text("LLM_API_KEY")
    LLM_MODEL = _text("LLM_MODEL")
    ANTHROPIC_MODEL = _text("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")

    # AI Agent Playground
    SEARCH_PROVIDER = _text("SEARCH_PROVIDER", "serpapi").lower()      # serpapi | serper | zenserp | brave | searxng | google_cse
    SERPER_API_KEY = _text("SERPER_API_KEY")
    BRAVE_API_KEY = _text("BRAVE_API_KEY")
    ZENSERP_API_KEY = _text("ZENSERP_API_KEY")
    SEARXNG_URL = _text("SEARXNG_URL")
    GOOGLE_CSE_API_KEY = _text("GOOGLE_CSE_API_KEY")
    GOOGLE_CSE_ID = _text("GOOGLE_CSE_ID")
    PLAYGROUND_STORE = _text("PLAYGROUND_STORE", "mysql").lower()      # mysql | memory (dev/tests only)
    USER_AGENT = _text("USER_AGENT", "ResquoraResearchBot/1.0 (+set-a-contact-url-in-.env)")
    GEOCODER_URL = _text("GEOCODER_URL", "https://nominatim.openstreetmap.org/reverse")
    DEFAULT_CURRENCY = _text("DEFAULT_CURRENCY", "INR").upper()
    PAGES_PER_AGENT = _number("PAGES_PER_AGENT", 4)
    MAX_AGENTS_PER_RUN = _number("MAX_AGENTS_PER_RUN", 12)
    RUN_TIMEOUT_SECONDS = _number("RUN_TIMEOUT_SECONDS", 480)
    SEARCH_LIMIT_PER_MONTH = _number("SEARCH_LIMIT_PER_MONTH", 250)
    MAX_CONTENT_LENGTH = 64 * 1024

    HTTP_TIMEOUT_SECONDS = _number("HTTP_TIMEOUT_SECONDS", 15)
    CACHE_TTL_SECONDS = _number("CACHE_TTL_SECONDS", 900)
    MAX_RESULTS_PER_SEARCH = _number("MAX_RESULTS_PER_SEARCH", 40)


def configuration_problems(
    settings: Mapping[str, Any],
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    if not settings.get("SECRET_KEY") and not settings.get("DEBUG"):
        errors.append("SECRET_KEY is not set. Add a long random value to .env.")

    if not settings.get("DB_USER"):
        errors.append("DB_USER is not set in .env.")

    if not settings.get("DB_NAME"):
        errors.append("DB_NAME is not set in .env.")

    try:
        db_port = int(settings.get("DB_PORT") or 0)
    except (TypeError, ValueError):
        db_port = 0

    if not 1 <= db_port <= 65535:
        errors.append("DB_PORT must be a valid TCP port from 1 to 65535.")

    if settings.get("PLAYGROUND_STORE", "mysql") not in {"mysql", "memory"}:
        errors.append("PLAYGROUND_STORE must be mysql or memory.")

    try:
        search_limit = int(settings.get("SEARCH_LIMIT_PER_MONTH", 250))
    except (TypeError, ValueError):
        search_limit = 0

    if search_limit < 1:
        errors.append("SEARCH_LIMIT_PER_MONTH must be a positive integer.")

    secret = settings.get("SECRET_KEY") or ""

    if secret and len(secret) < 32:
        warnings.append("SECRET_KEY is shorter than 32 characters.")

    provider = settings.get("SEARCH_PROVIDER", "serpapi")
    key_name = {"serpapi": "SERPAPI_API_KEY", "serper": "SERPER_API_KEY", "brave": "BRAVE_API_KEY", "zenserp": "ZENSERP_API_KEY", "searxng": "SEARXNG_URL", "google_cse": "GOOGLE_CSE_API_KEY"}.get(provider)
    if key_name is None:
        errors.append("SEARCH_PROVIDER must be serpapi, serper, zenserp, brave, searxng or google_cse.")
    elif not settings.get(key_name):
        warnings.append(
            f"{key_name} is empty (SEARCH_PROVIDER={provider}). The playground will start, but it cannot "
            "research products until a key is added (there is no sample-data mode)."
        )

    if "set-a-contact-url" in (settings.get("USER_AGENT") or ""):
        warnings.append("USER_AGENT has no contact URL. Set one so website owners can reach you.")

    other_llm = settings.get("LLM_PROVIDER") == "openai_compat" and settings.get("LLM_BASE_URL") and settings.get("LLM_MODEL")
    if settings.get("AI_REPORT_ENABLED") and not settings.get("ANTHROPIC_API_KEY") and not other_llm:
        warnings.append(
            "ANTHROPIC_API_KEY is empty. Multilingual understanding, spoken summaries in other languages "
            "and free-form follow-up answers need it; English rule-based fallbacks are used instead."
        )

    return errors, warnings
