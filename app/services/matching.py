import re
from statistics import median
from collections.abc import Iterable
from typing import Protocol, TypedDict

STOPWORDS: set[str] = {
    word.lower() for word in {
        "a", "an", "and", "the", "for", "with", "of", "in", "on", "to", "by",
        "new", "latest", "buy", "online", "India", "pack", "combo", "set",
    }
}

DESCRIPTORS: set[str] = {
    "men", "mens", "man", "women", "womens", "woman", "boys", "boy", "girls", "girl",
    "kids", "unisex", "black", "white", "red", "blue", "green", "yellow", "orange",
    "pink", "purple", "brown", "grey", "gray", "beige", "cream", "navy", "maroon",
    "silver", "gold", "wireless", "bluetooth", "wired", "original", "genuine",
    "official", "india", "indian", "color", "colour",
}

QUALIFIERS: set[str] = {
    "pro", "max", "plus", "ultra", "mini", "lite", "fe", "air", "se", "xl", "neo",
    "prime", "edge", "note", "flip", "fold", "gt", "turbo",
}

UNIT_PATTERN: re.Pattern[str] = re.compile(r"(\d+(?:\.\d+)?)\s*(gb|tb|mb|ml|kg|mah|inch|in|w|l|g)\b", re.I)
CAPACITY_PATTERN: re.Pattern[str] = re.compile(r"^\d+(gb|tb)$")


class _ListingLike(Protocol):
    title: str
    price: float | None
    rating: float | None
    review_count: int | None
    platform: str | None


class _ProductGroup(TypedDict):
    tokens: list[str]
    title: str
    members: list[_ListingLike]


class _ListingGroup(TypedDict):
    title: str
    offers: list[_ListingLike]


def tokenize(text: str) -> list[str]:
    text = (text or "").lower().replace("&", " and ")
    text = UNIT_PATTERN.sub(lambda m: m.group(1) + m.group(2), text)
    words = re.findall(r"[a-z0-9]+(?:\.[0-9]+)?", text)

    return [
        word
        for word in words
        if word not in STOPWORDS and (len(word) > 1 or word.isdigit())
    ]


def _jaccard(first: set[str], second: set[str]) -> float:
    if not first or not second:
        return 0.0

    return len(first & second) / len(first | second)


def same_product(first_tokens: Iterable[str], second_tokens: Iterable[str], threshold: float = 0.5) -> bool:
    first, second = set(first_tokens), set(second_tokens)

    if _jaccard(first, second) < threshold:
        return False

    if (first & QUALIFIERS) != (second & QUALIFIERS):
        return False

    only_first = (first - second) - DESCRIPTORS - QUALIFIERS
    only_second = (second - first) - DESCRIPTORS - QUALIFIERS

    if only_first and only_second:
        return False

    capacity_first = {t for t in first if CAPACITY_PATTERN.match(t)}
    capacity_second = {t for t in second if CAPACITY_PATTERN.match(t)}

    if capacity_first and capacity_second and capacity_first.isdisjoint(capacity_second):
        return False

    keys_first = {t for t in first if any(c.isdigit() for c in t)}
    keys_second = {t for t in second if any(c.isdigit() for c in t)}

    if keys_first and keys_second and keys_first.isdisjoint(keys_second):
        return False

    return True


def is_relevant(
    listing: _ListingLike,
    query_tokens: Iterable[str],
    minimum_share: float = 0.6,
) -> bool:
    if not query_tokens:
        return True

    title_tokens = set(tokenize(listing.title))
    matched = sum(1 for token in set(query_tokens) if token in title_tokens)

    return matched / len(set(query_tokens)) >= minimum_share


def filter_relevant(listings: list[_ListingLike], query: str) -> list[_ListingLike]:
    query_tokens = tokenize(query)

    return [item for item in listings if is_relevant(item, query_tokens)]


def split_price_outliers(
    listings: list[_ListingLike],
    floor_ratio: float = 0.3,
    minimum_count: int = 5,
) -> tuple[list[_ListingLike], list[_ListingLike]]:
    priced = [item.price for item in listings if item.price]

    if len(priced) < minimum_count:
        return listings, []

    floor = median(priced) * floor_ratio
    kept: list[_ListingLike] = []
    dropped: list[_ListingLike] = []

    for item in listings:
        if item.price and item.price < floor:
            dropped.append(item)
        else:
            kept.append(item)

    return kept, dropped


def group_listings(listings: list[_ListingLike]) -> list[_ListingGroup]:
    ordered = sorted(
        listings,
        key=lambda item: (-(item.review_count or 0), item.price or float("inf")),
    )

    groups: list[_ProductGroup] = []

    for listing in ordered:
        tokens = tokenize(listing.title)

        for group in groups:
            if same_product(group["tokens"], tokens):
                group["members"].append(listing)
                break
        else:
            groups.append({"tokens": tokens, "title": listing.title, "members": [listing]})

    results: list[_ListingGroup] = []

    for group in groups:
        by_platform: dict[str | None, _ListingLike] = {}

        for member in group["members"]:
            current = by_platform.get(member.platform)

            if current is None or (member.price or float("inf")) < (current.price or float("inf")):
                by_platform[member.platform] = member

        offers = sorted(
            by_platform.values(),
            key=lambda item: item.price if item.price is not None else float("inf"),
        )

        results.append({"title": group["title"], "offers": offers})

    results.sort(
        key=lambda group: (
            -len(group["offers"]),
            group["offers"][0].price if group["offers"][0].price is not None else float("inf"),
        )
    )

    return results
