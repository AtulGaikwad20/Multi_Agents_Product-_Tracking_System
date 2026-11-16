import threading
from collections.abc import Callable, Mapping
from typing import Any, Protocol, cast

import pytest

from app.playground.search import BraveSearch, SearchUnavailable, SerperSearch, _price, build_search  # pyright: ignore[reportPrivateUsage]


class R:
    def __init__(self, status: int = 200, data: dict[str, Any] | None = None) -> None:
        self.status_code: int = status
        self._d: dict[str, Any] = data or {}

    def json(self) -> object:
        return self._d


class S:
    def __init__(self, resp: R) -> None:
        self.resp: R = resp
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def request(self, method: str, url: str, **kw: Any) -> R:
        self.calls.append((method, url, kw))
        return self.resp


class _Llm(Protocol):
    available: bool

    def json(self, system: str, user: str) -> object: ...

    def text(self, system: str, user: str) -> str | None: ...


@pytest.mark.parametrize("v,e", [("₹1,299.00", 1299.0), ("Rs. 499", 499.0), (250, 250.0), ("free", None), (None, None)])
def test_price_parsing(v: str | int | None, e: float | None) -> None:
    assert _price(v) == e


def test_serper_parses_shopping_and_web_and_sends_key_as_header_only() -> None:
    s = S(R(data={"shopping": [{"title": "boAt Airdopes 141", "source": "Amazon.in", "price": "₹1,299", "link": "https://www.amazon.in/dp/1",
                                "imageUrl": "https://i/1.png", "rating": 4.1, "ratingCount": 500}, {"title": ""}],
                  "organic": [{"title": "T", "link": "https://boat-lifestyle.com/p", "snippet": "x"}]}))
    p = SerperSearch("SECRET", session=s)
    hit = p.shopping("airdopes")[0]
    assert (hit.title, hit.merchant, hit.price, hit.rating, hit.review_count, hit.google_link) == ("boAt Airdopes 141", "Amazon.in", 1299.0, 4.1, 500, False)
    assert p.web("x")[0].domain == "boat-lifestyle.com"
    method, url, kw = s.calls[0]
    assert method == "POST"
    assert kw["headers"]["X-API-KEY"] == "SECRET" and "SECRET" not in url and "SECRET" not in str(kw.get("json"))


def test_brave_is_web_only() -> None:
    s = S(R(data={"web": {"results": [{"title": "T", "url": "https://croma.com/p", "description": "d"}]}}))
    p = BraveSearch("K", session=s)
    assert p.shopping("x") == [] and p.web("x")[0].url == "https://croma.com/p"


@pytest.mark.parametrize("status,frag", [(401, "rejected"), (429, "quota"), (500, "HTTP 500")])
def test_provider_errors_are_friendly(status: int, frag: str) -> None:
    with pytest.raises(SearchUnavailable) as e:
        SerperSearch("K", session=S(R(status))).web("x")
    assert frag in e.value.user_message


def test_missing_key_message_names_the_right_variable() -> None:
    with pytest.raises(SearchUnavailable) as e:
        BraveSearch(None).web("x")
    assert "BRAVE_API_KEY" in e.value.user_message


def test_provider_selection() -> None:
    assert isinstance(build_search({"SEARCH_PROVIDER": "serper", "SERPER_API_KEY": "k"}), SerperSearch)
    assert isinstance(build_search({"SEARCH_PROVIDER": "brave"}), BraveSearch)
    assert build_search({}).name == "serpapi"


def test_searxng_parses_results_and_needs_no_key() -> None:
    from app.playground.search import SearxngSearch
    s = S(R(data={"results": [{"title": "Pins", "url": "https://shop.in/p", "content": "c"}, {"title": "bad", "url": "javascript:x"}]}))
    p = SearxngSearch("http://localhost:8080/", session=s)
    assert p.configured and p.shopping("x") == [] and [h.url for h in p.web("safety pins")] == ["https://shop.in/p"]
    assert s.calls[0][1] == "http://localhost:8080/search" and s.calls[0][2]["params"]["format"] == "json"
    assert not SearxngSearch("").configured


def test_google_cse_needs_both_key_and_engine_id() -> None:
    from app.playground.search import GoogleCseSearch
    assert not GoogleCseSearch("k", None).configured
    with pytest.raises(SearchUnavailable) as e:
        GoogleCseSearch("k", None).web("x")
    assert "GOOGLE_CSE_ID" in e.value.user_message
    s = S(R(data={"items": [{"title": "T", "link": "https://a.in/p", "snippet": "s"}]}))
    assert GoogleCseSearch("k", "cx1", session=s).web("x")[0].domain == "a.in"


def test_zenserp_tolerant_parsing_and_header_auth() -> None:
    from app.playground.search import ZenserpSearch
    s = S(R(data={"shopping_results": [{"title": "boAt Airdopes", "source": "Flipkart", "price": "₹1,099", "link": "https://flipkart.com/p", "rating": 4.2},
                                       {"title": "plain web page", "link": "https://blog.example/x"}],
                  "organic": [{"title": "T", "url": "https://boat-lifestyle.com/p", "description": "d"}]}))
    p = ZenserpSearch("SECRET", session=s)
    hits = p.shopping("airdopes")
    assert [(h.merchant, h.price) for h in hits] == [("Flipkart", 1099.0)]        # non-shopping rows are skipped
    assert p.web("x")[0].url == "https://boat-lifestyle.com/p"
    assert s.calls[0][2]["headers"]["apikey"] == "SECRET" and "SECRET" not in str(s.calls[0][2].get("params"))
    assert build_search({"SEARCH_PROVIDER": "zenserp"}).name == "zenserp"


def test_openai_compatible_llm_parses_reply_and_json_and_fails_soft() -> None:
    import app.playground.llm as llm_module
    from app.playground.llm import OpenAICompatLlm

    build_llm = cast(
        Callable[[Mapping[str, str | None]], _Llm],
        getattr(llm_module, "build_llm"),
    )

    class Sess:
        def __init__(self, resp: R) -> None:
            self.resp: R = resp
            self.calls: list[tuple[str, dict[str, Any]]] = []

        def post(self, url: str, **kw: Any) -> R:
            self.calls.append((url, kw))
            return self.resp

    ok = R(data={"choices": [{"message": {"content": '```json\n{"product": "safety pins"}\n```'}}]})
    sess = Sess(ok)
    llm = cast(_Llm, OpenAICompatLlm("https://api.groq.com/openai/v1/", "KEY", "m", session=sess))
    assert llm.available and llm.json("sys", "x") == {"product": "safety pins"}
    url, kw = sess.calls[0]
    assert url == "https://api.groq.com/openai/v1/chat/completions" and kw["headers"]["Authorization"] == "Bearer KEY"
    assert cast(_Llm, OpenAICompatLlm("http://localhost:11434/v1", None, "llama3.1", session=Sess(ok))).text("s", "u")   # local: no key needed
    assert cast(_Llm, OpenAICompatLlm("https://x", "K", "m", session=Sess(R(429)))).text("s", "u") is None
    assert cast(_Llm, OpenAICompatLlm("https://x", "K", "m", session=Sess(R(data={"nope": 1})))).text("s", "u") is None
    assert not OpenAICompatLlm("", "K", "m").available
    assert isinstance(build_llm({"LLM_PROVIDER": "openai_compat", "LLM_BASE_URL": "http://l/v1", "LLM_MODEL": "m"}), OpenAICompatLlm)
    assert not build_llm({}).available


def test_temporary_gateway_errors_are_retried_then_succeed() -> None:
    from app.playground.search import ZenserpSearch

    class Flaky:
        def __init__(self) -> None:
            self.n = 0

        def request(self, method: str, url: str, **kwargs: Any) -> R:
            _ = method, url, kwargs
            self.n += 1
            return R(502) if self.n < 3 else R(data={"organic": [{"title": "T", "url": "https://a.in/p"}]})

    f = Flaky()
    p = ZenserpSearch("K", session=f)
    p.backoff = 0
    assert p.web("x")[0].url == "https://a.in/p" and f.n == 3


def test_persistent_gateway_error_gives_a_helpful_message_and_timeouts_are_retried() -> None:
    import requests as rq
    from app.playground.search import ZenserpSearch

    class Always502:
        n: int = 0

        def request(self, method: str, url: str, **kwargs: Any) -> R:
            _ = method, url, kwargs
            Always502.n += 1
            return R(502)

    p = ZenserpSearch("K", session=Always502()); p.backoff = 0
    with pytest.raises(SearchUnavailable) as e:
        p.web("x")
    assert "temporary" in e.value.user_message and Always502.n == 3

    class Timeout:
        n: int = 0

        def request(self, method: str, url: str, **kwargs: Any) -> R:
            _ = method, url, kwargs
            Timeout.n += 1
            raise rq.Timeout()

    p = ZenserpSearch("K", session=Timeout()); p.backoff = 0
    with pytest.raises(SearchUnavailable) as e:
        p.web("x")
    assert "timed out" in e.value.user_message and Timeout.n == 3


def test_provider_calls_are_serialised() -> None:
    import time as t

    from app.playground.search import ZenserpSearch
    state: dict[str, int] = {"now": 0, "max": 0}

    class Sess:
        def request(self, method: str, url: str, **kwargs: Any) -> R:
            _ = method, url, kwargs
            state["now"] += 1; state["max"] = max(state["max"], state["now"]); t.sleep(0.05); state["now"] -= 1
            return R(data={"organic": []})

    p = ZenserpSearch("K", session=Sess())
    ts: list[threading.Thread] = [threading.Thread(target=p.web, args=(f"q{i}",)) for i in range(5)]
    [x.start() for x in ts]; [x.join() for x in ts]
    assert state["max"] == 1
