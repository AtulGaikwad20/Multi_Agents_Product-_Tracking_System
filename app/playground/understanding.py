"""Turn a spoken/typed request (in any language) into a structured research brief.

With Claude configured the request is understood in any language and mapped
onto capability tags for *any* product. Without Claude we fall back to
deterministic English parsing and say so; we never pretend to understand a
language we cannot.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Protocol, TypeGuard

from .capabilities import PROFILE_BY_KEY, TAGS

CURRENCY: str = r"(?<![A-Za-z])(?:₹|rs\.?|inr)"
AMOUNT: str = r"(\d[\d,]*(?:\.\d+)?)\s*(k\b)?(?:\s*(?:rupees?|rs\b\.?|inr\b))?"

COLORS: list[str] = ["black", "white", "red", "blue", "green", "yellow", "orange", "pink", "purple", "brown",
          "grey", "gray", "beige", "cream", "navy", "maroon", "silver", "gold"]

SCRIPT_LANG: list[tuple[str, str]] = [  # unicode block -> language hint (only used to explain limitations)
    (r"[\u0900-\u097F]", "hi"), (r"[\u0B80-\u0BFF]", "ta"), (r"[\u0C00-\u0C7F]", "te"),
    (r"[\u0980-\u09FF]", "bn"), (r"[\u0A80-\u0AFF]", "gu"), (r"[\u0C80-\u0CFF]", "kn"),
    (r"[\u0D00-\u0D7F]", "ml"), (r"[\u0A00-\u0A7F]", "pa"), (r"[\u0600-\u06FF]", "ar"),
    (r"[\u4E00-\u9FFF]", "zh"), (r"[\u3040-\u30FF]", "ja"), (r"[\uAC00-\uD7AF]", "ko"),
    (r"[\u0400-\u04FF]", "ru"),
]

# keyword -> tags: only the *fallback* classifier. With Claude, any product works.
KEYWORD_TAGS: list[tuple[str, list[str]]] = [
    (r"phone|smartphone|iphone|galaxy|redmi|pixel", ["mobiles", "electronics"]),
    (r"laptop|notebook|macbook|monitor|keyboard|mouse|printer|router|pendrive|ssd|hard disk", ["computers", "electronics"]),
    (r"earbud|earphone|headphone|headset|speaker|soundbar|airdopes|neckband|microphone", ["audio", "electronics"]),
    (r"smartwatch|smart watch|fitness band|fitness tracker|wearable", ["wearables", "electronics"]),
    (r"charger|cable|power ?bank|adapter|phone case|screen guard|tempered glass|usb", ["mobile_accessories", "electronics"]),
    (r"camera|gopro|dslr|tripod|lens", ["cameras", "electronics"]),
    (r"playstation|xbox|console|gamepad|controller|gaming", ["gaming", "electronics"]),
    (r"\btv\b|television|refrigerator|fridge|washing machine|air conditioner|\bac\b|geyser|water heater|microwave|dishwasher", ["appliances_large", "electronics"]),
    (r"mixer|grinder|blender|air fryer|kettle|toaster|iron\b|vacuum|induction|juicer|cooker|fan\b|heater|purifier", ["appliances_small", "kitchen"]),
    (r"sofa|couch|bed\b|mattress|wardrobe|cupboard|almirah|cabinet|table|chair|desk|shelf|bookcase|furniture|recliner|dining|shoe rack|dresser", ["home_furniture"]),
    (r"curtain|bedsheet|pillow|cushion|blanket|towel|carpet|rug|duvet", ["home_textiles", "home_decor"]),
    (r"vase|wall art|lamp|decor|mirror|frame|candle|showpiece|planter", ["home_decor"]),
    (r"pan\b|pot\b|utensil|lunch box|bottle|container|cookware|knife|plate|bowl|cup\b|mug", ["kitchen", "household_essentials"]),
    (r"safety pin|needle|thread|button|zipper|sewing|scissors|velcro|hook and eye|craft", ["sewing_craft", "household_essentials"]),
    (r"detergent|soap|broom|mop|bucket|bulb|battery|tape|glue|trash bag|tissue|hanger|clip|clothes? ?pin|rubber band|duster|sponge", ["household_essentials"]),
    (r"milk|rice|atta|flour|oil\b|sugar|salt|tea\b|coffee|biscuit|snack|fruit|vegetable|egg|bread|dal\b|spice|chocolate|juice", ["grocery"]),
    (r"pen\b|pencil|notebook|diary|stapler|marker|eraser|highlighter|folder|stationery|paper|sticky note", ["stationery"]),
    (r"drill|screwdriver|hammer|wrench|spanner|plier|toolkit|tool kit|saw\b|ladder|hardware", ["tools_hardware"]),
    (r"shirt|t-?shirt|jeans|trouser|kurta|saree|dress|top\b|jacket|hoodie|sweater|shorts|skirt|leggings|lehenga|suit|blazer|innerwear|socks|apparel|clothing|clothes", ["apparel"]),
    (r"shoe|sneaker|sandal|slipper|boot|heel|loafer|flip.?flop|footwear", ["footwear"]),
    (r"wallet|belt|bag\b|backpack|handbag|sunglass|cap\b|scarf|tie\b|luggage|watch\b", ["fashion_accessories"]),
    (r"necklace|earring|ring\b|bracelet|jewel|bangle|pendant|anklet", ["jewellery"]),
    (r"lipstick|foundation|serum|moisturi[sz]er|sunscreen|makeup|mascara|perfume|kajal|nail polish|face wash|shampoo|conditioner", ["beauty", "personal_care"]),
    (r"toothpaste|toothbrush|razor|trimmer|deodorant|body wash|sanitary|hair oil", ["personal_care"]),
    (r"vitamin|protein|supplement|ayurved|thermometer|bp monitor|glucometer|mask\b|sanitizer", ["health_wellness"]),
    (r"diaper|baby|infant|toddler|stroller|crib|feeding|kids?\b|child|maternity", ["baby_kids"]),
    (r"toy|lego|puzzle|board game|doll|action figure|rc car", ["toys"]),
    (r"yoga|dumbbell|treadmill|cricket|football|badminton|gym|cycle|bicycle|skipping|fitness", ["sports_fitness"]),
    (r"book|novel|textbook|magazine|comic", ["books_media"]),
    (r"helmet|car |bike |tyre|tire|dashcam|seat cover|engine oil|wiper", ["automotive"]),
    (r"dog|cat\b|pet\b|puppy|kitten|aquarium", ["pets"]),
    (r"seed|fertili[sz]er|garden|gardening|sprinkler|lawn", ["garden"]),
]

LOCATION_HINTS: re.Pattern[str] = re.compile(
    r"near me|nearby|nearest|local|in my (?:area|city)|deliver(?:y|ed)? (?:to|by|today|tomorrow)|same.?day|"
    r"in stock near|pick ?up|today|tomorrow|within \d+ ?(?:min|hour)|\bnow\b|urgent", re.I)

PLATFORM_WORDS: dict[str, str] = {
    "amazon": r"\bamazon\b", "flipkart": r"\bflipkart\b", "meesho": r"\bmeesho\b", "blinkit": r"\bblinkit\b",
    "myntra": r"\bmyntra\b", "ajio": r"\bajio\b", "nykaa": r"\bnykaa\b", "firstcry": r"\bfirst\s?cry\b",
    "snapdeal": r"\bsnapdeal\b", "hm": r"\bh\s?&\s?m\b", "shopify": r"\bshopify\b",
    "croma": r"\bcroma\b", "pepperfry": r"\bpepperfry\b",
}


class _BriefLlm(Protocol):
    available: bool

    def json(self, system: str, user: str, max_tokens: int = 900) -> dict[str, object] | None: ...


def _is_json_array(value: object) -> TypeGuard[list[object]]:
    return isinstance(value, list)


@dataclass
class Brief:
    original: str
    language: str = "en"
    product: str = ""
    brand: str | None = None
    tags: list[str] = field(default_factory=lambda: list[str]())
    min_price: float | None = None
    max_price: float | None = None
    colors: list[str] = field(default_factory=lambda: list[str]())
    must_have: list[str] = field(default_factory=lambda: list[str]())
    avoid: list[str] = field(default_factory=lambda: list[str]())
    priorities: list[str] = field(default_factory=lambda: list[str]())      # e.g. ["price", "rating", "quality"]
    platforms_requested: list[str] = field(default_factory=lambda: list[str]())
    location_relevant: bool = False
    location_reason: str = ""
    understood_by: str = "rules"                        # llm | rules
    notice: str | None = None
    intent: str = "search"

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _to_number(digits: str, thousands: str | None) -> float:
    value = float(digits.replace(",", ""))
    return value * 1000 if thousands else value


def extract_price_range(text: str) -> dict[str, float]:
    out: dict[str, float] = {}
    m = (re.search(rf"between\s*{CURRENCY}?\s*{AMOUNT}\s*(?:and|to|-)\s*{CURRENCY}?\s*{AMOUNT}", text, re.I)
         or re.search(rf"{CURRENCY}\s*{AMOUNT}\s*(?:to|-)\s*{CURRENCY}?\s*{AMOUNT}", text, re.I))
    if m:
        a, b = _to_number(m.group(1), m.group(2)), _to_number(m.group(3), m.group(4))
        return {"min_price": min(a, b), "max_price": max(a, b)}
    m = re.search(rf"(?:under|below|less than|upto|up to|max(?:imum)?|within|budget(?: of)?)\s*{CURRENCY}?\s*{AMOUNT}", text, re.I)
    if m:
        return {"max_price": _to_number(m.group(1), m.group(2))}
    m = re.search(rf"(?:above|over|more than|at least|minimum|min)\s*{CURRENCY}?\s*{AMOUNT}", text, re.I)
    if m:
        out["min_price"] = _to_number(m.group(1), m.group(2))
    m = re.search(rf"{CURRENCY}\s*{AMOUNT}", text, re.I)
    if m and not out:
        out["max_price"] = _to_number(m.group(1), m.group(2))
    return out


def classify_tags(text: str) -> list[str]:
    lowered = f" {text.lower()} "
    tags: list[str] = []
    for pattern, found in KEYWORD_TAGS:
        if re.search(pattern, lowered):
            for tag in found:
                if tag not in tags:
                    tags.append(tag)
    return tags or ["general"]


def clean_product_text(text: str) -> str:
    cleaned = text
    for pattern in [
        r"\b(?:please|kindly|show me|find me|find|search for|search|get me|i want|i need|looking for|compare|comparison)\b",
        r"\b(?:best|good|top|cheap|cheapest|affordable|lowest price|best price|recommended?)\b",
        r"\b(?:reviews?|ratings?|prices?)\b", r"\bnear me\b|\bnearby\b|\bonline\b|\bavailable\b",
        r"\b(?:on|from|at)\s+(?:" + "|".join(PLATFORM_WORDS.values()) + ")",
        "|".join(PLATFORM_WORDS.values()),
        rf"\bbetween\s*{CURRENCY}?\s*{AMOUNT}\s*(?:and|to|-)\s*{CURRENCY}?\s*{AMOUNT}",
        rf"{CURRENCY}\s*{AMOUNT}\s*(?:to|-)\s*{CURRENCY}?\s*{AMOUNT}",
        rf"\b(?:under|below|less than|upto|up to|within|budget(?: of)?|above|over|more than|at least)\s*{CURRENCY}?\s*{AMOUNT}",
        rf"{CURRENCY}\s*{AMOUNT}",
    ]:
        cleaned = re.sub(pattern, " ", cleaned, flags=re.I)
    cleaned = re.sub(r"[?!.,]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,.-&")
    cleaned = re.sub(r"^(?:a|an|the|some|and|or|of|for|in|on|with)\b\s*", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\s*\b(?:and|or|of|for|in|on|with|a|an)$", "", cleaned, flags=re.I)
    return cleaned.strip()


def detect_script(text: str) -> str | None:
    for pattern, lang in SCRIPT_LANG:
        if re.search(pattern, text):
            return lang
    return None


def understand_with_rules(text: str, language_hint: str = "en") -> Brief:
    detected = detect_script(text)
    brief = Brief(original=text, language=detected or language_hint or "en", understood_by="rules")
    prices = extract_price_range(text)
    brief.min_price, brief.max_price = prices.get("min_price"), prices.get("max_price")
    brief.colors = [c for c in COLORS if re.search(rf"\b{c}\b", text, re.I)]
    brief.platforms_requested = [
        k for k, p in PLATFORM_WORDS.items()
        if re.search(p, text, re.I)
    ]
    brief.product = clean_product_text(text) or text.strip()
    brief.tags = classify_tags(brief.product)
    lowered = text.lower()
    if re.search(r"cheap|lowest|budget|afford", lowered):
        brief.priorities.append("price")
    if re.search(r"best|quality|durable|top rated|reliable", lowered):
        brief.priorities += ["rating", "quality"]
    m = LOCATION_HINTS.search(text)
    if m:
        brief.location_relevant = True
        brief.location_reason = f"Your request mentions “{m.group(0)}”, so local availability and delivery matter."
    non_english = (detected and detected != "en") or (language_hint not in (None, "", "en") and not re.search(r"[a-z]{3,}", lowered))
    if non_english:
        brief.notice = ("Reliable understanding of non-English requests needs an AI language model. "
                        "I tried my best with the text as written.")
    return brief


SYSTEM: str = f"""You are the Supervisor of a team of product-research agents. Convert the user's request
(any language) into a research brief. Understand the user's intended meaning, not just the literal spelling.
Silently correct obvious spelling mistakes, grammar, word order, punctuation, and speech-to-text errors.
Do not criticize or mention the user's writing. Preserve brands, model numbers, product names, sizes,
quantities, colors, budgets, platforms, and other explicit constraints; do not "correct" unusual names
that may be brands or models. Never add a feature, product detail, or constraint the user did not request.
For example, interpret "i want a moblie with good camra and long bettry under 20 thausand" as a
smartphone with a good camera and long battery life, with max_price 20000 INR. Interpret "red shose
for men below 3k" as men's red shoes with max_price 3000 INR. If the intended product is uncertain,
use a broad search phrase and the best-supported tag rather than inventing a specific model or feature.
Do not answer the request; do not invent facts.
Return JSON with keys:
language: BCP-47 code of the language the user WROTE in (e.g. "hi", "ta", "en", "es").
product: concise English product search phrase (keep brand/model/size/quantity; drop price words).
brand: brand string or null.
tags: 1-5 capability tags chosen ONLY from this list: {", ".join(TAGS)}. Use "general" only if nothing else fits.
  Tag guidance: audio/wearables/mobile_accessories are sub-areas of electronics; tag electronics too when relevant.
min_price, max_price: numbers in INR or null (convert "5k" -> 5000).
colors: list; must_have: list of required features/attributes in English; avoid: list of things to avoid.
priorities: subset of ["price","rating","quality","delivery_speed","brand","features"] the user cares about.
platforms_requested: subset of {sorted(PLATFORM_WORDS)} that the user explicitly named.
location_relevant: true only if local stock, nearby shops, delivery time/charges or regional pricing matter.
location_reason: one short sentence in the USER'S language explaining why, or "".
intent: "search" for a normal request."""


def understand(text: str, llm: _BriefLlm | None = None, language_hint: str = "en") -> Brief:
    fallback = understand_with_rules(text, language_hint)
    if not llm or not llm.available:
        return fallback
    data = llm.json(SYSTEM, text)
    if data is None or not data.get("product"):
        fallback.notice = fallback.notice or "The AI model could not parse the request; used basic parsing instead."
        return fallback

    def num(v: object) -> float | None:
        if not isinstance(v, (int, float, str)) or v == "":
            return None
        try:
            return float(v)
        except ValueError:
            return None

    def strs(v: object) -> list[str]:
        return [str(x).strip() for x in v if str(x).strip()] if _is_json_array(v) else []

    tags = [t for t in strs(data.get("tags")) if t in TAGS] or classify_tags(str(data.get("product")))
    brief = Brief(
        original=text,
        language=str(data.get("language") or language_hint or "en")[:12],
        product=str(data["product"]).strip()[:200],
        brand=(str(data["brand"]).strip() or None) if data.get("brand") else None,
        tags=tags,
        min_price=num(data.get("min_price")), max_price=num(data.get("max_price")),
        colors=strs(data.get("colors")), must_have=strs(data.get("must_have")),
        avoid=strs(data.get("avoid")), priorities=strs(data.get("priorities")),
        platforms_requested=[p for p in strs(data.get("platforms_requested")) if p in PROFILE_BY_KEY],
        location_relevant=bool(data.get("location_relevant")),
        location_reason=str(data.get("location_reason") or ""),
        understood_by="llm",
    )
    if brief.min_price is None and brief.max_price is None:
        prices = extract_price_range(text)
        brief.min_price, brief.max_price = prices.get("min_price"), prices.get("max_price")
    return brief
