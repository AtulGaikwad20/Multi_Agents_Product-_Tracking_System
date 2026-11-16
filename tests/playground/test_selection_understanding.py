import pytest

from app.playground.agents import build_agent
from app.playground.capabilities import PROFILE_BY_KEY, PROFILES, select_agents
from app.playground.understanding import SYSTEM, understand, understand_with_rules


def keys(tags, requested=None):
    sel, _ = select_agents(tags, requested)
    return {p.key for p, _ in sel}


def test_every_original_platform_name_is_retained():
    labels = {p.label for p in PROFILES}
    assert {"Amazon", "Flipkart", "Meesho", "Blinkit", "Myntra", "AJIO", "Nykaa", "FirstCry", "Snapdeal", "H&M",
            "Shopify"} <= labels
    assert "Internet agent" in labels


def test_internet_agent_is_always_on():
    for tags in (["apparel"], ["audio"], ["general"], ["pets"]):
        assert "internet" in keys(tags)


def test_electronics_involve_croma_but_not_boat_or_fashion_sites():
    k = keys(["audio", "electronics"])
    assert {"croma", "amazon", "flipkart", "internet"} <= k
    assert not {"boat", "myntra", "ajio", "firstcry", "pepperfry"} & k


def test_fashion_involves_fashion_platforms_not_electronics_ones():
    k = keys(["apparel"])
    assert {"myntra", "ajio", "hm", "amazon", "flipkart"} <= k and not {"boat", "croma", "pepperfry"} & k
    assert "firstcry" not in k                         # adult clothing
    assert "firstcry" in keys(["apparel", "baby_kids"])


def test_furniture_involves_furniture_and_general_sites_only():
    k = keys(["home_furniture"])
    assert {"pepperfry", "amazon", "flipkart"} <= k and not {"myntra", "boat", "blinkit", "firstcry"} & k


def test_blinkit_follows_its_real_coverage():
    assert "blinkit" in keys(["grocery"]) and "blinkit" in keys(["household_essentials"])
    assert "blinkit" not in keys(["home_furniture"]) and "blinkit" not in keys(["apparel"])


def test_user_can_request_a_platform_explicitly():
    assert "croma" in keys(["home_furniture"], requested=["croma"])


def test_skipped_agents_get_a_reason():
    _, skipped = select_agents(["home_furniture"])
    assert skipped and all(reason for _, reason in skipped)


@pytest.mark.parametrize("text,tag", [("safety pins", "sewing_craft"), ("steel almirah", "home_furniture"),
                                      ("boAt Airdopes earbuds", "audio"), ("men's cotton shirt", "apparel"),
                                      ("front load washing machine", "appliances_large"), ("lipstick", "beauty")])
def test_fallback_classifier_covers_varied_products(text, tag):
    assert tag in understand_with_rules(text).tags


def test_unknown_product_falls_back_to_general_not_an_error():
    b = understand_with_rules("zorblax gizmo")
    assert b.tags == ["general"]


def test_price_and_platform_parsing():
    b = understand_with_rules("cotton t-shirt between 300 and 600 rupees on myntra")
    assert (b.min_price, b.max_price, b.platforms_requested, b.product) == (300, 600, ["myntra"], "cotton t-shirt")
    assert understand_with_rules("earphones under 2k").max_price == 2000


def test_brand_named_like_platform_is_kept_in_product():
    b = understand_with_rules("boAt earbuds under 2k")
    assert "boAt" in b.product and b.platforms_requested == []


def test_boat_store_is_searchable_by_internet_agent():
    internet = build_agent(PROFILE_BY_KEY["internet"])
    hit = type("WebHit", (), {
        "domain": "boat-lifestyle.com",
        "url": "https://www.boat-lifestyle.com/products/airdopes",
    })()

    assert "boat" not in PROFILE_BY_KEY
    assert internet.accepts_web_hit(hit)


def test_non_english_without_claude_is_flagged_honestly():
    b = understand_with_rules("स्टील की अलमारी", "hi-IN")
    assert b.language == "hi" and "language model" in b.notice


def test_location_relevance_detected():
    assert understand_with_rules("sofa near me").location_relevant
    assert not understand_with_rules("sofa").location_relevant


class LlmStub:
    available = True

    def __init__(self, data):
        self.data = data
        self.calls = []

    def json(self, system, user, **kwargs):
        self.calls.append((system, user))
        return self.data


def test_llm_prompt_repairs_obvious_typos_and_preserves_search_intent():
    text = "i want a moblie with good camra and long bettry under 20 thausand"
    llm = LlmStub({
        "language": "en",
        "product": "smartphone with a good camera and long battery life",
        "tags": ["mobiles", "electronics"],
        "max_price": 20000,
        "must_have": ["good camera", "long battery life"],
    })

    brief = understand(text, llm)

    assert brief.product == "smartphone with a good camera and long battery life"
    assert brief.tags == ["mobiles", "electronics"]
    assert brief.max_price == 20000
    assert brief.must_have == ["good camera", "long battery life"]
    assert llm.calls == [(SYSTEM, text)]
    assert "Silently correct obvious spelling mistakes" in SYSTEM
    assert "do not \"correct\" unusual names" in SYSTEM


def test_llm_brief_is_sanitised_and_overrides_rules():
    b = understand("सेफ्टी पिन 100 रुपये से कम", LlmStub({"language": "hi", "product": "safety pins", "tags": ["sewing_craft", "bogus_tag"],
                                                   "max_price": "100", "platforms_requested": ["blinkit", "nonsense"],
                                                   "location_relevant": True, "location_reason": "…"}), "en-IN")
    assert (b.language, b.product, b.tags, b.max_price, b.platforms_requested, b.understood_by) == \
           ("hi", "safety pins", ["sewing_craft"], 100.0, ["blinkit"], "llm")


def test_llm_garbage_falls_back_to_rules():
    assert understand("sofa", LlmStub({"nope": 1})).understood_by == "rules"
