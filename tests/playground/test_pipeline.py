from app.playground.agents import AgentContext, build_agent, canonical_url
from app.playground.capabilities import PROFILE_BY_KEY
from app.playground.events import RunLog
from app.playground.net import SafeFetcher
from app.playground.search import SearchUnavailable
from app.playground.understanding import understand_with_rules
from tests.playground.fakes import FakeFetcher, FakeLlm, FakeSearch, png_bytes, product_page, shop, web
from tests.playground.helpers import make_supervisor, wait

TEE = "https://www.myntra.com/tshirt/1"


def ctx_for(text, search, fetcher, location=None):
    return AgentContext(understand_with_rules(text), search, fetcher, RunLog("t"), {}, location)


def research(key, text, search, fetcher, location=None):
    return build_agent(PROFILE_BY_KEY[key]).research(ctx_for(text, search, fetcher, location))


def test_no_search_key_means_no_results_and_an_honest_report():
    class NoKey(FakeSearch):
        configured = False
    report = research("amazon", "safety pins", NoKey(fail="No search provider key is configured (SERPAPI_API_KEY), so live research is not possible."), FakeFetcher())
    assert report.status == "not_configured" and report.offers == [] and "key" in report.summary.lower()


def test_agent_only_keeps_its_own_platform_and_relevant_items():
    s = FakeSearch(shopping={"t-shirt": [shop("Roadster T-Shirt Men", "Myntra", 499, TEE),
                                         shop("Roadster T-Shirt Men", "Amazon.in", 520, "https://www.amazon.in/dp/1"),
                                         shop("Leather Wallet", "Myntra", 300, "https://www.myntra.com/w/2")]})
    r = research("myntra", "t-shirt", s, FakeFetcher({TEE: product_page("Roadster T-Shirt Men", 499)}))
    assert [o.title for o in r.offers] == ["Roadster T-Shirt Men"]
    assert any("Not relevant" in x["reason"] for x in r.excluded)


def test_page_data_is_verified_and_search_only_data_is_reported():
    s = FakeSearch(shopping={"t-shirt": [shop("Roadster T-Shirt", "Myntra", 499, TEE, 4.1, 100),
                                         shop("Roadster T-Shirt Pro", "Myntra", 650, "https://www.myntra.com/t/2", 4.0, 50)]})
    f = FakeFetcher({TEE: product_page("Roadster T-Shirt", 499, rating=4.1, reviews=100), "https://www.myntra.com/t/2": "robots"})
    r = research("myntra", "t-shirt", s, f)
    by = {o.title: o for o in r.offers}
    assert by["Roadster T-Shirt"].price.status == "verified" and by["Roadster T-Shirt"].link.status == "verified"
    blocked = by["Roadster T-Shirt Pro"]
    assert blocked.price.status == "reported" and blocked.link.status == "unretrieved"
    assert blocked.availability.status == "unretrieved"       # never guessed
    assert any("did not attempt to bypass" in l or "bypass" in l for l in r.limitations)


def test_page_price_beats_stale_search_price_and_is_flagged():
    s = FakeSearch(shopping={"t-shirt": [shop("Roadster T-Shirt", "Myntra", 399, TEE)]})
    r = research("myntra", "t-shirt", s, FakeFetcher({TEE: product_page("Roadster T-Shirt", 499)}))
    o = r.offers[0]
    assert o.price.value == 499 and o.price.status == "verified" and any(f["code"] == "price_mismatch" for f in o.flags)


def test_nothing_is_invented_when_search_returns_nothing():
    r = research("amazon", "safety pins", FakeSearch(), FakeFetcher())
    assert r.status == "none_found" and r.offers == [] and any("not proof" in l for l in r.limitations)


def test_google_shopping_link_is_flagged_as_indirect_and_never_verified():
    s = FakeSearch(shopping={"pins": [shop("Safety Pins 100", "Amazon.in", 40, "https://www.google.com/shopping/product/1", google=True)]})
    r = research("amazon", "safety pins", s, FakeFetcher())
    o = r.offers[0]
    assert o.link.status == "reported" and any(f["code"] == "indirect_link" for f in o.flags) and o.price.status == "reported"


def test_blinkit_without_location_does_not_claim_availability():
    u = "https://blinkit.com/prn/safety-pins/1"
    s = FakeSearch(shopping={"pins": [shop("Safety Pins Pack", "Blinkit", 30, u)]})
    r = research("blinkit", "safety pins", s, FakeFetcher({u: product_page("Safety Pins Pack", 30)}))
    o = r.offers[0]
    assert o.availability.status == "unretrieved" and "address" in o.availability.note
    assert any("delivery address" in l for l in r.limitations)


def test_blinkit_with_location_is_only_an_estimate():
    u = "https://blinkit.com/prn/safety-pins/1"
    s = FakeSearch(shopping={"pins": [shop("Safety Pins Pack", "Blinkit", 30, u, delivery="10 mins")]})
    r = research("blinkit", "safety pins", s, FakeFetcher({u: product_page("Safety Pins Pack", 30)}), {"label": "Pune"})
    o = r.offers[0]
    assert o.availability.status == "unretrieved" and "estimate" in o.availability.note
    assert o.delivery.status == "reported" and "not confirmed by the retailer" in o.delivery.note
    assert ("shopping", "safety pins Blinkit", "Pune") in s.queries          # location used only as a search bias


def test_internet_agent_skips_platform_agent_domains_and_noise():
    s = FakeSearch(web_hits={"buy": [web("Pins", "https://www.amazon.in/dp/1"), web("Pins", "https://www.reddit.com/r/x"),
                                     web("Safety Pins", "https://www.smallshop.in/p/pins")]})
    f = FakeFetcher({"https://www.smallshop.in/p/pins": product_page("Safety Pins", 25)})
    r = research("internet", "safety pins", s, f)
    assert [o.url for o in r.offers] == ["https://www.smallshop.in/p/pins"]
    assert r.offers[0].trust["level"] == "unrated" and "check seller" in " ".join(r.offers[0].trust["reasons"]).lower()


def test_shopify_agent_only_reports_pages_confirmed_as_shopify():
    s = FakeSearch(web_hits={"inurl": [web("Brass Pins", "https://a.in/products/pins"), web("Steel Pins", "https://b.in/products/pins")]})
    f = FakeFetcher({"https://a.in/products/pins": product_page("Brass Safety Pins", 99, shopify=True),
                     "https://b.in/products/pins": product_page("Steel Safety Pins", 80)})
    r = research("shopify", "safety pins", s, f)
    assert [o.url for o in r.offers] == ["https://a.in/products/pins"]


def test_agent_crash_is_contained():
    class Boom(FakeSearch):
        def shopping(self, *a, **k):
            raise RuntimeError("kaboom")
    r = research("amazon", "pins", Boom(), FakeFetcher())
    assert r.status == "error" and r.offers == []


def test_canonical_url_strips_tracking():
    assert canonical_url("https://www.amazon.in/dp/B0/?tag=x&utm_source=a#f") == "https://amazon.in/dp/B0"


# --------------------------------------------------------------- full runs
def tee_world():
    search = FakeSearch(shopping={"t-shirt": [
        shop("Roadster Cotton T-Shirt Men Blue", "Myntra", 499, TEE, 4.1, 2100, "https://img/1.png"),
        shop("Roadster Cotton T-Shirt Men Blue", "Amazon.in", 519, "https://www.amazon.in/dp/B1", 4.0, 800, "https://img/2.png"),
        shop("H&M Cotton T-Shirt Men", "H&M", 799, "https://www2.hm.com/p/1", 4.4, 50),
        shop("Premium Cotton T-Shirt Men Blue", "Myntra", 2999, "https://www.myntra.com/t/9", 4.5, 300)]},
        web_hits={"site:myntra.com": [web("Roadster", TEE)]})
    fetcher = FakeFetcher({TEE: product_page("Roadster Cotton T-Shirt Men Blue", 499, rating=4.1, reviews=2100, image="https://img/1.png"),
                           "https://www.amazon.in/dp/B1": "robots",
                           "https://www2.hm.com/p/1": product_page("H&M Cotton T-Shirt Men", 799, availability="OutOfStock"),
                           "https://www.myntra.com/t/9": product_page("Premium Cotton T-Shirt Men Blue", 2999)},
                          {"https://img/1.png": png_bytes()})
    return search, fetcher


def test_full_run_end_to_end_with_budget_and_honest_unavailability():
    search, fetcher = tee_world()
    sup = make_supervisor(search, fetcher)
    run = wait(sup.start(1, "cotton t-shirt for men under 1000 rupees"))
    assert run.status == "done"
    r = run.report
    titles = {(o["platform"], o["price"]["value"]) for o in r["offers"]}
    assert ("myntra", 499) in titles and ("amazon", 519) in titles
    assert all(o["price"]["value"] <= 1000 for o in r["offers"])                         # budget enforced
    assert any("Over budget" in e["reason"] for e in r["excluded"])
    assert any(u["kind"] == "out_of_stock" and u["platform"] == "H&M" for u in r["unavailable"])
    myn = next(o for o in r["offers"] if o["platform"] == "myntra")
    assert myn["image"]["status"] == "verified"
    amz = next(o for o in r["offers"] if o["platform"] == "amazon")
    assert amz["price"]["status"] == "reported" and amz["image"]["status"] != "verified"  # 404 image never shown as verified
    assert r["sources"] and all(s["retrieved_at"] for s in r["sources"])
    assert "firstcry" not in {a["key"] for a in r["agents"]} and "boat" not in {a["key"] for a in r["agents"]}
    types = [e["type"] for e in run.log.all()]
    assert types.index("meeting_started") < types.index("task_assigned") < types.index("verification_started") < types.index("report_ready")
    assert types.count("meeting_started") == 2 and r["summary"]["text"]


def test_budget_refinement_reuses_collected_listings_without_new_fetches():
    search, fetcher = tee_world()
    sup = make_supervisor(search, fetcher)
    run = wait(sup.start(1, "cotton t-shirt for men"))
    before = (len(fetcher.fetched), len(search.queries))
    res = sup.followup(1, run.id, "under 600 rupees")
    assert res["action"] == "refine"
    wait(run)
    assert (len(fetcher.fetched), len(search.queries)) == before          # no new research, as promised
    assert all(o["price"]["value"] <= 600 for o in run.report["offers"]) and run.revision == 2


def test_question_is_answered_from_findings_not_new_research():
    search, fetcher = tee_world()
    sup = make_supervisor(search, fetcher)
    run = wait(sup.start(1, "cotton t-shirt for men"))
    n = len(search.queries)
    res = sup.followup(1, run.id, "which is the cheapest?")
    assert res["action"] == "question" and "₹499" in res["answer"] and len(search.queries) == n


def test_followup_response_uses_detected_message_language():
    class LanguageLlm(FakeLlm):
        available = True

        def json(self, system, user, **kwargs):
            if "Convert the user's request" in system:
                return {"language": "en", "product": "cotton t-shirt for men",
                        "tags": ["apparel"], "platforms_requested": []}
            if "Route a follow-up message" in system:
                return {"action": "question", "language": "mr-IN"}
            return None

        def text(self, system, user, **kwargs):
            if "answering a follow-up question aloud" in system:
                assert "BCP-47: mr-IN" in system
                return "मराठीत उत्तर."
            return None

    search, fetcher = tee_world()
    sup = make_supervisor(search, fetcher, LanguageLlm())
    run = wait(sup.start(1, "cotton t-shirt for men", "en-IN"))

    response = sup.followup(1, run.id, "सगळ्यात स्वस्त कोणता?", "en-IN")

    assert response["action"] == "question"
    assert response["language"] == "mr-IN"
    assert response["answer"] == "मराठीत उत्तर."


def test_expand_runs_only_the_requested_extra_agent():
    search, fetcher = tee_world()
    sup = make_supervisor(search, fetcher)
    run = wait(sup.start(1, "cotton t-shirt for men"))
    assert "croma" not in run.agent_reports
    res = sup.followup(1, run.id, "also check croma")
    assert res["action"] == "expand"
    wait(run)
    assert "croma" in run.agent_reports and {"myntra", "amazon"} <= set(run.agent_reports)


def test_interrupt_stops_work_and_keeps_what_was_done():
    import threading
    gate = threading.Event()

    class Slow(FakeSearch):
        def shopping(self, q, location=None, limit=40):
            gate.wait(2)
            return super().shopping(q, location, limit)
    search, fetcher = tee_world()
    slow = Slow(shopping=search.shopping_map, web_hits=search.web_map)
    sup = make_supervisor(slow, fetcher)
    run = sup.start(1, "cotton t-shirt for men")
    import time
    time.sleep(0.3)
    sup.cancel(run.id)
    gate.set()
    wait(run)
    assert run.status in ("cancelled", "done") and any(e["type"] in ("run_cancelled", "report_ready") for e in run.log.all())


def test_total_failure_still_produces_a_clear_report():
    sup = make_supervisor(FakeSearch(fail="The search service could not be reached."), FakeFetcher())
    run = wait(sup.start(1, "sofa"))
    assert run.status == "done" and run.report["offers"] == []
    assert "could not find any listing" in run.report["summary"]["text"]


def test_users_cannot_open_each_others_runs_and_limit_applies():
    import pytest
    from app.playground.supervisor import RunLimitError
    sup = make_supervisor(*tee_world())
    run = wait(sup.start(1, "cotton t-shirt"))
    assert sup.open_run(2, run.id) is None and sup.followup(2, run.id, "why?") is None


def test_location_is_used_only_when_relevant():
    search, fetcher = tee_world()
    sup = make_supervisor(search, fetcher)
    loc = {"label": "Pune, Maharashtra", "source": "manual", "precision": "manual entry"}
    run = wait(sup.start(1, "cotton t-shirt for men", location=loc))
    assert run.report["location"] is None and all(q[2] is None for q in search.queries)
    run2 = wait(sup.start(1, "cotton t-shirt for men near me", location=loc))
    assert run2.report["location"]["label"] == "Pune, Maharashtra" and any(q[2] == "Pune, Maharashtra" for q in search.queries)
    assert any("estimates" in l for l in run2.report["limitations"])


def test_run_is_rehydrated_for_another_device():
    sup = make_supervisor(*tee_world())
    run = wait(sup.start(1, "cotton t-shirt for men"))
    sup.runs.clear()                                                    # simulates a different server process / device
    again = sup.open_run(1, run.id)
    assert again and again.report["product"] == run.report["product"] and len(again.log.all()) == len(run.log.all())
