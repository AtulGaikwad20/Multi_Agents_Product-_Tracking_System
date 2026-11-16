"""Licensed search access (SerpApi: Google Shopping + Google Web).

There is deliberately no demo/sample mode. If no key is configured, searches
raise SearchUnavailable and the agents report that honestly.
"""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol, TypedDict, Unpack, cast
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

_JsonObject = dict[str, Any]
_CacheKey = tuple[tuple[str, str], ...]


class _Response(Protocol):
    status_code: int

    def json(self) -> object: ...


class _GetSession(Protocol):
    def get(
        self, url: str, *, params: Mapping[str, object] | None = None, timeout: float | None = None
    ) -> _Response: ...


class _RequestSession(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        params: Mapping[str, object] | None = None,
        json: _JsonObject | None = None,
        timeout: float | None = None,
    ) -> _Response: ...


class _SearchSettings(TypedDict, total=False):
    SERPAPI_COUNTRY: str
    SERPAPI_LANGUAGE: str
    HTTP_TIMEOUT_SECONDS: float
    CACHE_TTL_SECONDS: float
    SEARCH_PROVIDER: str
    SERPAPI_API_KEY: str | None
    SERPER_API_KEY: str | None
    BRAVE_API_KEY: str | None
    ZENSERP_API_KEY: str | None
    SEARXNG_URL: str | None
    GOOGLE_CSE_API_KEY: str | None
    GOOGLE_CSE_ID: str | None


class _CommonProviderOptions(TypedDict):
    country: str
    language: str
    timeout: float
    ttl: float


class _KeyedProviderOptions(TypedDict, total=False):
    country: str
    language: str
    timeout: float
    ttl: float
    session: _RequestSession | requests.Session | None
    concurrency: int
    retries: int


class SearchUnavailable(Exception):
    """The search provider is not configured or cannot be used right now."""

    def __init__(self, user_message: str) -> None:
        super().__init__(user_message)
        self.user_message = user_message


@dataclass
class ShoppingHit:
    title: str
    merchant: str
    price: float | None
    currency: str | None
    url: str | None
    rating: float | None
    review_count: int | None
    image: str | None
    delivery: str | None
    google_link: bool
    retrieved_at: str


@dataclass
class WebHit:
    title: str
    url: str
    snippet: str
    domain: str
    retrieved_at: str


def _url(value: object) -> str | None:
    if not value or not isinstance(value, str):
        return None
    parsed = urlparse(value.strip())
    return value.strip() if parsed.scheme in ("http", "https") and parsed.netloc else None


def _float(value: str | int | float | None) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _int(value: str | int | float | None) -> int | None:
    if value is None:
        return None
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def domain_of(url: str | None) -> str:
    host = urlparse(url or "").netloc.lower()
    return host[4:] if host.startswith("www.") else host


class SerpApiSearch:
    endpoint: str = "https://serpapi.com/search.json"
    name: str = "serpapi"

    def __init__(
        self,
        api_key: str | None,
        country: str = "in",
        language: str = "en",
        timeout: float = 15,
        ttl: float = 900,
        session: _GetSession | requests.Session | None = None,
    ) -> None:
        self.api_key: str | None = api_key
        self.country: str = country
        self.language: str = language
        self.timeout: float = timeout
        self.ttl: float = ttl
        self.session: _GetSession | requests.Session = session or requests.Session()
        self._cache: dict[_CacheKey, tuple[float, _JsonObject]] = {}
        self._lock = threading.Lock()
        self.calls: int = 0

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def _call(self, params: Mapping[str, str | int]) -> tuple[_JsonObject, bool]:
        if not self.api_key:
            raise SearchUnavailable(
                "No search provider key is configured (SERPAPI_API_KEY), so live research is not possible."
            )
        key: _CacheKey = tuple(sorted((k, str(v)) for k, v in params.items()))
        now = time.monotonic()
        with self._lock:
            hit = self._cache.get(key)
            if hit and hit[0] > now:
                return hit[1], True
        full: dict[str, str | int] = {
            **params,
            "api_key": self.api_key,
            "gl": self.country,
            "hl": self.language,
        }
        try:
            response = self.session.get(self.endpoint, params=full, timeout=self.timeout)
        except requests.RequestException as error:
            logger.warning("search request failed: %s", type(error).__name__)
            raise SearchUnavailable("The search service could not be reached.")
        if response.status_code in (401, 403):
            raise SearchUnavailable("The search service rejected the API key.")
        if response.status_code == 429:
            raise SearchUnavailable("The search service rate limit was reached.")
        if response.status_code != 200:
            raise SearchUnavailable(f"The search service returned HTTP {response.status_code}.")
        try:
            data = cast(_JsonObject, response.json())
        except ValueError:
            raise SearchUnavailable("The search service returned an unreadable response.")
        self.calls += 1
        error_text = str(data.get("error") or "")
        if error_text and "hasn't returned any results" not in error_text:
            logger.warning("search provider error: %s", error_text[:200])
            raise SearchUnavailable("The search service reported a problem with this query.")
        with self._lock:
            self._cache[key] = (now + self.ttl, data)
        return data, False

    @staticmethod
    def _stamp() -> str:
        from .models import now_iso
        return now_iso()

    def shopping(
        self, query: str, location: str | None = None, limit: int = 40
    ) -> list[ShoppingHit]:
        params = {"engine": "google_shopping", "q": query}
        if location:
            params["location"] = location
        data, _ = self._call(params)
        stamp = self._stamp()
        hits: list[ShoppingHit] = []
        for item in data.get("shopping_results") or []:
            title = (item.get("title") or "").strip()
            if not title:
                continue
            price = _float(item.get("extracted_price"))
            direct = _url(item.get("link"))
            google = _url(item.get("product_link"))
            url = direct or google
            hits.append(ShoppingHit(
                title=title,
                merchant=(item.get("source") or "").strip(),
                price=price,
                currency=None,  # Google Shopping gives a display string; currency inferred by caller/country
                url=url,
                rating=_float(item.get("rating")),
                review_count=_int(item.get("reviews")),
                image=_url(item.get("thumbnail")),
                delivery=(item.get("delivery") or None),
                google_link=bool(url and domain_of(url).endswith("google.com")),
                retrieved_at=stamp,
            ))
        return hits[:limit]

    def web(
        self, query: str, num: int = 10, location: str | None = None
    ) -> list[WebHit]:
        params = {"engine": "google", "q": query, "num": num}
        if location:
            params["location"] = location
        data, _ = self._call(params)
        stamp = self._stamp()
        hits: list[WebHit] = []
        for item in data.get("organic_results") or []:
            url = _url(item.get("link"))
            if not url:
                continue
            hits.append(WebHit(
                title=(item.get("title") or "").strip(),
                url=url,
                snippet=(item.get("snippet") or "").strip(),
                domain=domain_of(url),
                retrieved_at=stamp,
            ))
        return hits


def _price(value: str | int | float | None) -> float | None:
    """'₹1,299.00' / 'Rs. 499' / 1299 -> float, else None."""
    if isinstance(value, (int, float)):
        return _float(value)
    import re
    m = re.search(r"\d[\d,]*(?:\.\d+)?", str(value or ""))
    return _float(m.group(0).replace(",", "")) if m else None


class _KeyedProvider:
    """Shared plumbing: caching, error mapping, one place that never logs the key."""
    name: str = ""
    key_name: str = ""
    label: str = ""

    def __init__(
        self,
        api_key: str | None,
        country: str = "in",
        language: str = "en",
        timeout: float = 15,
        ttl: float = 900,
        session: _RequestSession | requests.Session | None = None,
        concurrency: int = 1,
        retries: int = 2,
    ) -> None:
        self.api_key: str | None = api_key
        self.country: str = country
        self.language: str = language
        self.retries: int = retries
        self.backoff: float = 1.5
        self.timeout, self.ttl = timeout, ttl
        self.session: _RequestSession | requests.Session = session or requests.Session()
        self._cache: dict[tuple[str, str, _CacheKey], tuple[float, _JsonObject]] = {}
        self._lock, self.calls = threading.Lock(), 0
        # Free plans often reject parallel calls (HTTP 502/429): send provider calls one at a time.
        self._gate = threading.Semaphore(max(1, int(concurrency)))

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def _request(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str] | None = None,
        params: Mapping[str, object] | None = None,
        body: _JsonObject | None = None,
    ) -> _JsonObject:
        if not self.api_key:
            raise SearchUnavailable(f"No search provider key is configured ({self.key_name}), so live research is not possible.")
        key: tuple[str, str, _CacheKey] = (
            method,
            url,
            tuple(sorted((k, str(v)) for k, v in (params or body or {}).items())),
        )
        now = time.monotonic()
        with self._lock:
            hit = self._cache.get(key)
            if hit and hit[0] > now:
                return hit[1]
        response: _Response | None = None
        for attempt in range(self.retries + 1):
            try:
                with self._gate:
                    response = self.session.request(method, url, headers=headers, params=params, json=body,
                                                    timeout=max(self.timeout, 30))
            except requests.RequestException as error:
                logger.warning("%s request failed (attempt %d): %s", self.name, attempt + 1, type(error).__name__)
                response = None
                if attempt == self.retries:
                    raise SearchUnavailable("The search service could not be reached (timed out or no connection).")
            else:
                if response.status_code not in (500, 502, 503, 504) or attempt == self.retries:
                    break
                logger.warning("%s returned HTTP %s (attempt %d), retrying", self.name, response.status_code, attempt + 1)
            time.sleep(self.backoff * (attempt + 1))
        if response is None:
            raise SearchUnavailable("The search service could not be reached (timed out or no connection).")
        if response.status_code in (401, 403):
            raise SearchUnavailable("The search service rejected the API key.")
        if response.status_code == 429:
            raise SearchUnavailable("The search service rate limit or quota was reached.")
        if response.status_code != 200:
            raise SearchUnavailable(f"The search service returned HTTP {response.status_code} (a temporary problem on its side; try again in a minute).")
        try:
            data = cast(_JsonObject, response.json())
        except ValueError:
            raise SearchUnavailable("The search service returned an unreadable response.")
        self.calls += 1
        with self._lock:
            self._cache[key] = (now + self.ttl, data)
        return data

    @staticmethod
    def _stamp() -> str:
        from .models import now_iso
        return now_iso()


class SerperSearch(_KeyedProvider):
    """serper.dev: Google web + Google Shopping results over a simple JSON API."""
    name, key_name = "serper", "SERPER_API_KEY"

    def _post(self, path: str, payload: _JsonObject) -> _JsonObject:
        payload = {**payload, "gl": self.country, "hl": self.language}
        return self._request("POST", f"https://google.serper.dev/{path}",
                             headers={"X-API-KEY": self.api_key, "Content-Type": "application/json"}, body=payload)

    def shopping(
        self, query: str, location: str | None = None, limit: int = 40
    ) -> list[ShoppingHit]:
        body = {"q": query}
        if location:
            body["location"] = location
        data = self._post("shopping", body)
        stamp, hits = self._stamp(), []
        for item in data.get("shopping") or []:
            title = (item.get("title") or "").strip()
            if not title:
                continue
            url = _url(item.get("link"))
            hits.append(ShoppingHit(
                title=title, merchant=(item.get("source") or "").strip(), price=_price(item.get("price")),
                currency=None, url=url, rating=_float(item.get("rating")), review_count=_int(item.get("ratingCount")),
                image=_url(item.get("imageUrl")), delivery=item.get("delivery") or None,
                google_link=bool(url and domain_of(url).endswith("google.com")), retrieved_at=stamp))
        return hits[:limit]

    def web(
        self, query: str, num: int = 10, location: str | None = None
    ) -> list[WebHit]:
        body = {"q": query, "num": num}
        if location:
            body["location"] = location
        data = self._post("search", body)
        stamp, hits = self._stamp(), []
        for item in data.get("organic") or []:
            url = _url(item.get("link"))
            if url:
                hits.append(WebHit((item.get("title") or "").strip(), url, (item.get("snippet") or "").strip(),
                                   domain_of(url), stamp))
        return hits


class BraveSearch(_KeyedProvider):
    """Brave Search API: web results only. There is no shopping feed, so agents rely on site-scoped
    web search plus reading each product page's own data (still fully verifiable)."""
    name, key_name = "brave", "BRAVE_API_KEY"

    def shopping(
        self, query: str, location: str | None = None, limit: int = 40
    ) -> list[ShoppingHit]:
        return []

    def web(
        self, query: str, num: int = 10, location: str | None = None
    ) -> list[WebHit]:
        data = self._request("GET", "https://api.search.brave.com/res/v1/web/search",
                             headers={"X-Subscription-Token": self.api_key, "Accept": "application/json"},
                             params={"q": query, "count": min(num, 20), "country": self.country.upper(),
                                     "search_lang": self.language})
        stamp, hits = self._stamp(), []
        for item in (data.get("web") or {}).get("results") or []:
            url = _url(item.get("url"))
            if url:
                hits.append(WebHit((item.get("title") or "").strip(), url, (item.get("description") or "").strip(),
                                   domain_of(url), stamp))
        return hits


class SearxngSearch(_KeyedProvider):
    """Self-hosted SearXNG: free, no API key. Web results only (no shopping feed).
    The instance must have `json` enabled under `search.formats` in its settings.yml."""
    name, key_name = "searxng", "SEARXNG_URL"

    def __init__(self, base_url: str | None, **kw: Unpack[_KeyedProviderOptions]) -> None:
        super().__init__(base_url, **kw)
        self.base_url: str = (base_url or "").rstrip("/")

    def shopping(
        self, query: str, location: str | None = None, limit: int = 40
    ) -> list[ShoppingHit]:
        return []

    def web(
        self, query: str, num: int = 10, location: str | None = None
    ) -> list[WebHit]:
        data = self._request("GET", f"{self.base_url}/search",
                             params={"q": query, "format": "json", "language": f"{self.language}-{self.country.upper()}"})
        stamp, hits = self._stamp(), []
        for item in (data.get("results") or [])[:num]:
            url = _url(item.get("url"))
            if url:
                hits.append(WebHit((item.get("title") or "").strip(), url, (item.get("content") or "").strip(),
                                   domain_of(url), stamp))
        return hits


class GoogleCseSearch(_KeyedProvider):
    """Google Programmable Search JSON API: web results only; free tier is limited per day."""
    name, key_name = "google_cse", "GOOGLE_CSE_API_KEY"

    def __init__(self, api_key: str | None, cx: str | None, **kw: Unpack[_KeyedProviderOptions]) -> None:
        super().__init__(api_key, **kw)
        self.cx: str | None = cx

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.cx)

    def shopping(
        self, query: str, location: str | None = None, limit: int = 40
    ) -> list[ShoppingHit]:
        return []

    def web(
        self, query: str, num: int = 10, location: str | None = None
    ) -> list[WebHit]:
        if not self.cx:
            raise SearchUnavailable("No search provider key is configured (GOOGLE_CSE_ID), so live research is not possible.")
        data = self._request("GET", "https://www.googleapis.com/customsearch/v1",
                             params={"key": self.api_key, "cx": self.cx, "q": query, "num": min(num, 10),
                                     "gl": self.country, "hl": self.language})
        stamp, hits = self._stamp(), []
        for item in data.get("items") or []:
            url = _url(item.get("link"))
            if url:
                hits.append(WebHit((item.get("title") or "").strip(), url, (item.get("snippet") or "").strip(),
                                   domain_of(url), stamp))
        return hits


class ZenserpSearch(_KeyedProvider):
    """Zenserp: Google web results and (tbm=shop) Google Shopping. Field names are read tolerantly because
    this adapter was written from the documentation, not from a live response."""
    name, key_name = "zenserp", "ZENSERP_API_KEY"
    endpoint: str = "https://app.zenserp.com/api/v2/search"

    def _get(self, params: dict[str, str | int]) -> _JsonObject:
        return self._request("GET", self.endpoint, headers={"apikey": self.api_key},
                             params={**params, "gl": self.country, "hl": self.language})

    def shopping(
        self, query: str, location: str | None = None, limit: int = 40
    ) -> list[ShoppingHit]:
        params = {"q": query, "tbm": "shop"}
        if location:
            params["location"] = location
        data = self._get(params)
        items = data.get("shopping_results") or data.get("shopping") or data.get("organic") or []
        stamp = self._stamp()
        hits: list[ShoppingHit] = []
        for item in items:
            title = (item.get("title") or item.get("name") or "").strip()
            url = _url(item.get("link") or item.get("url"))
            price = _price(item.get("extracted_price") or item.get("price"))
            if not title or (price is None and not item.get("merchant") and not item.get("source")):
                continue          # ordinary web result without any shopping data
            merchant = item.get("source") or item.get("merchant") or item.get("seller") or ""
            hits.append(ShoppingHit(
                title=title, merchant=str(merchant).strip(), price=price, currency=None, url=url,
                rating=_float(item.get("rating")), review_count=_int(item.get("reviews") or item.get("rating_count")),
                image=_url(item.get("thumbnail") or item.get("image")), delivery=item.get("delivery") or None,
                google_link=bool(url and domain_of(url).endswith("google.com")), retrieved_at=stamp))
        return hits[:limit]

    def web(
        self, query: str, num: int = 10, location: str | None = None
    ) -> list[WebHit]:
        params = {"q": query, "num": num}
        if location:
            params["location"] = location
        data = self._get(params)
        stamp, hits = self._stamp(), []
        for item in data.get("organic") or []:
            url = _url(item.get("url") or item.get("link"))
            if url:
                hits.append(WebHit((item.get("title") or "").strip(), url,
                                   (item.get("description") or item.get("snippet") or "").strip(), domain_of(url), stamp))
        return hits


def build_search(
    settings: Mapping[str, object],
) -> SerpApiSearch | SerperSearch | BraveSearch | SearxngSearch | GoogleCseSearch | ZenserpSearch:
    typed_settings = cast(_SearchSettings, settings)
    common: _CommonProviderOptions = {
        "country": typed_settings.get("SERPAPI_COUNTRY", "in"),
        "language": typed_settings.get("SERPAPI_LANGUAGE", "en"),
        "timeout": typed_settings.get("HTTP_TIMEOUT_SECONDS", 15),
        "ttl": typed_settings.get("CACHE_TTL_SECONDS", 900),
    }
    choice = (typed_settings.get("SEARCH_PROVIDER") or "serpapi").lower()
    if choice == "serper":
        return SerperSearch(typed_settings.get("SERPER_API_KEY"), **common)
    if choice == "brave":
        return BraveSearch(typed_settings.get("BRAVE_API_KEY"), **common)
    if choice == "zenserp":
        return ZenserpSearch(typed_settings.get("ZENSERP_API_KEY"), **common)
    if choice == "searxng":
        return SearxngSearch(typed_settings.get("SEARXNG_URL"), **common)
    if choice == "google_cse":
        return GoogleCseSearch(typed_settings.get("GOOGLE_CSE_API_KEY"), typed_settings.get("GOOGLE_CSE_ID"), **common)
    return SerpApiSearch(typed_settings.get("SERPAPI_API_KEY"), **common)
