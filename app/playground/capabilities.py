"""Capability-based agent selection.

Each employee agent declares which product areas ("capability tags") its
platform genuinely sells. The Supervisor classifies a request into tags (any
product, not a fixed list of products) and only agents whose coverage
overlaps are activated. These coverage profiles are a *prior*: the agent still
has to find real evidence on its platform, and reports "not found" when the
platform turns out not to stock the item.
"""
from __future__ import annotations

from dataclasses import dataclass, field

TAGS: list[str] = [
    "mobiles", "computers", "audio", "wearables", "mobile_accessories", "cameras", "gaming",
    "electronics", "appliances_large", "appliances_small", "kitchen", "home_furniture",
    "home_decor", "home_textiles", "household_essentials", "grocery", "stationery",
    "sewing_craft", "tools_hardware", "apparel", "footwear", "fashion_accessories",
    "jewellery", "beauty", "personal_care", "health_wellness", "baby_kids", "toys",
    "sports_fitness", "books_media", "automotive", "pets", "garden", "general",
]

ALL: set[str] = set(TAGS)

_GENERAL_RETAIL: set[str] = ALL - {"general"}


@dataclass
class AgentProfile:
    key: str
    label: str
    role: str
    domains: tuple[str, ...]
    merchant_aliases: tuple[str, ...]
    tags: set[str] = field(default_factory=lambda: set[str]())
    kind: str = "marketplace"       # marketplace | specialist | hyperlocal | independent | internet
    hyperlocal: bool = False         # availability depends on the delivery address
    avatar: str = "#6b7cff"
    note: str = ""


PROFILES: list[AgentProfile] = [
    AgentProfile("amazon", "Amazon", "General marketplace researcher", ("amazon.in", "amazon.com"),
                 ("amazon",), set(_GENERAL_RETAIL), "marketplace", avatar="#f59e0b",
                 note="Broad catalogue across most consumer categories."),
    AgentProfile("flipkart", "Flipkart", "General marketplace researcher", ("flipkart.com",),
                 ("flipkart",), set(_GENERAL_RETAIL) - {"garden"}, "marketplace", avatar="#2563eb",
                 note="Broad catalogue; strongest in electronics, fashion, home."),
    AgentProfile("meesho", "Meesho", "Value fashion & home researcher", ("meesho.com",), ("meesho",),
                 {"apparel", "footwear", "fashion_accessories", "jewellery", "beauty", "personal_care",
                  "home_decor", "home_textiles", "kitchen", "household_essentials", "baby_kids", "toys",
                  "mobile_accessories", "stationery", "sewing_craft", "sports_fitness"},
                 "marketplace", avatar="#db2777", note="Value-priced fashion, home and everyday items."),
    AgentProfile("blinkit", "Blinkit", "Quick-commerce researcher", ("blinkit.com",), ("blinkit",),
                 {"grocery", "household_essentials", "personal_care", "beauty", "baby_kids", "stationery",
                  "mobile_accessories", "pets", "health_wellness"},
                 "hyperlocal", hyperlocal=True, avatar="#eab308",
                 note="Hyperlocal: stock and delivery depend entirely on the delivery address."),
    AgentProfile("myntra", "Myntra", "Fashion researcher", ("myntra.com",), ("myntra",),
                 {"apparel", "footwear", "fashion_accessories", "jewellery", "beauty", "personal_care",
                  "home_decor", "home_textiles", "sports_fitness", "baby_kids"},
                 "specialist", avatar="#e11d48", note="Fashion, footwear, accessories, beauty, some home."),
    AgentProfile("ajio", "AJIO", "Fashion researcher", ("ajio.com",), ("ajio",),
                 {"apparel", "footwear", "fashion_accessories", "jewellery", "beauty", "home_decor",
                  "home_textiles", "baby_kids", "sports_fitness"},
                 "specialist", avatar="#0f766e", note="Fashion, footwear, accessories, some home."),
    AgentProfile("nykaa", "Nykaa", "Beauty & wellness researcher", ("nykaa.com", "nykaafashion.com"),
                 ("nykaa",), {"beauty", "personal_care", "health_wellness", "apparel", "fashion_accessories",
                              "footwear", "jewellery"},
                 "specialist", avatar="#be185d", note="Beauty, personal care, wellness, fashion."),
    AgentProfile("firstcry", "FirstCry", "Baby & kids researcher", ("firstcry.com",), ("firstcry",),
                 {"baby_kids", "toys", "books_media"},
                 "specialist", avatar="#7c3aed", note="Baby, kids, toys, maternity. Not for adult-only products."),
    AgentProfile("snapdeal", "Snapdeal", "Value marketplace researcher", ("snapdeal.com",), ("snapdeal",),
                 {"apparel", "footwear", "fashion_accessories", "home_decor", "kitchen",
                  "household_essentials", "mobile_accessories", "beauty", "personal_care", "sports_fitness",
                  "toys", "tools_hardware", "automotive", "stationery", "appliances_small", "sewing_craft",
                  "general"},
                 "marketplace", avatar="#dc2626", note="Value marketplace for fashion, home and small items."),
    AgentProfile("hm", "H&M", "Fashion researcher", ("hm.com",), ("h&m", "hm"),
                 {"apparel", "footwear", "fashion_accessories", "home_decor", "home_textiles", "beauty",
                  "baby_kids", "sports_fitness", "jewellery"},
                 "specialist", avatar="#b91c1c", note="Apparel, accessories, kids, home textiles."),
    AgentProfile("shopify", "Shopify", "Independent-store researcher", (), ("shopify",),
                 {"apparel", "footwear", "fashion_accessories", "jewellery", "beauty", "personal_care",
                  "health_wellness", "home_decor", "home_textiles", "kitchen", "pets", "sports_fitness",
                  "toys", "stationery", "audio", "mobile_accessories", "general"},
                 "independent", avatar="#16a34a",
                 note="Finds independent brand stores that run on Shopify (no single domain)."),
    AgentProfile("croma", "Croma", "Electronics & appliances researcher", ("croma.com",), ("croma",),
                 {"mobiles", "computers", "audio", "wearables", "mobile_accessories", "cameras", "gaming",
                  "electronics", "appliances_large", "appliances_small", "kitchen"},
                 "specialist", avatar="#0e7490", note="Electronics and home appliances retailer."),
    AgentProfile("pepperfry", "Pepperfry", "Furniture researcher", ("pepperfry.com",), ("pepperfry",),
                 {"home_furniture", "home_decor", "home_textiles", "kitchen"},
                 "specialist", avatar="#c2410c", note="Furniture, decor and furnishings."),
    AgentProfile("internet", "Internet agent", "Open-web discovery researcher", (), (),
                 set(ALL), "internet", avatar="#4f46e5",
                 note="Discovers genuine retailers beyond the supported platforms."),
]

PROFILE_BY_KEY: dict[str, AgentProfile] = {p.key: p for p in PROFILES}


def select_agents(
    tags: list[str] | set[str],
    requested: list[str] | set[str] | None = None,
    limit: int = 12,
) -> tuple[list[tuple[AgentProfile, str]], list[tuple[AgentProfile, str]]]:
    """Choose agents for the request.

    Returns (selected, skipped) where each item is (profile, reason).
    `requested` lets the user name platforms explicitly ("check Croma too").
    """
    tag_set: set[str] = {tag for tag in tags} or {"general"}
    requested_set: set[str] = {platform for platform in requested} if requested else set()
    selected: list[tuple[AgentProfile, str]] = []
    skipped: list[tuple[AgentProfile, str]] = []
    for profile in PROFILES:
        if profile.key == "internet":
            selected.append((profile, "Always on: looks beyond the supported platforms for genuine retailers."))
            continue
        overlap = profile.tags & tag_set
        if profile.key in requested_set:
            selected.append((profile, "Requested by the user."))
        elif overlap:
            shown = ", ".join(sorted(overlap)[:3]).replace("_", " ")
            selected.append((profile, f"Covers {shown}."))
        elif "general" in tag_set and profile.kind in ("marketplace", "independent") and "general" in profile.tags:
            selected.append((profile, "General-purpose platform; unknown product type, so it will check."))
        elif tag_set == {"general"} and profile.key in ("amazon", "flipkart"):
            selected.append((profile, "General-purpose platform; unknown product type, so it will check."))
        else:
            wanted = ", ".join(sorted(tag_set)[:3]).replace("_", " ")
            skipped.append((profile, f"{profile.label} does not cover {wanted}."))
    if len(selected) > limit:
        keep = [s for s in selected if s[0].key in requested_set or s[0].key == "internet"]
        rest = [s for s in selected if s not in keep][: max(0, limit - len(keep))]
        dropped = [s for s in selected if s not in keep and s not in rest]
        selected = [s for s in selected if s in keep or s in rest]
        skipped.extend((p, "Skipped to stay within the per-run agent limit.") for p, _ in dropped)
    return selected, skipped
