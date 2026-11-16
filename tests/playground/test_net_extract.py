import pytest
import requests

from app.playground.extract import extract_product, looks_like_shopify
from app.playground.net import BLOCKED_CHALLENGE, BLOCKED_ROBOTS, OK, UNSAFE_URL, SafeFetcher, check_url_safety
from tests.playground.fakes import product_page


def public_resolver(host, port, proto=0):
    return [(2, 1, 6, "", ("93.184.216.34", port))]


@pytest.mark.parametrize("url", ["http://127.0.0.1/", "http://localhost/", "http://169.254.169.254/latest/meta-data",
                                 "http://10.0.0.5/", "http://[::1]/", "ftp://example.com/", "http://example.com:8080/",
                                 "http://user:pw@example.com/", "file:///etc/passwd"])
def test_unsafe_urls_are_refused(url):
    assert check_url_safety(url)[0] is False


def test_public_url_is_allowed():
    assert check_url_safety("https://example.com/x", public_resolver)[0] is True


def test_hostname_resolving_to_private_address_is_refused():
    private = lambda h, p, proto=0: [(2, 1, 6, "", ("10.1.2.3", p))]
    assert check_url_safety("https://evil.example/", private) == (False, "host resolves to a non-public address")


class Resp:
    def __init__(self, status=200, text="", headers=None, location=None):
        self.status_code, self._text = status, text
        self.headers = {"Content-Type": "text/html", **(headers or {})}
        if location:
            self.headers["Location"] = location
        self.encoding = "utf-8"

    @property
    def text(self):
        return self._text

    def iter_content(self, n):
        yield self._text.encode()

    def close(self):
        pass


class Session:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def get(self, url, **kw):
        self.calls.append(url)
        r = self.routes.get(url)
        if isinstance(r, Exception):
            raise r
        return r or Resp(404)


def fetcher(routes):
    return SafeFetcher("TestBot/1", session=Session(routes), resolver=public_resolver, min_host_interval=0)


def test_robots_disallow_stops_the_fetch():
    f = fetcher({"https://shop.example/robots.txt": Resp(200, "User-agent: *\nDisallow: /p/"),
                 "https://shop.example/p/1": Resp(200, "<html>hi</html>")})
    r = f.fetch("https://shop.example/p/1")
    assert r.status == BLOCKED_ROBOTS
    assert "https://shop.example/p/1" not in f.session.calls          # never requested


def test_missing_robots_allows_and_page_is_read():
    f = fetcher({"https://shop.example/p/1": Resp(200, product_page("X", 10))})
    r = f.fetch("https://shop.example/p/1")
    assert r.status == OK and "ld+json" in r.text


def test_robots_server_error_is_treated_conservatively():
    f = fetcher({"https://shop.example/robots.txt": Resp(503), "https://shop.example/p/1": Resp(200, "x")})
    assert f.fetch("https://shop.example/p/1").status == BLOCKED_ROBOTS


@pytest.mark.parametrize("status", [403, 429, 503])
def test_access_refusals_are_reported_not_bypassed(status):
    f = fetcher({"https://shop.example/p/1": Resp(status)})
    r = f.fetch("https://shop.example/p/1")
    assert r.status == BLOCKED_CHALLENGE and len(f.session.calls) == 2   # robots + one attempt, no retries


def test_captcha_page_is_recognised():
    f = fetcher({"https://shop.example/p/1": Resp(200, "<html>Are you a robot? Solve the CAPTCHA</html>")})
    assert f.fetch("https://shop.example/p/1").status == BLOCKED_CHALLENGE


def test_redirect_to_private_address_is_blocked():
    def resolver(host, port, proto=0):
        return [(2, 1, 6, "", ("10.0.0.1" if host == "internal.example" else "93.184.216.34", port))]
    f = SafeFetcher("T/1", session=Session({"https://shop.example/p": Resp(302, location="https://internal.example/admin")}),
                    resolver=resolver, min_host_interval=0)
    assert f.fetch("https://shop.example/p").status == UNSAFE_URL


def test_network_error_is_reported():
    f = fetcher({"https://shop.example/p": requests.ConnectionError("boom")})
    assert f.fetch("https://shop.example/p").status == "network_error"


# ---- extraction
def test_extracts_price_availability_rating():
    p = extract_product(product_page("Pin Pack", 49, availability="OutOfStock", rating=4.3, reviews=120, brand="Acme"))
    assert (p.name, p.price, p.currency, p.availability, p.rating, p.review_count, p.brand) == \
           ("Pin Pack", 49.0, "INR", "out_of_stock", 4.3, 120, "Acme")


def test_page_without_product_data_returns_none():
    assert extract_product("<html><body>Nothing here</body></html>") is None


def test_graph_and_aggregate_offer_are_understood():
    html = '''<script type="application/ld+json">{"@graph":[{"@type":"WebSite"},{"@type":"Product","name":"Sofa",
      "offers":{"@type":"AggregateOffer","lowPrice":"15999","priceCurrency":"INR"}}]}</script>'''
    p = extract_product(html)
    assert p.name == "Sofa" and p.price == 15999.0


def test_rating_on_other_scale_is_normalised_and_invalid_is_ignored():
    html = '''<script type="application/ld+json">{"@type":"Product","name":"A","aggregateRating":{"ratingValue":"8","bestRating":"10","reviewCount":"5"}}</script>'''
    assert extract_product(html).rating == 4.0
    bad = '''<script type="application/ld+json">{"@type":"Product","name":"A","aggregateRating":{"ratingValue":"9","reviewCount":"5"}}</script>'''
    assert extract_product(bad).rating is None


def test_malformed_json_ld_is_skipped():
    assert extract_product('<script type="application/ld+json">{not json</script>') is None


def test_shopify_detection():
    assert looks_like_shopify(product_page("A", 1, shopify=True)) and not looks_like_shopify(product_page("A", 1))
