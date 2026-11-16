"""Relevance helpers built on the project's existing title-matching code."""
from app.services.matching import tokenize

IGNORED: set[str] = {"black", "white", "red", "blue", "green", "yellow", "pink", "grey", "gray", "silver", "gold"}


def relevance_share(query: str, title: str) -> float:
    """Share of meaningful query tokens present in the title (0..1)."""
    wanted = [t for t in set(tokenize(query)) if t not in IGNORED] or list(set(tokenize(query)))
    if not wanted:
        return 1.0
    have = set(tokenize(title))
    # plural-insensitive match: "pins" ~ "pin"
    def hit(token: str) -> bool:
        return token in have or token.rstrip("s") in have or (token + "s") in have
    return sum(1 for t in wanted if hit(t)) / len(wanted)
