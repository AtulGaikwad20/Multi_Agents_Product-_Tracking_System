"""Shared data models for the AI Agent Playground.

Every product fact carries an Evidence record so the UI, the voice summary and
the exported reports can always say where a value came from and how far it can
be trusted. Nothing in this package invents a value: if a fact could not be
collected it is stored with status UNRETRIEVED and value None.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

# Evidence statuses -----------------------------------------------------------
VERIFIED: str = "verified"        # read from the retailer's own page at retrieval time
REPORTED: str = "reported"        # supplied by a search provider; not confirmed at source
UNAVAILABLE: str = "unavailable"  # the source says the item or fact does not exist
UNRETRIEVED: str = "unretrieved"  # we tried and could not get it (blocked, error, absent)

STATUSES: tuple[str, ...] = (VERIFIED, REPORTED, UNAVAILABLE, UNRETRIEVED)

# Agent / run statuses --------------------------------------------------------
AGENT_STATES: tuple[str, ...] = ("idle", "briefed", "researching", "reporting", "done", "blocked", "error", "skipped")
RUN_STATES: tuple[str, ...] = ("queued", "running", "done", "cancelled", "error")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Evidence:
    value: object = None
    status: str = UNRETRIEVED
    source_url: str | None = None
    retrieved_at: str | None = None
    note: str | None = None

    @classmethod
    def verified(cls, value: Any, source_url: str, note: str | None = None) -> Evidence:
        return cls(value, VERIFIED, source_url, now_iso(), note)

    @classmethod
    def reported(cls, value: Any, source_url: str, note: str | None = None) -> Evidence:
        return cls(value, REPORTED, source_url, now_iso(), note)

    @classmethod
    def unretrieved(cls, note: str | None = None, source_url: str | None = None) -> Evidence:
        return cls(None, UNRETRIEVED, source_url, now_iso(), note)

    @classmethod
    def unavailable(cls, note: str | None = None, source_url: str | None = None) -> Evidence:
        return cls(None, UNAVAILABLE, source_url, now_iso(), note)

    @property
    def ok(self) -> bool:
        return self.value is not None and self.status in (VERIFIED, REPORTED)


@dataclass
class Offer:
    """One product listing on one site, with per-field evidence."""
    platform: str
    platform_label: str
    title: str
    url: str | None = None
    brand: str | None = None
    currency: str | None = None
    price: Evidence = field(default_factory=Evidence)
    availability: Evidence = field(default_factory=Evidence)   # in_stock / out_of_stock / preorder
    rating: Evidence = field(default_factory=Evidence)
    review_count: Evidence = field(default_factory=Evidence)
    image: Evidence = field(default_factory=Evidence)
    link: Evidence = field(default_factory=Evidence)           # did the product URL load?
    delivery: Evidence = field(default_factory=Evidence)       # never "verified" from location estimates
    specs: dict[str, str] = field(default_factory=lambda: dict[str, str]())
    spec_status: str = UNRETRIEVED
    review_snippets: list[str] = field(default_factory=lambda: list[str]())  # real review text found on the page
    description: str | None = None
    seller: str | None = None
    match_type: str = "exact"                                  # exact | alternative
    relevance: float = 0.0
    trust: dict[str, Any] = field(default_factory=lambda: dict[str, Any]())  # internet agent: trust notes
    flags: list[dict[str, str]] = field(default_factory=lambda: list[dict[str, str]]())
    sources: list[dict[str, str | None]] = field(default_factory=lambda: list[dict[str, str | None]]())
    score: float | None = None
    score_parts: dict[str, float] = field(default_factory=lambda: dict[str, float]())
    pros: list[str] = field(default_factory=lambda: list[str]())
    cons: list[str] = field(default_factory=lambda: list[str]())
    offer_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AgentReport:
    agent_key: str
    label: str
    status: str                       # found | none_found | blocked | not_configured | error | cancelled
    summary: str
    offers: list[Offer] = field(default_factory=lambda: list[Offer]())
    steps: list[dict[str, Any]] = field(default_factory=lambda: list[dict[str, Any]]())
    queries: list[str] = field(default_factory=lambda: list[str]())
    pages_attempted: int = 0
    pages_verified: int = 0
    limitations: list[str] = field(default_factory=lambda: list[str]())  # honest list of what could not be done
    excluded: list[dict[str, str | None]] = field(default_factory=lambda: list[dict[str, str | None]]())
    started_at: str | None = None
    finished_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
