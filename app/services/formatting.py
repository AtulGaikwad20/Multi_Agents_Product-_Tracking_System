import re
from datetime import datetime
from urllib.parse import unquote, urlparse

URL_NOISE: set[str] = {"dp", "p", "product", "products", "buy", "item", "pd", "prn", "prid", "itm", "gp"}


def _indian_grouping(digits: str) -> str:
    if len(digits) <= 3:
        return digits

    head: str = digits[:-3]
    tail: str = digits[-3:]
    groups: list[str] = []

    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]

    if head:
        groups.insert(0, head)

    return ",".join(groups + [tail])


def format_inr(value: int | float | None) -> str:
    if value is None:
        return "Price unavailable"

    if abs(value - round(value)) < 0.005:
        return "₹" + _indian_grouping(str(int(round(value))))

    whole, fraction = f"{value:.2f}".split(".")

    return "₹" + _indian_grouping(whole) + "." + fraction


def format_count(value: int | float | None) -> str:
    if not value:
        return "0"

    return f"{int(value):,}"


def friendly_time(moment: datetime | None, now: datetime | None = None) -> str:
    if not moment:
        return ""

    now = now or datetime.now()
    clock = moment.strftime("%I:%M %p").lstrip("0")
    days = (now.date() - moment.date()).days

    if days == 0:
        return f"Today, {clock}"

    if days == 1:
        return "Yesterday"

    label = f"{moment.day} {moment:%b}"

    if moment.year != now.year:
        label += f" {moment.year}"

    return label


def title_from_url(url: str) -> str | None:
    try:
        path = unquote(urlparse(url).path)
    except ValueError:
        return None

    candidates: list[list[str]] = []

    for segment in path.split("/"):
        words: list[str] = [
            word
            for word in re.split(r"[-_+]", segment)
            if word and word.lower() not in URL_NOISE
        ]

        words = [word for word in words if not re.fullmatch(r"[a-z]*\d{6,}[a-z0-9]*", word, re.I)]

        if len(words) >= 2:
            candidates.append(words)

    if not candidates:
        return None

    best: list[str] = max(candidates, key=len)

    return " ".join(best)
