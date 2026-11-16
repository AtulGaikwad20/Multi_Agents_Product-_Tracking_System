"""The Supervisor Agent: the office manager.

Lifecycle of a request
  understand -> choose agents -> kickoff meeting -> parallel research ->
  consolidation meeting -> verification -> report + spoken summary.

Follow-ups reuse the work already done: questions are answered from the stored
report, budget/feature changes re-verify the stored listings without refetching,
"also check X" only dispatches the extra agents, and a genuinely new product
starts a linked run (search results are cached, so unchanged queries cost nothing).
"""
from __future__ import annotations

import copy
import logging
import re
import threading
import time
import uuid
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Protocol

from .agents import AgentContext, build_agent
from .capabilities import PROFILE_BY_KEY, AgentProfile, select_agents
from .events import RunLog
from .models import AgentReport, Offer, VERIFIED, now_iso
from .net import FetchResult
from .search import ShoppingHit, WebHit
from .store import StoreError
from app.services.matching import same_product, tokenize
from .understanding import PLATFORM_WORDS, Brief, extract_price_range, understand
from .verification import VerificationResult, Verifier

logger = logging.getLogger(__name__)

MAX_ACTIVE_RUNS_PER_USER = 2
NEXUS_NAME = "NEXUS"


class RunLimitError(Exception):
    pass


class _SearchAvailability(Protocol):
    configured: bool

    def shopping(self, query: str, location: str | None = None) -> list[ShoppingHit]: ...

    def web(self, query: str, num: int = 10, location: str | None = None) -> list[WebHit]: ...


class _Fetcher(Protocol):
    def fetch(self, url: str) -> FetchResult: ...

    def fetch_image(self, url: str) -> FetchResult: ...


class _LanguageModel(Protocol):
    available: bool

    def text(self, system: str, user: str, max_tokens: int = 700) -> str | None: ...

    def json(self, system: str, user: str, max_tokens: int = 900) -> dict[str, object] | None: ...


class _RunStore(Protocol):
    def save(self, record: dict[str, Any]) -> None: ...

    def get(self, user_id: int, run_id: str) -> dict[str, Any] | None: ...


@dataclass
class Run:
    id: str
    user_id: int
    text: str
    language: str
    log: RunLog
    status: str = "queued"
    brief: Brief | None = None
    selected: list[str] = field(default_factory=lambda: list[str]())
    skipped: list[str] = field(default_factory=lambda: list[str]())
    agent_reports: dict[str, AgentReport] = field(default_factory=lambda: dict[str, AgentReport]())
    raw_offers: list[Offer] = field(default_factory=lambda: list[Offer]())
    report: dict[str, Any] | None = None
    location: dict[str, str] | None = None
    location_used: dict[str, str] | None = None
    parent_run_id: str | None = None
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)
    revision: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def record(self) -> dict[str, Any]:
        return {
            "id": self.id, "user_id": self.user_id, "text": self.text, "language": self.language,
            "status": self.status, "brief": self.brief.to_dict() if self.brief else None,
            "report": self.report, "events": self.log.all(), "parent_run_id": self.parent_run_id,
            "location_label": (self.location or {}).get("label"),
            "created_at": self.created_at, "updated_at": self.updated_at, "revision": self.revision,
        }


def _money(value: int | float, currency: str | None = "INR") -> str:
    sym = {"INR": "₹", "USD": "$", "EUR": "€", "GBP": "£"}.get(currency or "", (currency or "") + " ")
    return f"{sym}{value:,.0f}" if float(value).is_integer() else f"{sym}{value:,.2f}"


def _offer_price(offer: Offer) -> float | None:
    value = offer.price.value
    return float(value) if isinstance(value, (int, float)) else None


class Supervisor:
    def __init__(
        self,
        search: _SearchAvailability,
        fetcher: _Fetcher,
        llm: _LanguageModel,
        store: _RunStore,
        settings: dict[str, Any] | None = None,
    ) -> None:
        self.search = search
        self.fetcher = fetcher
        self.llm = llm
        self.store = store
        self.settings = settings or {}
        self.runs: dict[str, Run] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ runs
    def get_run(self, run_id: str) -> Run | None:
        return self.runs.get(run_id)

    def open_run(self, user_id: int, run_id: str) -> Run | None:
        """Return a live Run, rebuilding it from the store if it is not in memory (other device / restart)."""
        run = self.runs.get(run_id)
        if run:
            return run if run.user_id == user_id else None
        record = self.store.get(user_id, run_id)
        if not record:
            return None
        brief_data: dict[str, Any] = record.get("brief") or {}
        brief = Brief(**{key: value for key, value in brief_data.items()
                         if key in Brief.__dataclass_fields__}) if brief_data else None
        run = Run(record["id"], user_id, record["text"], record["language"], RunLog(record["id"]),
                  status=record["status"] if record["status"] in ("done", "cancelled", "error") else "error",
                  brief=brief, report=record.get("report"), parent_run_id=record.get("parent_run_id"),
                  created_at=record["created_at"], updated_at=record["updated_at"], revision=record.get("revision", 0))
        run.log.load(record.get("events") or [])
        if record.get("location_label"):
            run.location_used = {"label": record["location_label"]}
        self.runs[run.id] = run
        return run

    def _active_count(self, user_id: int) -> int:
        return sum(1 for r in self.runs.values() if r.user_id == user_id and r.status in ("queued", "running"))

    def start(
        self,
        user_id: int,
        text: str,
        language: str = "en",
        location: dict[str, str] | None = None,
        parent_run_id: str | None = None,
        forced_platforms: Sequence[str] | None = None,
    ) -> Run:
        with self._lock:
            if self._active_count(user_id) >= MAX_ACTIVE_RUNS_PER_USER:
                raise RunLimitError("You already have research running. Interrupt it or wait for it to finish.")
            run = Run(uuid.uuid4().hex, user_id, text, language, RunLog(uuid.uuid4().hex),
                      location=location, parent_run_id=parent_run_id)
            run.log.run_id = run.id
            self.store.save(run.record())
            self.runs[run.id] = run
        thread = threading.Thread(target=self._execute, args=(run, forced_platforms), daemon=True,
                                  name=f"run-{run.id[:6]}")
        thread.start()
        return run

    def cancel(self, run_id: str) -> Run | None:
        run = self.runs.get(run_id)
        if run and run.status in ("queued", "running"):
            run.log.cancel()
            run.log.emit("supervisor_say", agent="supervisor",
                         message=self._localized_response(
                             "Understood, I'm stopping the team now. Anything already verified will be kept.",
                             run.brief.language if run.brief else run.language))
        return run

    # --------------------------------------------------------------- pipeline
    def _context(self, run: Run) -> AgentContext:
        assert run.brief is not None
        ctx = AgentContext(run.brief, self.search, self.fetcher, run.log, self.settings, run.location_used)
        ctx.deadline = time.monotonic() + float(self.settings.get("RUN_TIMEOUT_SECONDS", 240))
        return ctx

    def _execute(self, run: Run, forced_platforms: Sequence[str] | None = None) -> None:
        log = run.log
        run.status = "running"
        try:
            log.emit("run_started", agent="supervisor", message="Run started.", run_id=run.id,
                     provider_configured=self.search.configured, llm_available=self.llm.available)
            run.brief = understand(run.text, self.llm, run.language)
            run.language = run.brief.language
            if forced_platforms:
                run.brief.platforms_requested = sorted(set(run.brief.platforms_requested) | set(forced_platforms))
            log.emit("understanding", agent="supervisor",
                     message=f"Request understood by {'Claude' if run.brief.understood_by == 'llm' else 'basic rules'}: "
                             f"“{run.brief.product}”.", brief=run.brief.to_dict())
            if run.brief.notice:
                log.emit("notice", agent="supervisor", message=run.brief.notice)
            if run.brief.location_relevant and not run.location:
                log.emit("location_suggested", agent="supervisor",
                         message=run.brief.location_reason or "Local availability may matter for this request.",
                         reason=run.brief.location_reason)

            selected, skipped = select_agents(run.brief.tags, run.brief.platforms_requested,
                                              limit=int(self.settings.get("MAX_AGENTS_PER_RUN", 12)))
            run.selected = [p.key for p, _ in selected]
            run.skipped = [p.key for p, _ in skipped]
            wants_location = run.brief.location_relevant or any(p.hyperlocal for p, _ in selected)
            run.location_used = run.location if (run.location and wants_location) else None
            if run.location and not run.location_used:
                log.emit("notice", agent="supervisor",
                         message="Your saved location was not used: this request doesn't depend on where you are.")
            elif run.location_used:
                log.emit("location_used", agent="supervisor",
                         message=f"Using {run.location_used['label']} ({run.location_used.get('precision')}) to bias search "
                                 "results. Delivery and local-stock information will be estimates, not retailer-confirmed.",
                         label=run.location_used["label"])
            log.emit("team_selected", agent="supervisor",
                     message=f"Activating {len(selected)} agent(s); {len(skipped)} not relevant to this product.",
                     selected=[{"key": p.key, "label": p.label, "role": p.role, "reason": r, "avatar": p.avatar,
                                "kind": p.kind, "note": p.note} for p, r in selected],
                     skipped=[{"key": p.key, "label": p.label, "reason": r, "avatar": p.avatar} for p, r in skipped])
            self._kickoff_meeting(run, selected, skipped)
            if log.cancelled:
                return self._finish_cancelled(run)

            self._dispatch(run, selected)
            if log.cancelled and not run.raw_offers:
                return self._finish_cancelled(run)

            if not log.cancelled:
                self._consolidation_meeting(run)
            self._verify_and_report(run)
        except Exception as error:  # last-resort safety net: the run must always terminate cleanly
            logger.exception("run %s failed", run.id)
            run.status = "error"
            log.emit("run_error", agent="supervisor",
                     message=f"Something went wrong while coordinating the team ({type(error).__name__}).")
            self._persist(run)
        finally:
            run.updated_at = now_iso()
            log.close()

    # ---- meetings ---------------------------------------------------------
    def _say(
        self, run: Run, speaker: str, text: str, role: str = "agent", meeting: str | None = None
    ) -> None:
        run.log.emit("meeting_message", agent=speaker, message=text, role=role, meeting=meeting)

    def _kickoff_meeting(
        self,
        run: Run,
        selected: list[tuple[AgentProfile, str]],
        skipped: list[tuple[AgentProfile, str]],
    ) -> None:
        assert run.brief is not None
        b = run.brief
        run.log.emit("meeting_started", agent="supervisor", message="Kick-off meeting", meeting="kickoff",
                     participants=[p.key for p, _ in selected])
        budget = ""
        if b.max_price or b.min_price:
            lo = _money(b.min_price) if b.min_price else None
            hi = _money(b.max_price) if b.max_price else None
            budget = f" Budget: {lo + ' to ' if lo else 'up to '}{hi or 'any'}." if hi or lo else ""
        areas = ", ".join(t.replace("_", " ") for t in b.tags)
        self._say(run, "supervisor",
                  self._localized_response(
                      f"Team, new request: “{b.product}”. I've classed it under {areas}.{budget} "
                      f"Please research your own platforms, verify at source where you're allowed, and report evidence only.",
                      b.language),
                  "supervisor", "kickoff")
        for profile, _ in selected:
            if profile.hyperlocal:
                line = (f"{profile.label} here. Stock depends on the delivery address; "
                        + (f"I'll use {run.location_used['label']} as an estimate only." if run.location_used
                           else "no location was shared, so I can't confirm availability."))
            elif profile.kind == "internet":
                line = "Internet desk here. I'll look beyond our usual platforms for genuine retailers and check each page's own data."
            elif profile.kind == "independent":
                line = "Independent-stores desk here. I'll look for Shopify-hosted brand stores and confirm them from the page itself."
            else:
                line = f"{profile.label} here. I'll search {profile.domains[0]} for “{b.product}” and open the best matches."
            self._say(run, profile.key, line, "agent", "kickoff")
        if skipped:
            names = ", ".join(p.label for p, _ in skipped)
            self._say(run, "supervisor", self._localized_response(
                f"Standing down: {names}. Their platforms don't cover this kind of product.", b.language),
                      "supervisor", "kickoff")
        run.log.emit("meeting_ended", agent="supervisor", message="Kick-off finished; tasks assigned.", meeting="kickoff")

    def _consolidation_meeting(self, run: Run) -> None:
        reports = list(run.agent_reports.values())
        run.log.emit("meeting_started", agent="supervisor", message="Findings meeting", meeting="findings",
                     participants=[r.agent_key for r in reports])
        cheapest_by_agent: list[tuple[str, Offer, float]] = []
        for r in reports:
            if r.status == "found":
                live = [
                    (o, price) for o in r.offers
                    if (price := _offer_price(o)) and not (
                        o.availability.status == VERIFIED and o.availability.value == "out_of_stock"
                    )
                ]
                cheapest_pair = min(live, key=lambda item: item[1], default=None)
                msg = r.summary
                if cheapest_pair:
                    cheapest, price = cheapest_pair
                    msg += (f" Cheapest in stock on my platform: {_money(price, cheapest.currency)}"
                            f" ({'confirmed at source' if cheapest.price.status == VERIFIED else 'search-reported, not confirmed'}).")
                    cheapest_by_agent.append((r.label, cheapest, price))
                elif r.offers:
                    msg += " Everything I found is confirmed out of stock."
            else:
                msg = r.summary or "Nothing to report."
            if r.limitations:
                msg += " Limits: " + " ".join(r.limitations[:2])
            self._say(run, r.agent_key, msg, "agent", "findings")
        remark: str | None = None
        for i, (n1, o1, price1) in enumerate(cheapest_by_agent):
            for n2, o2, price2 in cheapest_by_agent[i + 1:]:
                if same_product(tokenize(o1.title), tokenize(o2.title)) and price1 != price2:
                    (lo_n, lo, lo_price), (hi_n, hi, hi_price) = sorted(
                        ((n1, o1, price1), (n2, o2, price2)), key=lambda item: item[2]
                    )
                    remark = (f"For the same product, {hi_n} ({_money(hi_price, hi.currency)}) costs "
                              f"{(hi_price - lo_price) / lo_price:.0%} more than {lo_n} "
                              f"({_money(lo_price, lo.currency)}).")
                    break
            if remark:
                break
        self._say(run, "supervisor",
                  self._localized_response(
                      (remark + " " if remark else "I see no like-for-like price pairs yet, so I won't compare across different products. ")
                      + "I'll now cross-check every price, link and image before telling the user.",
                      run.brief.language if run.brief else run.language),
                  "supervisor", "findings")
        run.log.emit("meeting_ended", agent="supervisor", message="Findings meeting finished.", meeting="findings")

    # ---- dispatch ---------------------------------------------------------
    def _dispatch(self, run: Run, selected: list[tuple[AgentProfile, str]]) -> None:
        assert run.brief is not None
        ctx = self._context(run)
        agents = [build_agent(p) for p, _ in selected]
        for a in agents:
            run.log.emit("task_assigned", agent=a.key, message=f"Research “{run.brief.product}” on {a.label}.",
                         task={"id": f"{run.id[:6]}-{a.key}", "title": f"Research “{run.brief.product}”",
                               "state": "assigned"})
            run.log.emit("agent_status", agent=a.key, message="Briefed and ready.", state="briefed")
        with ThreadPoolExecutor(max_workers=min(8, max(1, len(agents)))) as pool:
            futures = {pool.submit(a.research, ctx): a for a in agents}
            for future, agent in futures.items():
                try:
                    report = future.result()
                except Exception as error:
                    from .models import AgentReport
                    report = AgentReport(agent.key, agent.label, "error",
                                         f"{agent.name} failed ({type(error).__name__}).")
                run.agent_reports[agent.key] = report
                run.raw_offers.extend(report.offers)
                run.log.emit("agent_report", agent=agent.key, message=report.summary,
                             status=report.status, offers=len(report.offers))

    # ---- verification & report ---------------------------------------------
    def _verify_and_report(self, run: Run) -> None:
        assert run.brief is not None
        run.log.emit("verification_started", agent="supervisor",
                     message=f"{NEXUS_NAME} verification started: cross-checking prices, links, images and your requirements.")
        verifier = Verifier(run.brief, self.fetcher, run.log, self.settings)
        result = verifier.run(copy.deepcopy(run.raw_offers))
        run.report = self._build_report(run, result)
        run.report["summary"] = self._summary(run, run.report)
        run.revision += 1
        run.status = "cancelled" if run.log.cancelled else "done"
        run.updated_at = now_iso()
        run.log.emit("report_ready", agent="supervisor", message="Final report ready.", revision=run.revision,
                     report=run.report)
        run.log.emit("supervisor_say", agent="supervisor", message=run.report["summary"]["text"],
                     language=run.report["summary"]["language"], speak=True)
        run.log.emit("run_done", agent="supervisor", message="Run complete.", status=run.status)
        self._persist(run)       # after the final events, so another device replays the complete history

    def _build_report(self, run: Run, result: VerificationResult) -> dict[str, Any]:
        assert run.brief is not None
        offers: list[dict[str, Any]] = [o.to_dict() for o in result.accepted]
        stamps: list[str] = [s for o in result.accepted for s in (o.price.retrieved_at,) if s]
        unavailable: list[dict[str, Any]] = []
        for o in result.out_of_stock:
            unavailable.append({"platform": o.platform_label, "title": o.title, "url": o.url,
                                "reason": "The retailer's page states this item is out of stock.",
                                "kind": "out_of_stock"})
        agent_rows: list[dict[str, Any]] = []
        for key in run.selected:
            r = run.agent_reports.get(key)
            if not r:
                continue
            agent_rows.append({"key": r.agent_key, "label": r.label, "status": r.status, "summary": r.summary,
                               "limitations": r.limitations, "queries": r.queries,
                               "pages_attempted": r.pages_attempted, "pages_verified": r.pages_verified,
                               "listings": len(r.offers), "excluded_irrelevant": len(r.excluded)})
            if r.status in ("none_found", "blocked", "not_configured", "error"):
                unavailable.append({"platform": r.label, "title": None, "url": None, "reason": r.summary,
                                    "kind": "could_not_access" if r.status in ("blocked", "not_configured", "error") else "not_found"})
        sources: list[dict[str, str | None]] = []
        seen: set[tuple[str, str | None]] = set()
        for o in result.accepted:
            for s in o.sources:
                if s["url"] and (s["url"], s["kind"]) not in seen:
                    seen.add((s["url"], s["kind"]))
                    sources.append({"platform": o.platform_label, **s})
        limitations: list[str] = []
        if not self.search.configured:
            limitations.append("No search provider key is configured, so no live research could be performed.")
        limitations.extend(l for r in run.agent_reports.values() for l in r.limitations[:2])
        if run.location_used:
            limitations.append(f"Location “{run.location_used['label']}” was used only to bias search results. "
                               "Local availability, delivery times and charges are estimates unless a retailer page says otherwise.")
        return {
            "product": run.brief.product, "brief": run.brief.to_dict(), "generated_at": now_iso(),
            "freshness": {"oldest": min(stamps) if stamps else None, "newest": max(stamps) if stamps else None},
            "recommendations": result.recommendations, "offers": offers, "groups": result.groups,
            "unavailable": unavailable,
            "excluded": [{"platform": e["offer"].platform_label, "title": e["offer"].title, "url": e["offer"].url,
                          "reason": e["reason"]} for e in result.excluded],
            "agents": agent_rows, "verification_log": result.log, "sources": sources,
            "location": ({"label": run.location_used["label"], "source": run.location_used.get("source"),
                          "precision": run.location_used.get("precision")} if run.location_used else None),
            "limitations": list(dict.fromkeys(limitations)),
            "evidence_legend": {"verified": "Read from the retailer's own page at the time shown.",
                                "reported": "Reported by the search provider; not confirmed at source.",
                                "unavailable": "The source says it is not available.",
                                "unretrieved": "We tried and could not retrieve it."},
        }

    # ---- summary (spoken + on screen) ----------------------------------------
    def _facts(self, run: Run, report: dict[str, Any]) -> dict[str, Any]:
        by_id = {o["offer_id"]: o for o in report["offers"]}

        def brief_offer(oid: str) -> dict[str, Any] | None:
            o = by_id.get(oid)
            if not o:
                return None
            return {"platform": o["platform_label"], "title": o["title"][:90], "price": o["price"]["value"],
                    "currency": o["currency"], "price_status": o["price"]["status"],
                    "rating": o["rating"]["value"], "reviews": o["review_count"]["value"],
                    "in_stock": o["availability"]["value"]}
        rec = report["recommendations"]
        return {
            "request": run.text, "product": report["product"], "listings_found": len(report["offers"]),
            "agents_used": [a["label"] for a in report["agents"]],
            "best_overall": brief_offer(rec.get("best_overall", {}).get("offer_id")),
            "lowest_price": brief_offer(rec.get("lowest_price", {}).get("offer_id")),
            "top_rated": brief_offer(rec.get("top_rated", {}).get("offer_id")),
            "out_of_stock_on": [u["platform"] for u in report["unavailable"] if u["kind"] == "out_of_stock"],
            "nothing_found_on": [u["platform"] for u in report["unavailable"] if u["kind"] == "not_found"],
            "could_not_access": [u["platform"] for u in report["unavailable"] if u["kind"] == "could_not_access"],
            "retrieved_at_utc": report["freshness"]["newest"],
            "location_used": report["location"]["label"] if report["location"] else None,
            "limitations": report["limitations"][:3],
        }

    def _summary(self, run: Run, report: dict[str, Any]) -> dict[str, Any]:
        assert run.brief is not None
        facts = self._facts(run, report)
        language = run.brief.language or "en"
        if self.llm.available:
            text = self.llm.text(
                f"You are {NEXUS_NAME}, the Supervisor of a product-research team, speaking aloud to the user. Write 3 to 5 short "
                f"sentences in the language with BCP-47 code '{language}'. Use ONLY the facts supplied. Never invent "
                "prices, ratings, stores or availability. Say clearly when something could not be verified or when "
                "nothing was found. Mention that prices were retrieved at the given time. Plain sentences, no lists, "
                "no markdown, no emojis. Prices keep their currency symbol.",
                __import__("json").dumps(facts, ensure_ascii=False), max_tokens=500)
            if text:
                return {"text": text, "language": language, "written_by": "llm"}
        return {"text": self._template_summary(facts), "language": "en", "written_by": "template",
                "note": None if language == "en" else
                "Spoken in English because the language model (Claude) is not configured."}

    @staticmethod
    def _template_summary(f: dict[str, Any]) -> str:
        def names(key: str) -> str:
            return ", ".join(f[key][:5])
        if not f["listings_found"]:
            parts = [f"I could not find any listing for {f['product']} that I can stand behind."]
            if f["nothing_found_on"]:
                parts.append(f"Nothing relevant turned up on {names('nothing_found_on')}.")
            if f["out_of_stock_on"]:
                parts.append(f"Confirmed out of stock on {names('out_of_stock_on')}.")
            if f["could_not_access"]:
                parts.append(f"I could not access {names('could_not_access')}.")
            parts.append(f["limitations"][0] if f["limitations"] else "You can refine the request or ask me to check more platforms.")
            return " ".join(parts)
        parts = [f"My team checked {len(f['agents_used'])} sources for {f['product']} and found {f['listings_found']} listing(s) that passed verification."]

        def describe(o: dict[str, Any]) -> str:
            bits = [f"{o['platform']}"]
            if o["price"]:
                bits.append(f"at {_money(o['price'], o['currency'])}")
            if o["rating"]:
                bits.append(f"rated {o['rating']:g} out of 5" + (f" from {int(o['reviews']):,} reviews" if o["reviews"] else ""))
            bits.append("with the price confirmed on the retailer's page" if o["price_status"] == VERIFIED
                        else "but the price comes from a search listing and is not confirmed at source")
            return " ".join(bits)
        best, low, top = f["best_overall"], f["lowest_price"], f["top_rated"]
        if best:
            same_low = low and (low["platform"], low["price"], low["title"]) == (best["platform"], best["price"], best["title"])
            parts.append(f"The best overall is {describe(best)}" + (", and it is also the lowest price." if same_low else "."))
            if low and not same_low:
                parts.append(f"The lowest price is {describe(low)}.")
            top_pair = (top["platform"], top["title"]) if top else None
            best_pair = (best["platform"], best["title"])
            low_pair = (low["platform"], low["title"]) if low else None
            if top_pair and top_pair not in (best_pair, low_pair):
                parts.append(f"The top rated is {describe(top)}.")
        if f["out_of_stock_on"]:
            parts.append(f"Confirmed out of stock on {names('out_of_stock_on')}.")
        if f["nothing_found_on"]:
            parts.append(f"No relevant listing was found on {names('nothing_found_on')}.")
        if f["could_not_access"]:
            parts.append(f"I could not access {names('could_not_access')}, so those are unverified.")
        if f["retrieved_at_utc"]:
            parts.append(f"Data was retrieved at {f['retrieved_at_utc'][11:16]} UTC.")
        return " ".join(parts)

    # ---- cancellation / persistence -----------------------------------------
    def _finish_cancelled(self, run: Run) -> None:
        run.status = "cancelled"
        run.report = run.report or None
        run.updated_at = now_iso()
        run.log.emit("run_cancelled", agent="supervisor",
                     message="Run interrupted. Tell me what to change and I'll continue from what's already done.")
        self._persist(run)

    def _persist(self, run: Run) -> None:
        try:
            self.store.save(run.record())
        except StoreError:
            run.log.emit("notice", agent="supervisor",
                         message="This research could not be saved to the database. Check the database connection and migrations.",
                         persistence_failed=True)
            logger.exception("Could not persist research run %s", run.id)

    # =========================================================== follow-ups
    FOLLOWUP_SYSTEM = (
        f"You are {NEXUS_NAME}. Route a follow-up message in a product-research conversation. Given the previous brief (JSON) and "
        "the new message, return JSON: action one of "
        '"question" (asks about existing findings), "refine" (changes budget/colour/brand/feature/avoid constraints '
        'that can be applied to listings already found), "expand" (asks to also check named platforms or search more '
        'widely), "new_search" (a different product). Also: changes {min_price,max_price,colors,must_have,avoid,brand} '
        'only for "refine" (numbers in INR; null where unchanged), platforms (list of keys from '
        f'{sorted(PROFILE_BY_KEY)}) for "expand", and language (BCP-47 of the message).')

    def classify_followup(self, run: Run, text: str) -> dict[str, Any]:
        if self.llm.available:
            data = self.llm.json(self.FOLLOWUP_SYSTEM, __import__("json").dumps(
                {"previous_brief": run.brief.to_dict() if run.brief else {}, "message": text}, ensure_ascii=False))
            if isinstance(data, dict) and data.get("action") in ("question", "refine", "expand", "new_search"):
                return data
        lowered = text.lower()
        platforms = [k for k, p in PLATFORM_WORDS.items() if re.search(p, lowered)]
        if platforms and re.search(r"\b(also|too|as well|include|add|check|try|search)\b", lowered):
            return {"action": "expand", "platforms": platforms}
        prices = extract_price_range(text)
        if prices:
            return {"action": "refine", "changes": prices}
        if text.strip().endswith("?") or re.match(r"\s*(what|which|why|how|is|are|does|do|can|tell|explain|compare)\b", lowered):
            return {"action": "question"}
        return {"action": "new_search"}

    def followup(
        self,
        user_id: int,
        run_id: str,
        text: str,
        language: str | None = None,
        location: dict[str, str] | None = None,
    ) -> dict[str, Any] | None:
        run = self.open_run(user_id, run_id)
        if not run:
            return None
        decision = self.classify_followup(run, text)
        action = decision["action"]
        detected_language = decision.get("language")
        reply_language = detected_language if isinstance(detected_language, str) and re.fullmatch(
            r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})?", detected_language
        ) else (language or run.language)
        run.language = reply_language
        if run.brief:
            run.brief.language = reply_language
        if action == "question":
            return {"action": "question", "answer": self.answer_question(run, text, reply_language),
                    "language": reply_language}
        if action in ("refine", "expand") and run.status in ("queued", "running"):
            return {"action": "busy", "answer": self._localized_response(
                "The team is still working. Interrupt first, then I can refine the search.", reply_language),
                "language": reply_language}
        if action in ("refine", "expand") and not run.raw_offers:
            action = "new_search"   # e.g. a run opened from another device: re-run (searches are cached)
        if action == "refine":
            return self._refine(run, decision.get("changes") or {}, text, reply_language)
        if action == "expand" and decision.get("platforms"):
            return self._expand(run, decision["platforms"], location, reply_language)
        new = self.start(user_id, text, reply_language, location or run.location, parent_run_id=run.id)
        return {"action": "new_search", "run_id": new.id}

    def _refine(self, run: Run, changes: dict[str, Any], text: str, language: str) -> dict[str, Any]:
        assert run.brief is not None
        b = run.brief
        for key in ("min_price", "max_price"):
            if key in changes and changes[key] is not None:
                setattr(b, key, float(changes[key]))
        if changes.get("brand"):
            b.brand = changes["brand"]
        for key in ("colors", "must_have", "avoid"):
            if changes.get(key):
                setattr(b, key, list(dict.fromkeys(getattr(b, key) + list(changes[key]))))
        run.status = "running"
        run.log.reopen()
        threading.Thread(target=self._reverify, args=(run, text), daemon=True).start()
        return {"action": "refine", "run_id": run.id,
                "answer": self._localized_response(
                    "Applying your changes to the listings already collected; no need to start over.", language),
                "language": language}

    def _reverify(self, run: Run, text: str) -> None:
        run.log.reopen()
        try:
            assert run.brief is not None
            run.log.emit("followup_started", agent="supervisor", message=f"Refining: “{text}”", brief=run.brief.to_dict())
            self._verify_and_report(run)
        finally:
            run.log.close()

    def _expand(
        self, run: Run, platforms: Sequence[str], location: dict[str, str] | None, language: str
    ) -> dict[str, Any]:
        todo = [k for k in platforms if k in PROFILE_BY_KEY and k not in run.agent_reports]
        if not todo:
            return {"action": "question", "answer": self._localized_response(
                "Those platforms have already been checked in this run.", language), "language": language}
        if location and not run.location:
            run.location = location
            run.location_used = location
        run.status = "running"
        run.log.reopen()
        threading.Thread(target=self._more_research, args=(run, todo), daemon=True).start()
        return {"action": "expand", "run_id": run.id,
                "answer": self._localized_response(
                    "Sending " + ", ".join(PROFILE_BY_KEY[k].label for k in todo) + " to research now.", language),
                "language": language}

    def _more_research(self, run: Run, keys: list[str]) -> None:
        run.log.reopen()
        try:
            assert run.brief is not None
            selected = [(PROFILE_BY_KEY[k], "Added at your request.") for k in keys]
            run.log.emit("followup_started", agent="supervisor",
                         message="Adding agents: " + ", ".join(p.label for p, _ in selected),
                         selected=[{"key": p.key, "label": p.label, "role": p.role, "reason": r,
                                    "avatar": p.avatar, "kind": p.kind, "note": p.note} for p, r in selected],
                         brief=run.brief.to_dict())
            run.selected = list(dict.fromkeys(run.selected + keys))
            self._kickoff_meeting(run, selected, [])
            self._dispatch(run, selected)
            self._consolidation_meeting(run)
            self._verify_and_report(run)
        finally:
            run.log.close()

    def answer_question(self, run: Run, text: str, language: str | None = None) -> str:
        report = run.report
        if not report:
            return self._localized_response("I don't have findings for this request yet.",
                                            language or run.language)
        facts = self._facts(run, report)
        facts["top_listings"] = [{"platform": o["platform_label"], "title": o["title"][:80],
                                  "price": o["price"]["value"], "price_status": o["price"]["status"],
                                  "rating": o["rating"]["value"], "reviews": o["review_count"]["value"],
                                  "pros": o["pros"][:3], "cons": o["cons"][:3], "specs": dict(list(o["specs"].items())[:6]),
                                  "review_snippets": [r["text"][:200] for r in o["review_snippets"][:2]]}
                                 for o in report["offers"][:8]]
        if self.llm.available:
            answer = self.llm.text(
                f"You are {NEXUS_NAME}, the Supervisor of a product-research team answering a follow-up question aloud. Answer in the "
                f"same language as the question{f' (BCP-47: {language})' if language else ''} using ONLY the supplied findings. "
                "If the findings do not contain the "
                "answer, say so and offer to research it. Never invent. 1 to 4 sentences, no markdown.",
                __import__("json").dumps({"question": text, "findings": facts}, ensure_ascii=False), max_tokens=400)
            if answer:
                return answer
        lowered = text.lower()
        best = facts["best_overall"]
        if re.search(r"cheap|lowest|price|cost", lowered) and facts["lowest_price"]:
            o = facts["lowest_price"]
            return f"The lowest price I found is {_money(o['price'], o['currency'])} on {o['platform']}" + (
                ", confirmed on the retailer's page." if o["price_status"] == VERIFIED else ", but that price is from a search listing and was not confirmed at source.")
        if re.search(r"best|recommend|which", lowered) and best:
            return self._template_summary({**facts, "lowest_price": None, "top_rated": None})
        return self._localized_response(
            "I can answer free-form questions about the findings once a language model is configured. "
            "For now: " + self._template_summary(facts), language or run.language)

    def _localized_response(self, text: str, language: str) -> str:
        if not language or language.split("-", 1)[0].lower() == "en" or not self.llm.available:
            return text
        translated = self.llm.text(
            f"You are {NEXUS_NAME}. Translate the supplied message into the user's language "
            f"(BCP-47: {language}). Return only the translation. Preserve product names, URLs, numbers, "
            "and currency amounts exactly; do not add information.",
            __import__("json").dumps({"message": text}, ensure_ascii=False), max_tokens=250)
        return translated or text
