"""Test doubles. These exist ONLY in the test suite; the application ships no fake data."""
import io
import json

from PIL import Image

from app.playground.net import (BLOCKED_ROBOTS, HTTP_ERROR, NOT_HTML, OK, FetchResult)
from app.playground.search import SearchUnavailable, ShoppingHit, WebHit, domain_of

NOW = "2026-10-18T10:00:00+00:00"


def shop(title, merchant, price, url, rating=None, reviews=None, image=None, delivery=None, google=False):
    return ShoppingHit(title, merchant, price, "INR", url, rating, reviews, image, delivery, google, NOW)


def web(title, url, snippet=""):
    return WebHit(title, url, snippet, domain_of(url), NOW)


class FakeSearch:
    """Routes queries to canned hit lists by substring match."""
    configured = True

    def __init__(self, shopping=None, web_hits=None, fail=None):
        self.shopping_map = shopping or {}
        self.web_map = web_hits or {}
        self.fail = fail
        self.queries = []

    def shopping(self, query, location=None, limit=40):
        self.queries.append(("shopping", query, location))
        if self.fail:
            raise SearchUnavailable(self.fail)
        out = []
        for needle, hits in self.shopping_map.items():
            if needle.lower() in query.lower():
                out.extend(hits)
        return out

    def web(self, query, num=10, location=None):
        self.queries.append(("web", query, location))
        if self.fail:
            raise SearchUnavailable(self.fail)
        out = []
        for needle, hits in self.web_map.items():
            if needle.lower() in query.lower():
                out.extend(hits)
        return out


def product_page(name, price, currency="INR", availability="InStock", rating=None, reviews=None,
                 image=None, shopify=False, brand=None):
    node = {"@context": "https://schema.org", "@type": "Product", "name": name,
            "offers": {"@type": "Offer", "price": str(price), "priceCurrency": currency,
                       "availability": f"https://schema.org/{availability}"}}
    if brand:
        node["brand"] = {"@type": "Brand", "name": brand}
    if image:
        node["image"] = image
    if rating:
        node["aggregateRating"] = {"ratingValue": str(rating), "reviewCount": str(reviews or 0)}
    extra = '<link href="https://cdn.shopify.com/s/x.css">' if shopify else ""
    return f"<html><head>{extra}<script type='application/ld+json'>{json.dumps(node)}</script></head><body></body></html>"


def png_bytes():
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), (200, 30, 30)).save(buf, "PNG")
    return buf.getvalue()


class FakeFetcher:
    """pages: url -> html | FetchResult status string ('robots','http_error')."""

    def __init__(self, pages=None, images=None):
        self.pages = pages or {}
        self.images = images or {}
        self.fetched = []

    def fetch(self, url, **kwargs):
        self.fetched.append(url)
        page = self.pages.get(url)
        if page is None:
            return FetchResult(HTTP_ERROR, url, http_status=404, detail="HTTP 404", retrieved_at=NOW)
        if page == "robots":
            return FetchResult(BLOCKED_ROBOTS, url, detail="robots.txt does not allow automated access", retrieved_at=NOW)
        return FetchResult(OK, url, final_url=url, http_status=200, text=page, content_type="text/html", retrieved_at=NOW)

    def fetch_image(self, url, max_bytes=2_000_000):
        data = self.images.get(url)
        if data is None:
            return FetchResult(HTTP_ERROR, url, http_status=404, detail="HTTP 404", retrieved_at=NOW)
        return FetchResult(OK, url, final_url=url, http_status=200, content=data, content_type="image/png", retrieved_at=NOW)


class FakeLlm:
    available = False

    def text(self, *a, **k):
        return None

    def json(self, *a, **k):
        return None
