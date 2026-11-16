from datetime import datetime, timedelta

import pytest
import requests

from app.services.formatting import format_inr, friendly_time, title_from_url
from app.services.matching import (
    filter_relevant,
    group_listings,
    same_product,
    split_price_outliers,
    tokenize,
)
from app.services.scoring import bayesian_rating, rank_listings
from dataclasses import dataclass


@dataclass
class Listing:
    title: str
    merchant: str = ""
    price: float | None = None
    rating: float | None = None
    review_count: int | None = None
    platform: str | None = None
    score: float | None = None


def listing(title="Item", price=100.0, platform="amazon", rating=4.0, reviews=100, merchant="Amazon.in"):
    return Listing(title=title, price=price, platform=platform, rating=rating, review_count=reviews, merchant=merchant)


@pytest.mark.parametrize(
    "value,expected",
    [(None, "Price unavailable"), (999, "₹999"), (1299, "₹1,299"), (129999, "₹1,29,999"), (2500000, "₹25,00,000"), (1299.5, "₹1,299.50")],
)
def test_format_inr(value, expected):
    assert format_inr(value) == expected


def test_friendly_time():
    now = datetime(2026, 10, 16, 18, 30)

    assert friendly_time(datetime(2026, 10, 16, 9, 5), now) == "Today, 9:05 AM"
    assert friendly_time(now - timedelta(days=1), now) == "Yesterday"
    assert friendly_time(datetime(2026, 10, 3, 10, 0), now) == "3 Oct"
    assert friendly_time(datetime(2025, 12, 25, 10, 0), now) == "25 Dec 2025"


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://www.amazon.in/Sony-WH-1000XM5-Wireless-Headphones/dp/B09XS7JWHH", "Sony WH 1000XM5 Wireless Headphones"),
        ("https://www.flipkart.com/apple-iphone-15-blue-128-gb/p/itm6ac6485515ae4", "apple iphone 15 blue 128 gb"),
        ("https://www.myntra.com/shoes/nike/nike-men-black-running-shoes/12345678/buy", "nike men black running shoes"),
        ("https://www.amazon.in/", None),
    ],
)
def test_title_from_url(url, expected):
    assert title_from_url(url) == expected


def test_tokenize_normalises_units():
    assert "128gb" in tokenize("iPhone 15, 128 GB (Black)")


def test_same_product_requires_matching_capacity():
    assert same_product(tokenize("iPhone 15 128 GB Black"), tokenize("Apple iPhone 15 128GB"))
    assert not same_product(tokenize("iPhone 15 128 GB"), tokenize("iPhone 15 256 GB"))


def test_different_models_are_not_the_same_product():
    assert not same_product(tokenize("Nike Revolution Running Shoes"), tokenize("Nike Pegasus Running Shoes"))
    assert not same_product(tokenize("Samsung Galaxy S24 Ultra 256GB"), tokenize("Samsung Galaxy S24 256GB"))
    assert not same_product(tokenize("Apple iPhone 15 Pro 128GB"), tokenize("Apple iPhone 15 128GB"))


def test_colour_gender_and_extra_words_do_not_split_a_product():
    assert same_product(tokenize("Nike Revolution Running Shoes"), tokenize("Nike Revolution 7 Men's Road Running Shoes Black"))
    assert same_product(tokenize("Sony WH-1000XM5 Headphones"), tokenize("Sony WH 1000XM5 Wireless Headphones"))


def test_group_listings_keeps_lowest_price_per_platform():
    items = [
        listing("Sony WH-1000XM5 Headphones", 29990, "amazon", reviews=900),
        listing("Sony WH-1000XM5 Headphones", 28990, "amazon", reviews=300),
        listing("Sony WH 1000XM5 Wireless Headphones", 27990, "flipkart", reviews=500),
        listing("Boat Rockerz 450", 1499, "amazon"),
    ]

    groups = group_listings(items)

    assert len(groups) == 2
    top = groups[0]
    assert [offer.platform for offer in top["offers"]] == ["flipkart", "amazon"]
    assert top["offers"][1].price == 28990


def test_relevance_filter_removes_unrelated_results():
    items = [listing("Nike Air Zoom Running Shoes"), listing("Phone Charger Cable")]

    assert [i.title for i in filter_relevant(items, "nike running shoes")] == ["Nike Air Zoom Running Shoes"]


def test_price_outliers_are_split_out():
    items = [listing(price=p) for p in (30000, 31000, 29000, 32000, 450)]
    kept, dropped = split_price_outliers(items)

    assert len(kept) == 4 and [d.price for d in dropped] == [450]


def test_small_result_sets_keep_everything():
    items = [listing(price=p) for p in (30000, 450)]

    assert split_price_outliers(items) == (items, [])


def test_bayesian_rating_pulls_small_samples_toward_the_mean():
    assert bayesian_rating(5.0, 2, 4.0) < 4.1
    assert bayesian_rating(4.6, 5000, 4.0) > 4.55


def test_ranking_prefers_credible_ratings_over_a_perfect_score_from_two_reviews():
    ranked = rank_listings([
        listing("A", price=1000, rating=5.0, reviews=2),
        listing("B", price=1000, rating=4.6, reviews=5000),
    ])

    assert ranked[0].title == "B"
