"""Employee research agents.

Every agent follows the same genuine multi-step workflow on its own platform:

    1 plan queries -> 2 licensed shopping search -> 3 site-scoped web search ->
    4 rank candidates by relevance -> 5 open product pages (robots-compliant)
    and read their structured data -> 6 reconcile search data with page data ->
    7 report what is verified, what is only reported, and what is unavailable.

Agents never invent a listing, price, rating, image or URL. A fact is VERIFIED
only if it was read from the retailer's own page; if a site blocks automated
access the agent says so and falls back to what the licensed search API
reported, labelled as REPORTED.
"""
from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, TypedDict, cast
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from .capabilities import PROFILES, AgentProfile
from .extract import PageProduct, extract_product, looks_like_shopify
from .matching_bridge import relevance_share
from .models import REPORTED, VERIFIED, AgentReport, Evidence, Offer, now_iso
from .net import BLOCKED_CHALLENGE, BLOCKED_ROBOTS, OK, FetchResult
from .search import SearchUnavailable, ShoppingHit, WebHit, domain_of
from .understanding import Brief

TRACKING = {"fbclid", "gclid", "ref", "ref_", "tag", "utm_source", "utm_medium", "utm_campaign",
            "utm_term", "utm_content", "psc", "pd_rd_r", "pd_rd_w", "pd_rd_wg", "pf_rd_p", "pf_rd_r",
            "sprefix", "crid", "keywords", "qid", "sr", "srs", "dib", "dib_tag", "th"}

EXACT_THRESHOLD = 0.75
ALTERNATIVE_THRESHOLD = 0.4

NOISE_DOMAINS = (
    "google.", "youtube.", "reddit.", "quora.", "pinterest.", "facebook.", "instagram.", "twitter.",
    "x.com", "wikipedia.", "linkedin.", "medium.", "blogspot.", "tumblr.", "tiktok.", "bing.", "yahoo.",
    "duckduckgo.", "justdial.", "indiamart.", "alibaba.", "aliexpress.",
    "pricebaba.", "smartprix.", "91mobiles.", "gadgets360.", "mysmartprice.", "compareraja.",
    "pricehistory.", "t.me", "whatsapp.",
)

ESTABLISHED_RETAILERS = {
    "tatacliq.com", "reliancedigital.in", "jiomart.com", "bigbasket.com", "ikea.com", "urbanladder.com",
    "wakefit.co", "decathlon.in", "lenskart.com", "1mg.com", "pharmeasy.in", "vijaysales.com",
    "sangeethamobiles.com", "lifestylestores.com", "pantaloons.com", "westside.com", "bewakoof.com",
    "nilkamalfurniture.com", "hometown.in", "zara.com", "uniqlo.com", "marksandspencer.in",
    "titan.co.in", "tanishq.co.in", "caratlane.com", "bluestone.com", "pumaindia.com", "puma.com",
    "nike.com", "adidas.co.in", "sony.co.in", "samsung.com", "apple.com", "mi.com", "oneplus.in",
    "dell.com", "hp.com", "lenovo.com", "asus.com", "acer.com", "lg.com", "bosch-home.in",
    "ikea.com", "godrejinterio.com", "durian.in", "sleepwell.com", "bata.in", "staples.in",
    "officeworks.com.au", "indianoil.in", "boat-lifestyle.com",
}

MAX_STEPS_NOTE = 7


class _Search(Protocol):
    def shopping(
        self, query: str, location: str | None = None
    ) -> list[ShoppingHit]: ...

    def web(
        self, query: str, num: int = 10, location: str | None = None
    ) -> list[WebHit]: ...


class _Fetcher(Protocol):
    def fetch(self, url: str) -> FetchResult: ...


class _EventLog(Protocol):
    @property
    def cancelled(self) -> bool: ...

    def emit(
        self, type_: str, agent: str | None = None, message: str = "", **data: object
    ) -> object: ...


class _ReviewSnippet(TypedDict):
    text: str
    rating: float | int | None


def _verified(
    value: object, source_url: str | None, note: str | None = None
) -> Evidence:
    return Evidence(value, VERIFIED, source_url, now_iso(), note)


def _reported(
    value: object, source_url: str | None, note: str | None = None
) -> Evidence:
    return Evidence(value, REPORTED, source_url, now_iso(), note)


def canonical_url(url: str | None) -> str | None:
    if not url:
        return None
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return None
    query = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=False) if k.lower() not in TRACKING
             and not k.lower().startswith("utm_")]
    host = parsed.netloc.lower()
    host = host[4:] if host.startswith("www.") else host
    return urlunparse((parsed.scheme.lower(), host, parsed.path.rstrip("/") or "/", "", urlencode(query), ""))


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def merchant_matches(profile: AgentProfile, merchant: str) -> bool:
    m = _norm(merchant)
    for alias in profile.merchant_aliases:
        a = _norm(alias)
        if a and m.startswith(a) and (len(a) > 2 or m[len(a):] in ("", "com", "in", "india")):
            return True
    return False


def url_matches(profile: AgentProfile, url: str) -> bool:
    host = domain_of(url)
    return any(host == d or host.endswith("." + d) for d in profile.domains)


def owned_by_any_profile(
    merchant: str | None, url: str | None, exclude_key: str | None = None
) -> AgentProfile | None:
    for p in PROFILES:
        if p.key in (exclude_key, "internet", "shopify"):
            continue
        if (merchant and merchant_matches(p, merchant)) or (url and url_matches(p, url)):
            return p
    return None


@dataclass
class Candidate:
    url: str | None
    title: str
    merchant: str = ""
    shopping: ShoppingHit | None = None
    web: WebHit | None = None
    relevance: float = 0.0


class AgentContext:
    """Everything an agent needs; built once per run by the Supervisor."""

    def __init__(
        self,
        brief: Brief,
        search: _Search,
        fetcher: _Fetcher,
        log: _EventLog,
        settings: Mapping[str, object] | None = None,
        location: Mapping[str, str] | None = None,
    ) -> None:
        self.brief = brief
        self.search = search
        self.fetcher = fetcher
        self.log = log
        self.settings = settings or {}
        self.location = location
        self.deadline: float | None = None
        page_setting = self.settings.get("PAGES_PER_AGENT", 4)
        self.pages_per_agent = (
            int(page_setting) if isinstance(page_setting, (str, int, float)) else 4
        )
        currency = self.settings.get("DEFAULT_CURRENCY", "INR")
        self.default_currency = currency if isinstance(currency, str) else "INR"

    @property
    def location_label(self) -> str | None:
        return (self.location or {}).get("label")

    def cancelled(self) -> bool:
        return self.log.cancelled or bool(self.deadline and time.monotonic() > self.deadline)


class ResearchAgent:
    def __init__(self, profile: AgentProfile) -> None:
        self.profile = profile
        self.key = profile.key
        self.label = profile.label
        self.name = (profile.label if profile.label.lower().endswith(" agent")
                     else f"{profile.label} Agent")

    # ---- event helpers ---------------------------------------------------
    def _status(
        self, ctx: AgentContext, state: str, message: str, **data: object
    ) -> None:
        ctx.log.emit("agent_status", agent=self.key, message=message, state=state, **data)

    def _step(
        self,
        ctx: AgentContext,
        report: AgentReport,
        name: str,
        detail: str,
        ok: bool = True,
        done: int | None = None,
        total: int | None = None,
    ) -> None:
        report.steps.append({"step": name, "detail": detail, "ok": ok, "at": now_iso()})
        ctx.log.emit("agent_step", agent=self.key, message=detail, step=name, ok=ok,
                     progress={"done": done, "total": total})

    # ---- query planning --------------------------------------------------
    def plan_queries(self, ctx: AgentContext) -> tuple[str, list[str]]:
        product = ctx.brief.product
        shop_q = f"{product} {self.label}" if self.profile.kind != "internet" else product
        web_qs: list[str] = []
        if self.profile.domains:
            web_qs.append(f"site:{self.profile.domains[0]} {product}")
        return shop_q, web_qs

    def accepts_shopping_hit(self, hit: ShoppingHit) -> bool:
        return merchant_matches(self.profile, hit.merchant) or bool(
            hit.url and url_matches(self.profile, hit.url)
        )

    def accepts_web_hit(self, hit: WebHit) -> bool:
        return url_matches(self.profile, hit.url)

    # ---- main workflow ---------------------------------------------------
    def research(self, ctx: AgentContext) -> AgentReport:
        report = AgentReport(self.key, self.label, "none_found", "", started_at=now_iso())
        total = 6 + ctx.pages_per_agent
        done = 0
        self._status(ctx, "researching", f"{self.name} started research.")

        try:
            shop_q, web_qs = self.plan_queries(ctx)
            report.queries = [shop_q] + web_qs
            done += 1
            self._step(ctx, report, "plan", f"Planned queries: {', '.join(repr(q) for q in report.queries)}",
                       done=done, total=total)
            if self.profile.hyperlocal and not ctx.location_label:
                note = (f"{self.label} stock and delivery depend on the delivery address. No location was shared, "
                        "so availability cannot be confirmed.")
                report.limitations.append(note)
                self._step(ctx, report, "location", note, ok=False, done=done, total=total)

            candidates: dict[str, Candidate] = {}
            if ctx.cancelled():
                return self._cancelled(ctx, report)

            # 2. licensed shopping search --------------------------------------
            try:
                hits = ctx.search.shopping(shop_q, location=ctx.location_label)
                kept = [h for h in hits if self.accepts_shopping_hit(h)]
                for h in kept:
                    key = canonical_url(h.url) or f"shop:{_norm(h.title)}:{h.price}"
                    candidates.setdefault(key, Candidate(h.url, h.title, h.merchant, shopping=h))
                done += 1
                self._step(ctx, report, "shopping_search",
                           f"Shopping search returned {len(hits)} listings; {len(kept)} usable for {self.label}.",
                           done=done, total=total)
            except SearchUnavailable as error:
                report.status = "not_configured" if "key" in error.user_message.lower() else "error"
                report.summary = error.user_message
                report.limitations.append(error.user_message)
                self._step(ctx, report, "shopping_search", error.user_message, ok=False, done=done, total=total)
                self._status(ctx, "error", error.user_message)
                report.finished_at = now_iso()
                return report

            # 3. site-scoped web search ----------------------------------------
            for query in web_qs:
                if ctx.cancelled():
                    return self._cancelled(ctx, report)
                try:
                    hits = ctx.search.web(query, num=10, location=ctx.location_label)
                except SearchUnavailable as error:
                    report.limitations.append(error.user_message)
                    self._step(ctx, report, "web_search", error.user_message, ok=False, done=done, total=total)
                    continue
                kept = [h for h in hits if self.accepts_web_hit(h)]
                for h in kept:
                    key = canonical_url(h.url) or f"web:{h.url}"
                    if key in candidates:
                        candidates[key].web = h
                    else:
                        candidates[key] = Candidate(h.url, h.title, self.label, web=h)
                done += 1
                self._step(ctx, report, "web_search",
                           f"Web search{' on ' + self.profile.domains[0] if self.profile.domains else ''} returned {len(kept)} usable page(s).",
                           done=done, total=total)

            self._extra_discovery(ctx, report, candidates)

            # 4. relevance ranking -----------------------------------------------
            ranked: list[Candidate] = []
            for cand in candidates.values():
                cand.relevance = relevance_share(ctx.brief.product, cand.title)
                if cand.relevance >= ALTERNATIVE_THRESHOLD:
                    ranked.append(cand)
                else:
                    report.excluded.append({
                        "title": cand.title,
                        "url": cand.url,
                        "reason": f"Not relevant to “{ctx.brief.product}” (title match {cand.relevance:.0%}).",
                    })
            ranked.sort(key=lambda c: (-c.relevance, c.shopping.price if c.shopping and c.shopping.price else 9e12))
            done += 1
            self._step(ctx, report, "rank",
                       f"{len(ranked)} relevant candidates ({len(report.excluded)} discarded as unrelated).",
                       done=done, total=total)

            if not ranked:
                report.status = "none_found"
                report.summary = (f"No relevant {ctx.brief.product} listing was found on {self.label} "
                                  "through the licensed search sources.")
                report.limitations.append("Absence in search results is not proof that the platform does not sell the item.")
                self._status(ctx, "done", report.summary, found=0)
                report.finished_at = now_iso()
                return report

            # 5-6. open pages and reconcile ---------------------------------------
            opened = 0
            blocked = 0
            offers: list[Offer] = []
            for cand in ranked:
                if ctx.cancelled():
                    return self._cancelled(ctx, report)
                page: PageProduct | None = None
                fetch: FetchResult | None = None
                can_open = bool(cand.url) and not (cand.shopping and cand.shopping.google_link and not cand.web)
                if can_open and opened < ctx.pages_per_agent:
                    opened += 1
                    report.pages_attempted += 1
                    assert cand.url is not None
                    fetch = ctx.fetcher.fetch(cand.url)
                    if fetch.status == OK:
                        page = extract_product(fetch.text)
                        if page is None:
                            self._step(ctx, report, "open_page",
                                       f"Opened {domain_of(cand.url)} but found no product data on the page.",
                                       ok=False, done=min(done + opened, total - 1), total=total)
                        else:
                            if self.profile.kind == "independent" and not looks_like_shopify(fetch.text):
                                self._step(ctx, report, "open_page",
                                           f"{domain_of(cand.url)} is not a Shopify store; skipped.",
                                           ok=False, done=min(done + opened, total - 1), total=total)
                                page = None
                                fetch = None
                                opened -= 1
                                report.pages_attempted -= 1
                                continue
                            report.pages_verified += 1
                            self._step(ctx, report, "open_page",
                                       f"Opened {domain_of(cand.url)}: read “{(page.name or '')[:60]}”"
                                       + (f" at {page.currency or ''} {page.price:g}" if page.price else "")
                                       + ".",
                                       done=min(done + opened, total - 1), total=total)
                    else:
                        blocked += 1
                        why = {BLOCKED_ROBOTS: "robots.txt does not allow automated access",
                               BLOCKED_CHALLENGE: "the site refused automated access"}.get(fetch.status, fetch.detail or fetch.status)
                        self._step(ctx, report, "open_page",
                                   f"Could not open {domain_of(cand.url)} ({why}). Using the search provider's data, "
                                   "marked as not verified at source.",
                                   ok=False, done=min(done + opened, total - 1), total=total)
                if self.profile.kind == "independent" and page is None:
                    continue  # Shopify agent only reports pages it confirmed are Shopify stores
                if self.profile.kind == "internet" and page is None and not (cand.shopping and cand.shopping.price):
                    continue  # nothing usable
                offers.append(self._build_offer(ctx, cand, page, fetch))

            report.offers = offers
            if not offers:
                report.status = "blocked" if blocked else "none_found"
                report.summary = (f"{self.label} pages could not be read, and no usable data was found."
                                  if blocked else f"No usable {self.label} product data was found.")
            else:
                report.status = "found"
                verified = sum(1 for o in offers if o.price.status == VERIFIED)
                report.summary = (f"{len(offers)} relevant listing(s) on {self.label}; "
                                  f"{verified} confirmed on the retailer's own page, "
                                  f"{len(offers) - verified} reported by the search provider only.")
            if blocked:
                report.limitations.append(f"{blocked} page(s) could not be opened because the site restricts automated access; "
                                          "we did not attempt to bypass this.")
            done = total
            self._step(ctx, report, "report", report.summary, ok=bool(offers), done=done, total=total)
            self._status(ctx, "done" if offers else "blocked" if blocked else "done", report.summary,
                         found=len(offers))
        except Exception as error:  # an agent must never crash the run
            report.status = "error"
            report.summary = f"{self.name} hit an unexpected error ({type(error).__name__})."
            report.limitations.append(report.summary)
            ctx.log.emit("agent_error", agent=self.key, message=report.summary)
            self._status(ctx, "error", report.summary)
        report.finished_at = now_iso()
        return report

    def _cancelled(self, ctx: AgentContext, report: AgentReport) -> AgentReport:
        report.status = "cancelled"
        report.summary = (f"{self.label} stopped early because the run was interrupted." if ctx.log.cancelled
                          else f"{self.label} stopped early because the run's time limit was reached.")
        report.finished_at = now_iso()
        self._status(ctx, "idle", report.summary)
        return report

    def _extra_discovery(
        self, ctx: AgentContext, report: AgentReport, candidates: dict[str, Candidate]
    ) -> None:
        """Hook for agents that discover beyond one domain."""
        _ = ctx, report, candidates

    # ---- offer construction -----------------------------------------------
    def _build_offer(
        self,
        ctx: AgentContext,
        cand: Candidate,
        page: PageProduct | None,
        fetch: FetchResult | None,
    ) -> Offer:
        hit = cand.shopping
        url = cand.url
        if page is not None:
            assert url is not None
        platform_label = self.label
        if self.profile.kind in ("internet", "independent") and url:
            platform_label = f"{self.label} · {domain_of(url)}"
        offer = Offer(
            platform=self.key, platform_label=platform_label,
            title=(page.name if page and page.name else cand.title),
            url=url, brand=page.brand if page else None,
            relevance=round(cand.relevance, 2),
            match_type="exact" if cand.relevance >= EXACT_THRESHOLD else "alternative",
            description=(page.description[:500] if page and page.description else None),
            seller=page.seller if page else (cand.merchant or None),
        )
        offer.offer_id = hashlib.sha1(f"{self.key}|{canonical_url(url) or offer.title}".encode()).hexdigest()[:12]
        src_url = url
        if page and fetch is not None:
            offer.sources.append({"url": fetch.final_url or url, "kind": "retailer_page", "retrieved_at": fetch.retrieved_at})
        if hit:
            offer.sources.append({"url": hit.url, "kind": "search_listing", "retrieved_at": hit.retrieved_at})

        # price
        currency = (page.currency if page and page.currency else None) or ctx.default_currency
        offer.currency = currency
        if page and page.price is not None:
            offer.price = _verified(page.price, src_url, "Read from the retailer's product page.")
            if hit and hit.price and abs(hit.price - page.price) / page.price > 0.05:
                offer.flags.append({"code": "price_mismatch",
                                    "detail": f"Search listing showed {hit.price:g}, the retailer page shows {page.price:g}; the page price is used."})
        elif hit and hit.price is not None:
            offer.price = _reported(
                hit.price, hit.url, "From the search provider; not confirmed on the retailer's page."
            )
        else:
            offer.price = Evidence.unretrieved("No price could be read.", src_url)

        # availability
        if page and page.availability:
            offer.availability = _verified(page.availability, src_url, "Stated on the retailer's page.")
        elif fetch is not None and fetch.status != OK:
            offer.availability = Evidence.unretrieved("Retailer page could not be opened.", src_url)
        else:
            offer.availability = Evidence.unretrieved("Availability was not stated.", src_url)
        if self.profile.hyperlocal:
            offer.availability = Evidence.unretrieved(
                "Depends on the delivery address; not confirmed." if not ctx.location_label
                else f"Only an estimate for {ctx.location_label}; not confirmed by the retailer.", src_url)

        # rating / reviews
        if page and page.rating is not None:
            offer.rating = _verified(page.rating, src_url)
            if page.review_count is not None:
                offer.review_count = _verified(page.review_count, src_url)
        elif hit and hit.rating is not None:
            offer.rating = _reported(hit.rating, hit.url, "From the search provider.")
            if hit.review_count is not None:
                offer.review_count = _reported(hit.review_count, hit.url, "From the search provider.")
        else:
            offer.rating = Evidence.unretrieved("No rating published.", src_url)

        # image
        image = (page.image if page and page.image else None) or (hit.image if hit else None)
        if image:
            offer.image = _reported(image, src_url, "Image link found; checked later by the Supervisor.")
        else:
            offer.image = Evidence.unretrieved("No image found.", src_url)

        # link
        if fetch is not None and fetch.status == OK:
            offer.link = _verified(fetch.final_url or url, url, "Page loaded successfully.")
        elif hit and hit.google_link and not page:
            offer.link = _reported(url, url, "Google Shopping page, not a direct retailer link.")
            offer.flags.append({"code": "indirect_link", "detail": "Only a Google Shopping link was available, not the retailer's own page."})
        elif fetch is not None:
            offer.link = Evidence.unretrieved(f"Page not readable: {fetch.detail or fetch.status}.", url)
        elif url:
            offer.link = Evidence.reported(url, url, "Link from search results; not opened (page limit).")
        else:
            offer.link = Evidence.unretrieved("No link available.")

        # delivery (never "verified" by location estimates)
        if hit and hit.delivery:
            where = f" for {ctx.location_label}" if ctx.location_label else ""
            offer.delivery = _reported(
                hit.delivery,
                hit.url,
                f"Search-provider text{where}; an estimate, not confirmed by the retailer.",
            )

        # specs & reviews
        if page:
            raw_specs: object = getattr(page, "specs", {})
            specs: dict[str, str] = {}
            if isinstance(raw_specs, Mapping):
                for key, value in cast(Mapping[object, object], raw_specs).items():
                    if isinstance(key, str) and isinstance(value, str):
                        specs[key] = value
            if specs:
                offer.specs, offer.spec_status = specs, VERIFIED
        if page:
            raw_reviews: object = getattr(page, "reviews", [])
            snippets: list[_ReviewSnippet] = []
            if isinstance(raw_reviews, list):
                for review in cast(list[object], raw_reviews):
                    if not isinstance(review, Mapping):
                        continue
                    review_data = cast(Mapping[object, object], review)
                    text = review_data.get("text")
                    rating = review_data.get("rating")
                    if isinstance(text, str) and (
                        rating is None or isinstance(rating, (int, float))
                    ):
                        snippets.append({"text": text, "rating": rating})
            # The report pipeline consumes these structured snippets, although Offer
            # currently annotates this field as list[str].
            offer.review_snippets = cast(list[str], snippets)
        self._post_process(ctx, offer, cand, page)
        return offer

    def _post_process(
        self,
        ctx: AgentContext,
        offer: Offer,
        cand: Candidate,
        page: PageProduct | None,
    ) -> None:
        """Hook for subclass-specific tagging."""
        _ = ctx, offer, cand, page


class InternetResearchAgent(ResearchAgent):
    """Internet agent: discovers retailers beyond the supported platforms."""

    def plan_queries(self, ctx: AgentContext) -> tuple[str, list[str]]:
        product = ctx.brief.product
        return product, [f"buy {product} online India", f"{product} price in India"]

    def accepts_shopping_hit(self, hit: ShoppingHit) -> bool:
        if owned_by_any_profile(hit.merchant, hit.url):
            return False
        return not any(n in (domain_of(hit.url) or "") for n in NOISE_DOMAINS if not n.startswith("google.")) \
            or hit.google_link

    def accepts_web_hit(self, hit: WebHit) -> bool:
        domain = hit.domain
        if any(n in domain for n in NOISE_DOMAINS):
            return False
        return owned_by_any_profile("", hit.url) is None

    def _post_process(
        self,
        ctx: AgentContext,
        offer: Offer,
        cand: Candidate,
        page: PageProduct | None,
    ) -> None:
        _ = ctx, cand
        domain = domain_of(offer.url) if offer.url else ""
        reasons: list[str] = []
        level = "unrated"
        if domain and any(domain == d or domain.endswith("." + d) for d in ESTABLISHED_RETAILERS):
            level = "established"
            reasons.append("Recognised, long-standing retailer or brand store.")
        if offer.url and offer.url.startswith("https://"):
            reasons.append("Uses HTTPS.")
        else:
            reasons.append("Does not use HTTPS: be careful with payments.")
            offer.flags.append({"code": "no_https", "detail": "The page is not served over HTTPS."})
        if page and page.has_offer:
            reasons.append("Publishes structured price and availability data.")
        if level == "unrated":
            reasons.append("Not on our list of established retailers: check seller reviews and return policy before buying.")
        offer.trust = {"level": level, "domain": domain, "reasons": reasons}


class ShopifyStoresAgent(ResearchAgent):
    """Finds independent brand stores built on Shopify."""

    def plan_queries(self, ctx: AgentContext) -> tuple[str, list[str]]:
        product = ctx.brief.product
        return product, [f'inurl:/products/ {product} India', f'{product} "powered by Shopify"']

    def accepts_shopping_hit(self, hit: ShoppingHit) -> bool:
        _ = hit
        return False  # independent stores are found through web search and verified on the page

    def accepts_web_hit(self, hit: WebHit) -> bool:
        if any(n in hit.domain for n in NOISE_DOMAINS):
            return False
        return owned_by_any_profile("", hit.url) is None

    def _post_process(
        self,
        ctx: AgentContext,
        offer: Offer,
        cand: Candidate,
        page: PageProduct | None,
    ) -> None:
        _ = ctx, cand, page
        offer.trust = {"level": "unrated", "domain": domain_of(offer.url),
                       "reasons": ["Independent Shopify-hosted store confirmed from the page itself.",
                                   "Check seller reputation and return policy before buying."]}


def build_agent(profile: AgentProfile) -> ResearchAgent:
    if profile.key == "internet":
        return InternetResearchAgent(profile)
    if profile.key == "shopify":
        return ShopifyStoresAgent(profile)
    return ResearchAgent(profile)
