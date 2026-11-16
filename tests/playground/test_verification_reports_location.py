import csv
import io
import json

import pytest

from app.playground import location as loc
from app.playground.events import RunLog
from app.playground.models import Evidence, Offer
from app.playground.reports import export
from app.playground.understanding import understand_with_rules
from app.playground.verification import Verifier
from tests.playground.fakes import FakeFetcher, png_bytes, product_page
from tests.playground.helpers import make_supervisor, wait
from tests.playground.test_pipeline import tee_world


def offer(title, price, platform="amazon", url=None, status="reported", rating=None, reviews=None, img=None, stock=None, brand=None):
    o = Offer(platform, platform.title(), title, url or f"https://www.{platform}.com/{abs(hash(title + str(price)))}", brand=brand, currency="INR")
    o.price = Evidence.verified(price, o.url) if status == "verified" else Evidence.reported(price, o.url)
    if rating:
        o.rating, o.review_count = Evidence.reported(rating, o.url), Evidence.reported(reviews or 0, o.url)
    if img:
        o.image = Evidence.reported(img, o.url)
    if stock:
        o.availability = Evidence.verified(stock, o.url)
    o.offer_id = f"{platform}{abs(hash(title))}"
    o.relevance = 1.0
    return o


def verify(offers, text="steel almirah", fetcher=None):
    return Verifier(understand_with_rules(text), fetcher or FakeFetcher(), RunLog("t")).run(offers)


def test_duplicates_and_impossible_values_are_removed():
    a = offer("Steel Almirah 2 Door", 9000, url="https://www.amazon.com/dp/1")
    b = offer("Steel Almirah 2 Door", 9000, url="https://www.amazon.com/dp/1?tag=x")
    c = offer("Steel Almirah Free", -5, platform="flipkart")
    res = verify([a, b, c])
    assert len(res.accepted) == 1 and {e["reason"] for e in res.excluded} >= {"Duplicate of another listing already included.", "Price value is not a positive number."}


def test_platform_link_must_belong_to_platform():
    bad = offer("Steel Almirah", 9000, platform="amazon", url="https://www.evil.example/amazon")
    assert verify([bad]).accepted == [] 


def test_budget_brand_and_avoid_rules():
    r = verify([offer("Godrej Steel Almirah", 9000, brand="Godrej"), offer("Nilkamal Steel Almirah", 30000, platform="flipkart")],
               "godrej steel almirah under 20000")
    assert [o.platform for o in r.accepted] == ["amazon"]


def test_unconfirmed_requirements_are_flagged_not_hidden():
    brief = understand_with_rules("steel almirah")
    brief.must_have = ["lock"]
    res = Verifier(brief, FakeFetcher(), RunLog("t")).run([offer("Steel Almirah", 9000)])
    assert any(f["code"] == "unconfirmed_requirement" for f in res.accepted[0].flags)


def test_unconfirmed_price_outlier_is_excluded_but_confirmed_one_is_kept():
    peers = [offer("Wooden Dining Table 6 Seater", p, platform=pl) for p, pl in ((20000, "amazon"), (21000, "flipkart"), (22000, "snapdeal"))]
    fake = offer("Wooden Dining Table 6 Seater", 99, platform="meesho", url="https://www.meesho.com/t/1")
    res = verify(peers + [fake], "wooden dining table 6 seater")
    assert fake not in res.accepted and any("could not be confirmed" in e["reason"] for e in res.excluded)

    real = offer("Wooden Dining Table 6 Seater", 99, platform="meesho", url="https://www.meesho.com/t/2")
    f = FakeFetcher({"https://www.meesho.com/t/2": product_page("Wooden Dining Table 6 Seater", 99)})
    res = verify(peers + [real], "wooden dining table 6 seater", f)
    kept = [o for o in res.accepted if o.platform == "meesho"]
    assert kept and kept[0].price.status == "verified" and any(fl["code"] == "price_outlier_confirmed" for fl in kept[0].flags)


def test_images_must_really_load_as_images():
    good = offer("Steel Almirah A", 9000, img="https://img/ok.png")
    bad = offer("Steel Almirah B", 9100, platform="flipkart", img="https://img/missing.png")
    junk = offer("Steel Almirah C", 9200, platform="snapdeal", img="https://img/junk.png")
    f = FakeFetcher(images={"https://img/ok.png": png_bytes(), "https://img/junk.png": b"not an image"})
    verify([good, bad, junk], fetcher=f)
    assert good.image.status == "verified"
    assert bad.image.status == "unretrieved" and junk.image.status == "unretrieved"


def test_recommendations_explain_their_basis_and_prefer_verified():
    a = offer("Steel Almirah", 8000, status="reported", rating=4.5, reviews=400)
    b = offer("Steel Almirah", 8200, platform="flipkart", status="verified", rating=4.4, reviews=380)
    r = verify([a, b])
    rec = r.recommendations
    assert rec["lowest_price"]["offer_id"] == a.offer_id and "not confirmed at source" in rec["lowest_price"]["reason"]
    assert "weights" in rec["best_overall"]["reason"] and rec["best_overall"]["offer_id"] == b.offer_id


def test_pros_and_cons_come_only_from_evidence():
    o = offer("Steel Almirah", 9000, rating=3.2, reviews=500)
    r = verify([o])
    assert any("Low rating" in c for c in o.cons) and not any("Rated" in p for p in o.pros)
    assert any("not confirmed" in c for c in o.cons)


# ---------------------------------------------------------------- reports
@pytest.fixture(scope="module")
def record():
    search, fetcher = tee_world()
    sup = make_supervisor(search, fetcher)
    run = wait(sup.start(1, "cotton t-shirt for men under 1000 rupees"))
    return run.record(), fetcher


def test_pdf_is_a_real_pdf_with_content(record):
    rec, fetcher = record
    data, mime, ext = export(rec, "pdf", fetcher)
    assert data.startswith(b"%PDF") and mime == "application/pdf" and len(data) > 5000


def test_csv_and_json_round_trip_and_csv_is_injection_safe(record):
    rec, _ = record
    rows = list(csv.DictReader(io.StringIO(export(rec, "csv")[0].decode("utf-8-sig"))))
    assert rows and {"platform", "price", "price_status", "url", "retrieved_at"} <= set(rows[0])
    evil = json.loads(json.dumps(rec)); evil["report"]["offers"][0]["title"] = "=HYPERLINK(\"http://x\")"
    assert export(evil, "csv")[0].decode("utf-8-sig").count("'=HYPERLINK") == 1
    assert json.loads(export(rec, "json")[0])["report"]["product"]


def test_html_escapes_untrusted_titles(record):
    rec, _ = record
    evil = json.loads(json.dumps(rec)); evil["report"]["offers"][0]["title"] = "<script>alert(1)</script>"
    html = export(evil, "html")[0].decode()
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;" in html


def test_exports_state_evidence_and_timestamps(record):
    rec, _ = record
    md = export(rec, "md")[0].decode()
    assert "Verified at source" in md and "Reported by search" in md and "retrieved" in md.lower() and "Sources" in md


def test_export_requires_a_finished_report():
    with pytest.raises(ValueError):
        export({"report": None}, "pdf")


# ---------------------------------------------------------------- location
def test_manual_location_needs_no_device_and_extracts_postcode():
    r = loc.from_manual("Kothrud, Pune 411038")
    assert r["postal_code"] == "411038" and r["source"] == "manual" and "Not confirmed" in r["disclaimer"]


def test_manual_location_is_sanitised_and_validated():
    assert "<" not in loc.from_manual("<script>x</script> Pune")["label"]
    with pytest.raises(loc.LocationError):
        loc.from_manual(" ")


def test_device_location_requires_explicit_geocoder_consent_and_discards_coordinates():
    with pytest.raises(loc.LocationError) as e:
        loc.from_device(18.5, 73.8, "approximate", False, {})
    assert "OK" in e.value.user_message

    class S:
        def get(self, url, params=None, **k):
            assert params["zoom"] == 10 and params["lat"] == "18.5000"
            class R:
                status_code = 200
                def json(self): return {"address": {"city": "Pune", "state": "Maharashtra", "country": "India", "suburb": "Kothrud"}}
            return R()
    r = loc.from_device(18.5, 73.8, "approximate", True, {"GEOCODER_URL": "https://geo.example/r"}, session=S())
    assert r["label"] == "Pune, Maharashtra, India" and "18.5" not in json.dumps(r) and "73.8" not in json.dumps(r)


def test_out_of_range_coordinates_rejected():
    with pytest.raises(loc.LocationError):
        loc.from_device(200, 0, "approximate", True, {})


def test_location_expires():
    s = {"pg_location": {**loc.from_manual("Pune"), "expires_at": "2000-01-01T00:00:00+00:00"}}
    assert loc.current(s) is None and "pg_location" not in s
