"""Extract product facts from a retailer page's own structured data.

We only read what the page publishes about itself (schema.org JSON-LD, with
OpenGraph / product meta tags as a fallback). If a field is not present it
stays None; nothing is guessed.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup

AVAILABILITY = {
    "instock": "in_stock", "onlineonly": "in_stock", "limitedavailability": "in_stock",
    "instoreonly": "in_stock",
    "outofstock": "out_of_stock", "soldout": "out_of_stock", "discontinued": "out_of_stock",
    "preorder": "preorder", "presale": "preorder", "backorder": "preorder",
}


@dataclass
class PageProduct:
    name: str | None = None
    brand: str | None = None
    price: float | None = None
    currency: str | None = None
    availability: str | None = None
    rating: float | None = None
    review_count: int | None = None
    image: str | None = None
    description: str | None = None
    sku: str | None = None
    seller: str | None = None
    specs: dict = field(default_factory=dict)
    reviews: list = field(default_factory=list)
    has_offer: bool = False
    source: str = "jsonld"


def _num(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"\d[\d,]*(?:\.\d+)?", str(value))
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", ""))
    except ValueError:
        return None


def _text(value):
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, dict):
        return _text(value.get("name") or value.get("@value"))
    if isinstance(value, list) and value:
        return _text(value[0])
    return None


def _image(value):
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, dict):
        return _image(value.get("url") or value.get("contentUrl"))
    if isinstance(value, list):
        for item in value:
            found = _image(item)
            if found:
                return found
    return None


def _types(node):
    kind = node.get("@type")
    if isinstance(kind, str):
        return [kind]
    if isinstance(kind, list):
        return [k for k in kind if isinstance(k, str)]
    return []


def _walk(node):
    if isinstance(node, list):
        for item in node:
            yield from _walk(item)
    elif isinstance(node, dict):
        yield node
        for key in ("@graph", "itemListElement", "hasVariant", "mainEntity"):
            if key in node:
                yield from _walk(node[key])


def _availability(value):
    text = _text(value)
    if not text:
        return None
    key = re.sub(r"[^a-z]", "", text.rsplit("/", 1)[-1].lower())
    return AVAILABILITY.get(key)


def _offer_fields(offers):
    """Return (price, currency, availability, seller, has_offer)."""
    items = []
    for node in _walk(offers):
        if "Offer" in " ".join(_types(node)) or "price" in node or "lowPrice" in node:
            items.append(node)
    price = currency = availability = seller = None
    for offer in items:
        value = _num(offer.get("price"))
        if value is None:
            value = _num(offer.get("lowPrice"))
        if value is None:
            spec = offer.get("priceSpecification")
            for node in _walk(spec) if spec else []:
                value = _num(node.get("price"))
                if value is not None:
                    currency = currency or _text(node.get("priceCurrency"))
                    break
        if value is not None and (price is None or value < price):
            price = value
            currency = _text(offer.get("priceCurrency")) or currency
            seller = _text(offer.get("seller")) or seller
        availability = availability or _availability(offer.get("availability"))
    return price, currency, availability, seller, bool(items)


def _from_product_node(node):
    product = PageProduct()
    product.name = _text(node.get("name"))
    product.brand = _text(node.get("brand"))
    product.description = _text(node.get("description"))
    product.sku = _text(node.get("sku")) or _text(node.get("mpn"))
    product.image = _image(node.get("image"))
    (product.price, product.currency, product.availability,
     product.seller, product.has_offer) = _offer_fields(node.get("offers"))

    rating = node.get("aggregateRating")
    if isinstance(rating, dict):
        value = _num(rating.get("ratingValue"))
        best = _num(rating.get("bestRating")) or 5.0
        if value is not None and 0 < value <= best:
            product.rating = round(value * 5.0 / best, 2) if best != 5.0 else value
        count = _num(rating.get("reviewCount")) or _num(rating.get("ratingCount"))
        if count is not None and count >= 0:
            product.review_count = int(count)

    for prop in node.get("additionalProperty") or []:
        if isinstance(prop, dict):
            name, val = _text(prop.get("name")), _text(prop.get("value"))
            if name and val:
                product.specs[name] = val

    reviews = node.get("review")
    if isinstance(reviews, dict):
        reviews = [reviews]
    for review in reviews or []:
        if not isinstance(review, dict):
            continue
        body = _text(review.get("reviewBody")) or _text(review.get("description"))
        if body:
            rr = review.get("reviewRating")
            product.reviews.append({
                "text": body[:600],
                "rating": _num(rr.get("ratingValue")) if isinstance(rr, dict) else None,
            })
        if len(product.reviews) >= 8:
            break
    return product


def _meta(soup, **attrs):
    tag = soup.find("meta", attrs=attrs)
    return tag.get("content").strip() if tag and tag.get("content") else None


def extract_product(html: str) -> PageProduct | None:
    """Return the best product record found on the page, or None."""
    soup = BeautifulSoup(html, "lxml")
    candidates = []
    for script in soup.find_all("script", type=re.compile(r"ld\+json", re.I)):
        raw = script.string or script.get_text()
        if not raw or not raw.strip():
            continue
        try:
            data = json.loads(raw)
        except ValueError:
            try:
                data = json.loads(re.sub(r",\s*([}\]])", r"\1", raw))
            except ValueError:
                continue
        for node in _walk(data):
            if any(t in ("Product", "ProductGroup", "IndividualProduct") for t in _types(node)):
                candidates.append(_from_product_node(node))

    candidates = [c for c in candidates if c.name]
    if candidates:
        # prefer the record with an offer and the most information
        candidates.sort(key=lambda c: (c.has_offer, c.price is not None, c.rating is not None), reverse=True)
        best = candidates[0]
        best.image = best.image or _meta(soup, property="og:image")
        return best

    # Fallback: OpenGraph product tags
    og_type = (_meta(soup, property="og:type") or "").lower()
    price = _num(_meta(soup, property="product:price:amount") or _meta(soup, property="og:price:amount"))
    if "product" in og_type and price is not None:
        return PageProduct(
            name=_meta(soup, property="og:title"),
            price=price,
            currency=_meta(soup, property="product:price:currency") or _meta(soup, property="og:price:currency"),
            availability=_availability(_meta(soup, property="product:availability")),
            image=_meta(soup, property="og:image"),
            description=_meta(soup, property="og:description"),
            has_offer=True,
            source="opengraph",
        )
    return None


def looks_like_shopify(html: str) -> bool:
    head = html[:200000]
    return "cdn.shopify.com" in head or "Shopify.theme" in head or "myshopify.com" in head
