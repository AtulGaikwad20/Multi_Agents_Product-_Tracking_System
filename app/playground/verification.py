"""Supervisor verification: the last line of defence before anything is shown or spoken.

Checks performed, in order (each one is logged and streamed to the playground):
  1. integrity     - impossible values (price <= 0, rating outside 0-5) are removed
  2. duplicates    - same page or same title+price on the same platform
  3. link/domain   - a platform agent's link must belong to that platform
  4. requirements  - budget, brand, excluded terms, stock; features that cannot be
                     confirmed are flagged, not silently accepted
  5. price sanity  - outliers against the same product elsewhere are rechecked at
                     source and excluded if they cannot be confirmed
  6. freshness     - old retrievals are flagged
  7. images        - an image is shown only if it really loads as an image
  8. ranking       - evidence-weighted score; recommendations explain their basis
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from io import BytesIO
from statistics import median
from typing import Callable, Protocol, TypedDict, cast

from app.services.matching import same_product, tokenize
from app.services import scoring

from .agents import canonical_url, url_matches
from .capabilities import PROFILE_BY_KEY
from .events import RunLog
from .extract import extract_product
from .models import REPORTED, UNAVAILABLE, UNRETRIEVED, VERIFIED, Evidence, Offer, now_iso
from .net import OK, FetchResult
from .understanding import Brief

STALE_AFTER_SECONDS = 30 * 60
MAX_IMAGE_CHECKS = 16
_BAYESIAN_RATING = cast(
    Callable[[int | float | None, int | float], float],
    getattr(scoring, "bayesian_rating"),
)


class _ExcludedOffer(TypedDict):
    offer: Offer
    reason: str


class _VerificationGroup(TypedDict):
    title: str
    offer_ids: list[str]
    platforms: list[str]
    lowest_offer_id: str | None


class _OfferGroup(TypedDict):
    tokens: list[str]
    title: str
    members: list[Offer]


class _VerificationFetcher(Protocol):
    def fetch(self, url: str) -> FetchResult: ...

    def fetch_image(self, url: str) -> FetchResult: ...


@dataclass
class VerificationResult:
    accepted: list[Offer] = field(default_factory=list[Offer])
    out_of_stock: list[Offer] = field(default_factory=list[Offer])
    excluded: list[_ExcludedOffer] = field(default_factory=list[_ExcludedOffer])
    log: list[dict[str, object]] = field(default_factory=list[dict[str, object]])
    groups: list[_VerificationGroup] = field(default_factory=list[_VerificationGroup])
    recommendations: dict[str, dict[str, str]] = field(
        default_factory=dict[str, dict[str, str]]
    )


def _as_number(value: object) -> int | float | None:
    if isinstance(value, (int, float)):
        return value
    return None


def _age_seconds(stamp: str | None) -> float | None:
    if stamp is None:
        return None
    try:
        moment = datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return None
    return (datetime.now(timezone.utc) - moment).total_seconds()


class Verifier:
    def __init__(
        self,
        brief: Brief,
        fetcher: _VerificationFetcher,
        log: RunLog,
        settings: Mapping[str, object] | None = None,
    ) -> None:
        self.brief = brief
        self.fetcher = fetcher
        self.run_log = log
        self.settings = settings or {}
        self.result = VerificationResult()

    # ---- logging -----------------------------------------------------------
    def _note(
        self, check: str, message: str, ok: bool = True, **data: object
    ) -> None:
        entry: dict[str, object] = {
            "check": check,
            "message": message,
            "ok": ok,
            "at": now_iso(),
            **data,
        }
        self.result.log.append(entry)
        self.run_log.emit("verification_step", agent="supervisor", message=message, check=check, ok=ok)

    def _exclude(self, offer: Offer, reason: str) -> None:
        self.result.excluded.append({"offer": offer, "reason": reason})

    # ---- main --------------------------------------------------------------
    def run(self, offers: Iterable[Offer]) -> VerificationResult:
        offers = list(offers)
        self._note("start", f"Verifying {len(offers)} candidate listing(s) from the team.")
        offers = self._integrity(offers)
        offers = self._duplicates(offers)
        offers = self._links(offers)
        offers = self._requirements(offers)
        offers = self._price_sanity(offers)
        self._freshness(offers)
        self._images(offers)
        self._rank(offers)
        self.result.accepted = offers
        self._group(offers)
        self._recommend(offers)
        self._note("done", f"{len(offers)} listing(s) passed verification; "
                           f"{len(self.result.excluded)} excluded; {len(self.result.out_of_stock)} out of stock.")
        return self.result

    # ---- 1 ---------------------------------------------------------------
    def _integrity(self, offers: list[Offer]) -> list[Offer]:
        kept: list[Offer] = []
        for o in offers:
            price = _as_number(o.price.value)
            if o.price.value is not None and (price is None or price <= 0):
                self._exclude(o, "Price value is not a positive number.")
                continue
            rating = _as_number(o.rating.value)
            if o.rating.value is not None and (
                rating is None or not 0 < rating <= 5
            ):
                o.rating = Evidence.unretrieved("Rating outside 0–5 was discarded.")
                o.review_count = Evidence.unretrieved("Discarded with the rating.")
                o.flags.append({"code": "bad_rating", "detail": "Published rating was outside 0–5 and was discarded."})
            review_count = _as_number(o.review_count.value)
            if review_count is not None and review_count < 0:
                o.review_count = Evidence.unretrieved("Negative review count discarded.")
            if rating is not None and o.review_count.value is None:
                o.flags.append({"code": "rating_without_count", "detail": "Rating given without a review count; treat with caution."})
            kept.append(o)
        self._note("integrity", f"Integrity check: {len(offers) - len(kept)} impossible value(s) removed.")
        return kept

    # ---- 2 ---------------------------------------------------------------
    def _duplicates(self, offers: list[Offer]) -> list[Offer]:
        seen: dict[str | None, Offer] = {}
        kept: list[Offer] = []
        dropped = 0
        order = {VERIFIED: 0, REPORTED: 1, UNRETRIEVED: 2, UNAVAILABLE: 3}
        for o in sorted(offers, key=lambda x: order.get(x.price.status, 9)):
            keys: set[str | None] = {canonical_url(o.url)} if o.url else set()
            keys.add(f"{o.platform}|{' '.join(tokenize(o.title))}|{o.price.value}")
            if any(k in seen for k in keys):
                dropped += 1
                self._exclude(o, "Duplicate of another listing already included.")
                continue
            for k in keys:
                seen[k] = o
            kept.append(o)
        self._note("duplicates", f"Duplicate check: {dropped} duplicate(s) removed.")
        return kept

    # ---- 3 ---------------------------------------------------------------
    def _links(self, offers: list[Offer]) -> list[Offer]:
        kept: list[Offer] = []
        for o in offers:
            profile = PROFILE_BY_KEY.get(o.platform)
            if not o.url:
                o.flags.append({"code": "no_link", "detail": "No direct product link available."})
            if o.url and profile and profile.domains and not url_matches(profile, o.url) \
                    and not any(f["code"] == "indirect_link" for f in o.flags):
                self._exclude(o, f"Link does not belong to {profile.label}.")
                continue
            kept.append(o)
        self._note("links", f"Link check: {len(offers) - len(kept)} listing(s) removed for mismatched links.")
        return kept

    # ---- 4 ---------------------------------------------------------------
    def _requirements(self, offers: list[Offer]) -> list[Offer]:
        b = self.brief
        kept: list[Offer] = []
        for o in offers:
            price = _as_number(o.price.value)
            text_tokens = set(tokenize(" ".join([o.title, o.brand or "", o.description or "",
                                                 " ".join(f"{k} {v}" for k, v in o.specs.items())])))
            if o.availability.status == VERIFIED and o.availability.value == "out_of_stock":
                self.result.out_of_stock.append(o)
                continue
            if price is not None and b.max_price is not None and price > b.max_price:
                self._exclude(o, f"Over budget ({price:g} > {b.max_price:g}).")
                continue
            if price is not None and b.min_price is not None and price < b.min_price:
                self._exclude(o, f"Below the requested minimum ({price:g} < {b.min_price:g}).")
                continue
            if b.brand:
                brand_tokens = set(tokenize(b.brand))
                if brand_tokens and not brand_tokens & (set(tokenize(o.title)) | set(tokenize(o.brand or ""))):
                    self._exclude(o, f"Brand does not match the requested brand “{b.brand}”.")
                    continue
            bad = [w for w in b.avoid if set(tokenize(w)) and set(tokenize(w)) <= text_tokens]
            if bad:
                self._exclude(o, f"Contains something you asked to avoid: {', '.join(bad)}.")
                continue
            missing = [w for w in b.must_have if set(tokenize(w)) and not set(tokenize(w)) <= text_tokens]
            if missing:
                o.flags.append({"code": "unconfirmed_requirement",
                                "detail": f"Could not confirm: {', '.join(missing)}."})
            if b.colors and not any(c.lower() in text_tokens for c in b.colors):
                o.flags.append({"code": "colour_unconfirmed",
                                "detail": f"Requested colour ({', '.join(b.colors)}) is not stated in the listing."})
            if o.price.value is None:
                o.flags.append({"code": "no_price", "detail": "No price could be retrieved."})
            kept.append(o)
        self._note("requirements",
                   f"Requirement check against your brief: {len(kept)} remain; "
                   f"{len(self.result.out_of_stock)} confirmed out of stock.")
        return kept

    # ---- 5 ---------------------------------------------------------------
    def _price_sanity(self, offers: list[Offer]) -> list[Offer]:
        priced = [
            o
            for o in offers
            if (price := _as_number(o.price.value)) is not None and price
        ]
        outliers: list[Offer] = []
        for o in priced:
            price = _as_number(o.price.value)
            if price is None:
                continue
            peers = [
                peer_price
                for p in priced
                if p is not o
                and same_product(tokenize(o.title), tokenize(p.title))
                and (peer_price := _as_number(p.price.value)) is not None
                and peer_price
            ]
            if len(peers) >= 2:
                m = median(peers)
                if price < 0.3 * m or price > 3 * m:
                    outliers.append(o)
        kept = list(offers)
        for o in outliers:
            confirmed = False
            if o.price.status != VERIFIED and o.url and not any(f["code"] == "indirect_link" for f in o.flags):
                result = self.fetcher.fetch(o.url)
                if result.status == OK:
                    page = extract_product(result.text)
                    if page and page.price:
                        o.price = Evidence.verified(page.price, o.url, "Rechecked at source after outlier flag.")
                        confirmed = True
            if confirmed:
                o.flags.append({"code": "price_outlier_confirmed",
                                "detail": "Price differs a lot from other stores but was confirmed on the retailer's page."})
                self._note("price_sanity", f"Rechecked {o.platform_label}: outlier price confirmed at source.")
            elif o.price.status == VERIFIED:
                o.flags.append({"code": "price_outlier", "detail": "Price differs a lot from other stores for a similar product."})
            else:
                kept.remove(o)
                self._exclude(o, "Price is far from the same product elsewhere and could not be confirmed at source.")
                self._note("price_sanity", f"Excluded {o.platform_label}: unconfirmed price outlier.", ok=False)
        self._note("price_sanity", f"Price sanity check: {len(outliers)} outlier(s) examined.")
        return kept

    # ---- 6 ---------------------------------------------------------------
    def _freshness(self, offers: list[Offer]) -> None:
        stale = 0
        for o in offers:
            age = _age_seconds(o.price.retrieved_at)
            if age is not None and age > STALE_AFTER_SECONDS:
                stale += 1
                o.flags.append({"code": "stale", "detail": "This data was retrieved more than 30 minutes ago."})
        self._note("freshness", f"Freshness check: {stale} listing(s) older than 30 minutes.")

    # ---- 7 ---------------------------------------------------------------
    def _check_image(self, offer: Offer) -> None:
        url = offer.image.value
        if not isinstance(url, str) or not url:
            return
        res = self.fetcher.fetch_image(url)
        if res.status == OK and res.content_type.startswith("image/"):
            try:
                from PIL import Image
                Image.open(BytesIO(res.content)).verify()
                offer.image = Evidence.verified(url, url, "Image downloaded and decoded successfully.")
                return
            except Exception:
                pass
            offer.image = Evidence.unretrieved("Downloaded file was not a valid image.", url)
        elif res.status in ("blocked_robots", "blocked_challenge"):
            offer.image = Evidence.reported(url, url, "Image host restricts automated checks; shown but not verified.")
        else:
            offer.image = Evidence.unretrieved(f"Image could not be loaded ({res.detail or res.status}).", url)

    def _images(self, offers: list[Offer]) -> None:
        targets = [o for o in sorted(offers, key=lambda x: -x.relevance) if o.image.value][:MAX_IMAGE_CHECKS]
        if targets:
            with ThreadPoolExecutor(max_workers=6) as pool:
                list(pool.map(self._check_image, targets))
        bad = sum(1 for o in targets if o.image.status == UNRETRIEVED)
        good = sum(1 for o in targets if o.image.status == VERIFIED)
        self._note("images", f"Image check: {good} verified, {bad} could not be loaded and will not be shown.")
        for o in offers:
            if o not in targets and o.image.value:
                image_url = o.image.value
                if isinstance(image_url, str) and image_url:
                    o.image = Evidence.reported(
                        image_url,
                        o.image.source_url or image_url,
                        "Not checked (image limit).",
                    )

    # ---- 8 ---------------------------------------------------------------
    def _weights(self) -> dict[str, float]:
        p = set(self.brief.priorities)
        if "price" in p and not p & {"rating", "quality"}:
            return {"price": 0.5, "rating": 0.25, "evidence": 0.15, "stock": 0.1}
        if p & {"rating", "quality"} and "price" not in p:
            return {"price": 0.2, "rating": 0.5, "evidence": 0.2, "stock": 0.1}
        return {"price": 0.35, "rating": 0.35, "evidence": 0.2, "stock": 0.1}

    def _rank(self, offers: list[Offer]) -> None:
        w = self._weights()
        prices = [
            price
            for o in offers
            if (price := _as_number(o.price.value)) is not None and price
        ]
        lo = min(prices) if prices else None
        for o in offers:
            # proportional: the cheapest scores 1.0, one that costs 2% more scores ~0.98 (not 0)
            price = _as_number(o.price.value)
            rating = _as_number(o.rating.value)
            review_count = _as_number(o.review_count.value) or 0
            price_part = (lo / price) if (price and lo) else 0.0
            rating_part = (
                _BAYESIAN_RATING(rating, review_count) / 5
                if rating
                else 0.0
            )
            evidence_part = {VERIFIED: 1.0, REPORTED: 0.5}.get(o.price.status, 0.0)
            if o.link.status == VERIFIED:
                evidence_part = min(1.0, evidence_part + 0.0)
            stock_part = 1.0 if o.availability.value == "in_stock" else 0.5
            raw = (w["price"] * price_part + w["rating"] * rating_part
                   + w["evidence"] * evidence_part + w["stock"] * stock_part)
            if o.match_type == "alternative":
                raw *= 0.85
            o.score = round(100 * raw, 1)
            score_parts: dict[str, object] = {
                "price": round(price_part, 2),
                "rating": round(rating_part, 2),
                "evidence": round(evidence_part, 2),
                "stock": round(stock_part, 2),
                "weights": w,
                "alternative_penalty": o.match_type == "alternative",
            }
            o.score_parts = cast(dict[str, float], score_parts)
        offers.sort(key=lambda o: (-(o.score or 0), o.price.value if o.price.value else 9e12))
        self._pros_cons(offers)

    def _pros_cons(self, offers: list[Offer]) -> None:
        for o in offers:
            price = _as_number(o.price.value)
            peers = [
                peer_price
                for p in offers
                if p is not o
                and same_product(tokenize(o.title), tokenize(p.title))
                and (peer_price := _as_number(p.price.value)) is not None
                and peer_price
            ]
            if price and peers:
                m = median(peers + [price])
                diff = (m - price) / m
                if diff >= 0.05:
                    o.pros.append(f"{diff:.0%} below the median price of this product across the stores checked.")
                elif diff <= -0.05:
                    o.cons.append(f"{-diff:.0%} above the median price of this product across the stores checked.")
            rating = _as_number(o.rating.value)
            review_count = _as_number(o.review_count.value) or 0
            if rating and review_count >= 100 and rating >= 4.2:
                o.pros.append(f"Rated {rating:g}/5 from {int(review_count):,} reviews.")
            elif rating and rating < 3.8:
                o.cons.append(f"Low rating: {rating:g}/5" + (f" from {int(review_count):,} reviews." if review_count else "."))
            elif rating and review_count < 20:
                o.cons.append("Rating rests on very few reviews.")
            if not rating:
                o.cons.append("No customer rating was available.")
            if o.price.status == VERIFIED:
                o.pros.append("Price confirmed on the retailer's own page.")
            elif o.price.status == REPORTED:
                o.cons.append("Price comes from a search listing and was not confirmed on the retailer's page.")
            if o.availability.status == VERIFIED and o.availability.value == "in_stock":
                o.pros.append("Retailer page states it is in stock.")
            elif o.availability.status != VERIFIED:
                o.cons.append("Stock status could not be confirmed.")
            if o.match_type == "alternative":
                o.cons.append("Not an exact match for what you asked for; shown as a close alternative.")
            if o.delivery.value:
                o.pros.append(f"Delivery info (estimate, not retailer-confirmed): {o.delivery.value}.")

    def _group(self, offers: list[Offer]) -> None:
        groups: list[_OfferGroup] = []
        for o in sorted(
            offers, key=lambda x: -(_as_number(x.review_count.value) or 0)
        ):
            tokens = tokenize(o.title)
            for g in groups:
                if same_product(g["tokens"], tokens):
                    g["members"].append(o)
                    break
            else:
                groups.append({"tokens": tokens, "title": o.title, "members": [o]})
        out: list[_VerificationGroup] = []
        for g in groups:
            members = sorted(
                g["members"],
                key=lambda x: _as_number(x.price.value) or 9e12,
            )
            out.append({"title": g["title"], "offer_ids": [m.offer_id for m in members],
                        "platforms": sorted({m.platform_label for m in members}),
                        "lowest_offer_id": members[0].offer_id if members[0].price.value else None})
        out.sort(key=lambda g: (-len(g["platforms"]), g["title"]))
        self.result.groups = out

    def _recommend(self, offers: list[Offer]) -> None:
        rec: dict[str, dict[str, str]] = {}
        if not offers:
            return
        usable = [
            o
            for o in offers
            if (price := _as_number(o.price.value)) is not None and price
        ]
        if usable:
            best = max(usable, key=lambda o: o.score or 0)
            rec["best_overall"] = {"offer_id": best.offer_id, "reason": self._why(best)}
            cheapest = min(usable, key=lambda o: _as_number(o.price.value) or 0)
            cheapest_price = _as_number(cheapest.price.value)
            if cheapest_price is not None:
                rec["lowest_price"] = {
                    "offer_id": cheapest.offer_id,
                    "reason": f"Lowest price found ({cheapest.currency} {cheapest_price:g} on {cheapest.platform_label}), "
                              f"{'confirmed on the retailer page' if cheapest.price.status == VERIFIED else 'reported by search only and not confirmed at source'}."}
        rated: list[Offer] = []
        for offer in offers:
            rating = _as_number(offer.rating.value)
            reviews = _as_number(offer.review_count.value) or 0
            if rating and reviews >= 20:
                rated.append(offer)
        if rated:
            top = max(
                rated,
                key=lambda o: _BAYESIAN_RATING(
                    _as_number(o.rating.value),
                    _as_number(o.review_count.value) or 0,
                ),
            )
            top_rating = _as_number(top.rating.value)
            if top_rating is not None:
                rec["top_rated"] = {
                    "offer_id": top.offer_id,
                    "reason": f"Highest review-weighted rating: {top_rating:g}/5 from {int(_as_number(top.review_count.value) or 0):,} reviews on {top.platform_label}."}
        self.result.recommendations = rec

    def _why(self, o: Offer) -> str:
        w = self._weights()
        weights = ", ".join(f"{k} {int(v * 100)}%" for k, v in w.items())
        parts: list[str] = []
        price = _as_number(o.price.value)
        rating = _as_number(o.rating.value)
        if price:
            parts.append(f"{o.currency} {price:g}")
        if rating:
            parts.append(f"rated {rating:g}/5")
        parts.append("price confirmed at source" if o.price.status == VERIFIED else "price from search listing")
        return (f"Highest overall score ({o.score}/100) on {o.platform_label}: {', '.join(parts)}. "
                f"Score weights used: {weights}.")
