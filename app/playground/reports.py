"""Professional report exports: PDF, HTML, CSV, Markdown, JSON.

All content comes from the stored report; nothing is added or inferred here.
Images are embedded in the PDF only when the Supervisor verified them.
"""
from __future__ import annotations

import csv
import html
import io
import json
import os
from collections.abc import Callable
from datetime import datetime
from typing import Protocol, TypedDict, cast

from .net import FetchResult
from .models import VERIFIED

FONT_CANDIDATES: list[str] = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
    "C:/Windows/Fonts/segoeui.ttf",
    "C:/Windows/Fonts/arial.ttf",
]
BOLD_CANDIDATES: list[str] = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "C:/Windows/Fonts/segoeuib.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
]


class _NumericEvidence(TypedDict):
    value: int | float | None
    status: str
    retrieved_at: str | None


class _TextEvidence(TypedDict):
    value: str | None
    status: str
    retrieved_at: str | None


class _ImageEvidence(TypedDict):
    value: str | None
    status: str


class _Offer(TypedDict):
    offer_id: str
    title: str
    platform_label: str
    match_type: str
    price: _NumericEvidence
    currency: str | None
    rating: _NumericEvidence
    review_count: _NumericEvidence
    availability: _TextEvidence
    url: str | None
    score: float | None
    image: _ImageEvidence
    pros: list[str]
    cons: list[str]
    specs: dict[str, str]
    review_snippets: list[dict[str, str]]
    flags: list[dict[str, str]]


class _Recommendation(TypedDict):
    offer_id: str
    reason: str


class _Unavailable(TypedDict):
    platform: str
    reason: str
    kind: str


class _Excluded(TypedDict):
    platform: str
    title: str
    reason: str


class _AgentSummary(TypedDict):
    label: str
    status: str
    summary: str
    pages_attempted: int
    pages_verified: int


class _Verification(TypedDict):
    message: str


class _Source(TypedDict):
    platform: str
    kind: str
    url: str
    retrieved_at: str | None


class _Freshness(TypedDict):
    oldest: str | None
    newest: str | None


class _Summary(TypedDict):
    text: str
    language: str


class _Location(TypedDict):
    label: str


class _Report(TypedDict):
    product: str
    generated_at: str
    freshness: _Freshness
    recommendations: dict[str, _Recommendation]
    offers: list[_Offer]
    unavailable: list[_Unavailable]
    excluded: list[_Excluded]
    agents: list[_AgentSummary]
    verification_log: list[_Verification]
    sources: list[_Source]
    location: _Location | None
    limitations: list[str]
    summary: _Summary


class _Record(TypedDict):
    id: str
    text: str
    report: _Report


class _PdfMetrics(Protocol):
    def registerFont(self, font: object) -> None: ...


class _RegisterFontFamily(Protocol):
    def __call__(
        self,
        family: str,
        *,
        normal: str | None = None,
        bold: str | None = None,
        italic: str | None = None,
        boldItalic: str | None = None,
    ) -> None: ...


class _Row(TypedDict):
    platform: str
    title: str
    match: str
    price: int | float | None
    currency: str | None
    price_status: str
    rating: int | float | None
    rating_status: str
    reviews: int | float | None
    stock: str
    stock_status: str
    url: str
    retrieved_at: str
    score: float | None


class _ImageFetcher(Protocol):
    def fetch_image(self, url: str) -> FetchResult: ...


def money(value: int | float | None, currency: str | None) -> str:
    if value is None:
        return "—"
    currency_code = currency or ""
    sym = {"INR": "₹", "USD": "$", "EUR": "€", "GBP": "£"}.get(
        currency_code, f"{currency_code} "
    )
    return f"{sym}{value:,.0f}" if float(value).is_integer() else f"{sym}{value:,.2f}"


def _when(stamp: str | None) -> str:
    if stamp is None:
        return "unknown"
    try:
        return datetime.fromisoformat(stamp).strftime("%d %b %Y, %H:%M UTC")
    except (TypeError, ValueError):
        return "unknown"


STATUS_TEXT: dict[str, str] = {
    "verified": "Verified at source",
    "reported": "Reported by search (not confirmed)",
    "unavailable": "Unavailable",
    "unretrieved": "Could not be retrieved",
}


def _rows(report: _Report) -> list[_Row]:
    rows: list[_Row] = []
    for o in report["offers"]:
        rows.append({
            "platform": o["platform_label"], "title": o["title"], "match": o["match_type"],
            "price": o["price"]["value"], "currency": o["currency"], "price_status": o["price"]["status"],
            "rating": o["rating"]["value"], "rating_status": o["rating"]["status"],
            "reviews": o["review_count"]["value"], "stock": o["availability"]["value"] or "unknown",
            "stock_status": o["availability"]["status"], "url": o["url"] or "",
            "retrieved_at": o["price"]["retrieved_at"] or "", "score": o["score"],
        })
    return rows


# ------------------------------------------------------------------ CSV / JSON / MD
def to_csv(record: _Record) -> tuple[bytes, str, str]:
    rows = _rows(record["report"])
    out = io.StringIO()
    fields = ["platform", "title", "match", "price", "currency", "price_status", "rating", "rating_status", "reviews",
              "stock", "stock_status", "score", "url", "retrieved_at"]
    writer = csv.DictWriter(out, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        # neutralise spreadsheet formula injection from scraped titles
        row = {k: ("'" + v if isinstance(v, str) and v[:1] in "=+-@" else v) for k, v in row.items()}
        writer.writerow(row)
    return out.getvalue().encode("utf-8-sig"), "text/csv", "csv"


def to_json(record: _Record) -> tuple[bytes, str, str]:
    payload: dict[str, object] = {
        "run_id": record["id"],
        "query": record["text"],
        "report": record["report"],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"), "application/json", "json"


def to_markdown(record: _Record) -> tuple[bytes, str, str]:
    r = record["report"]
    by_id = {o["offer_id"]: o for o in r["offers"]}
    lines = [f"# Product research: {r['product']}", "",
             f"*Request:* {record['text']}  ", f"*Generated:* {_when(r['generated_at'])}  "]
    location = r.get("location")
    if location:
        lines.append(f"*Location (estimate only):* {location['label']}  ")
    lines += ["", "## Summary", "", r["summary"]["text"], ""]
    if r["recommendations"]:
        lines += ["## Recommendations", ""]
        for key, rec in r["recommendations"].items():
            o = by_id.get(rec["offer_id"])
            lines.append(f"- **{key.replace('_', ' ').title()}** — {o['title'] if o else ''} ({o['platform_label'] if o else ''}): {rec['reason']}")
        lines.append("")
    lines += ["## Comparison", "", "| Platform | Product | Price | Rating | Stock | Evidence |", "|---|---|---|---|---|---|"]
    for row in _rows(r):
        rating = f"{row['rating']:g}/5" if row["rating"] else "n/a"
        lines.append(f"| {row['platform']} | [{row['title'][:70].replace('|', '/')}]({row['url']}) | "
                     f"{money(row['price'], row['currency'])} | {rating} | {row['stock']} | {STATUS_TEXT.get(row['price_status'], row['price_status'])} |")
    if r["unavailable"]:
        lines += ["", "## Unavailable or could not be verified", ""]
        lines += [f"- **{u['platform']}**: {u['reason']}" for u in r["unavailable"]]
    lines += ["", "## Sources", ""]
    lines += [f"- {s['platform']} ({s['kind'].replace('_', ' ')}): {s['url']} — retrieved {_when(s['retrieved_at'])}" for s in r["sources"]]
    if r["limitations"]:
        lines += ["", "## Limitations", ""] + [f"- {l}" for l in r["limitations"]]
    return "\n".join(lines).encode("utf-8"), "text/markdown", "md"


# ------------------------------------------------------------------ HTML
def to_html(record: _Record) -> tuple[bytes, str, str]:
    r = record["report"]
    e = html.escape
    by_id = {o["offer_id"]: o for o in r["offers"]}
    parts = [f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Product research: {e(r['product'])}</title>
<style>body{{font:15px/1.5 system-ui,sans-serif;max-width:980px;margin:2rem auto;padding:0 1rem;color:#1b1f2a}}
table{{border-collapse:collapse;width:100%;font-size:14px}}th,td{{border:1px solid #d6dae3;padding:.45rem .6rem;text-align:left;vertical-align:top}}
th{{background:#f1f3f8}}.v{{color:#0a6b3a}}.r{{color:#8a5a00}}.u{{color:#a11}}img{{max-width:90px;max-height:90px}}
.card{{border:1px solid #d6dae3;border-radius:10px;padding:1rem;margin:1rem 0}}small{{color:#566}}</style></head><body>
<h1>Product research: {e(r['product'])}</h1>
<p><small>Request: {e(record['text'])}<br>Generated {e(_when(r['generated_at']))}"""]
    location = r.get("location")
    if location:
        parts.append(f"<br>Location used (estimate only): {e(location['label'])}")
    parts.append(f"</small></p><h2>Summary</h2><p>{e(r['summary']['text'])}</p>")
    if r["recommendations"]:
        parts.append("<h2>Recommendations</h2><ul>")
        for key, rec in r["recommendations"].items():
            o = by_id.get(rec["offer_id"])
            parts.append(f"<li><b>{e(key.replace('_', ' ').title())}</b>: {e(o['title'] if o else '')} — {e(rec['reason'])}</li>")
        parts.append("</ul>")
    parts.append("<h2>Comparison</h2><table><tr><th>Platform</th><th>Product</th><th>Price</th><th>Rating</th><th>Stock</th><th>Evidence</th></tr>")
    for row in _rows(r):
        cls = {"verified": "v", "reported": "r"}.get(row["price_status"], "u")
        link = f'<a href="{e(row["url"])}" rel="noopener noreferrer nofollow">{e(row["title"][:90])}</a>' if row["url"].startswith(("http://", "https://")) else e(row["title"][:90])
        parts.append(f"<tr><td>{e(row['platform'])}</td><td>{link}</td><td>{e(money(row['price'], row['currency']))}</td>"
                     f"<td>{'%g/5' % row['rating'] if row['rating'] else 'n/a'}{' (' + format(int(row['reviews']), ',') + ')' if row['reviews'] else ''}</td>"
                     f"<td>{e(str(row['stock']))}</td><td class='{cls}'>{e(STATUS_TEXT.get(row['price_status'], row['price_status']))}<br><small>{e(_when(row['retrieved_at']))}</small></td></tr>")
    parts.append("</table><h2>Details</h2>")
    for o in r["offers"][:12]:
        img = o["image"]["value"] if o["image"]["status"] == VERIFIED and str(o["image"]["value"]).startswith("http") else None
        parts.append("<div class='card'>" + (f"<img src='{e(img)}' alt='' referrerpolicy='no-referrer'> " if img else "")
                     + f"<b>{e(o['title'])}</b> — {e(o['platform_label'])}<br>")
        if o["pros"]:
            parts.append("<b>Advantages</b><ul>" + "".join(f"<li>{e(x)}</li>" for x in o["pros"]) + "</ul>")
        if o["cons"]:
            parts.append("<b>Disadvantages / cautions</b><ul>" + "".join(f"<li>{e(x)}</li>" for x in o["cons"]) + "</ul>")
        if o["specs"]:
            parts.append("<b>Specifications (from retailer page)</b><ul>" + "".join(f"<li>{e(k)}: {e(str(v))}</li>" for k, v in list(o['specs'].items())[:10]) + "</ul>")
        parts.append("</div>")
    if r["unavailable"]:
        parts.append("<h2>Unavailable or could not be verified</h2><ul>" + "".join(f"<li><b>{e(u['platform'])}</b>: {e(u['reason'])}</li>" for u in r["unavailable"]) + "</ul>")
    parts.append("<h2>Sources</h2><ul>" + "".join(f"<li>{e(s['platform'])} ({e(s['kind'].replace('_', ' '))}): {e(s['url'])} — retrieved {e(_when(s['retrieved_at']))}</li>" for s in r["sources"]) + "</ul>")
    if r["limitations"]:
        parts.append("<h2>Limitations</h2><ul>" + "".join(f"<li>{e(l)}</li>" for l in r["limitations"]) + "</ul>")
    parts.append("<p><small>Verified = read from the retailer's own page. Reported = from a search provider, not confirmed at source.</small></p></body></html>")
    return "".join(parts).encode("utf-8"), "text/html", "html"


# ------------------------------------------------------------------ PDF
def _register_fonts() -> tuple[str, str, bool]:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    regular = next((p for p in FONT_CANDIDATES if os.path.exists(p)), None)
    bold = next((p for p in BOLD_CANDIDATES if os.path.exists(p)), None)
    if not regular:
        return "Helvetica", "Helvetica-Bold", False
    font_registry = cast(_PdfMetrics, pdfmetrics)
    font_registry.registerFont(TTFont("PG-Regular", regular))
    font_registry.registerFont(TTFont("PG-Bold", bold or regular))
    register_font_family = cast(
        _RegisterFontFamily, getattr(pdfmetrics, "registerFontFamily")
    )
    register_font_family(
        "PG-Regular", normal="PG-Regular", bold="PG-Bold", italic="PG-Regular", boldItalic="PG-Bold"
    )
    return "PG-Regular", "PG-Bold", True


def to_pdf(
    record: _Record, fetcher: _ImageFetcher | None = None
) -> tuple[bytes, str, str]:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (Image, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table,
                                    TableStyle)
    from reportlab.pdfgen.canvas import Canvas
    from reportlab.platypus.doctemplate import BaseDocTemplate
    from reportlab.platypus.flowables import Flowable

    regular, bold, unicode_ok = _register_fonts()
    r = record["report"]
    by_id = {o["offer_id"]: o for o in r["offers"]}

    def txt(value: object) -> str:
        s = html.escape(str(value if value is not None else ""))
        return s if unicode_ok else s.encode("latin-1", "replace").decode("latin-1")

    def price(value: int | float | None, currency: str | None) -> str:
        if value is None:
            return "n/a"
        return money(value, currency) if unicode_ok else f"{currency or ''} {value:,.2f}".strip()

    base = ParagraphStyle("b", fontName=regular, fontSize=9, leading=12.5)
    small = ParagraphStyle("s", parent=base, fontSize=7.5, leading=10, textColor=colors.HexColor("#566070"))
    h1 = ParagraphStyle("h1", parent=base, fontName=bold, fontSize=19, leading=23, spaceAfter=4)
    h2 = ParagraphStyle("h2", parent=base, fontName=bold, fontSize=12.5, leading=16, spaceBefore=12, spaceAfter=5,
                        textColor=colors.HexColor("#2b3a8f"))
    cell = ParagraphStyle("c", parent=base, fontSize=8, leading=10.5)
    cellb = ParagraphStyle("cb", parent=cell, fontName=bold)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm, topMargin=16 * mm,
                            bottomMargin=16 * mm, title=f"Product research: {r['product']}", author="RESQUORA AI Agent Playground")

    def footer(canvas: Canvas, d: BaseDocTemplate) -> None:
        canvas.saveState()
        canvas.setFont(regular, 7)
        canvas.setFillColor(colors.HexColor("#6b7380"))
        canvas.drawString(16 * mm, 9 * mm, "Verified = read from the retailer's own page. Reported = from a search provider, not confirmed at source.")
        canvas.drawRightString(A4[0] - 16 * mm, 9 * mm, f"Page {d.page}")
        canvas.restoreState()

    story: list[Flowable] = [
        Paragraph(txt(f"Product research: {r['product']}"), h1),
        Paragraph(txt(f"Request: {record['text']}"), small),
        Paragraph(txt(f"Generated {_when(r['generated_at'])} · Run {record['id'][:8]}"), small),
    ]
    location = r.get("location")
    if location:
        story.append(Paragraph(txt(f"Location used (estimate only, not confirmed by any retailer): {location['label']}"), small))
    if r["freshness"]["oldest"]:
        story.append(Paragraph(txt(f"Data retrieved between {_when(r['freshness']['oldest'])} and {_when(r['freshness']['newest'])}"), small))

    story.extend((Paragraph("Summary", h2), Paragraph(txt(r["summary"]["text"]), base)))

    if r["recommendations"]:
        story.append(Paragraph("Recommendations and why", h2))
        for key, rec in r["recommendations"].items():
            o = by_id.get(rec["offer_id"])
            story.append(Paragraph(f"<b>{txt(key.replace('_', ' ').title())}:</b> {txt(o['title'][:90] if o else '')} "
                                   f"({txt(o['platform_label'] if o else '')}). {txt(rec['reason'])}", base))
            story.append(Spacer(1, 3))

    story.append(Paragraph("Comparison across platforms", h2))
    data = [[Paragraph(h, cellb) for h in ("Platform", "Product", "Price", "Rating", "Stock", "Evidence")]]
    for row in _rows(r):
        rating = f"{row['rating']:g}/5" + (f" ({int(row['reviews']):,})" if row["reviews"] else "") if row["rating"] else "n/a"
        data.append([Paragraph(txt(row["platform"]), cell),
                     Paragraph(f'<link href="{html.escape(row["url"])}" color="#2b3a8f">{txt(row["title"][:70])}</link>' if row["url"].startswith(("http://", "https://")) else txt(row["title"][:70]), cell),
                     Paragraph(txt(price(row["price"], row["currency"])), cell), Paragraph(txt(rating), cell),
                     Paragraph(txt(row["stock"].replace("_", " ")), cell),
                     Paragraph(txt(STATUS_TEXT.get(row["price_status"], row["price_status"])) + f"<br/><font size=6>{txt(_when(row['retrieved_at']))}</font>", cell)])
    if len(data) == 1:
        data.append(
            [Paragraph("No listings passed verification.", cell)]
            + [Paragraph("", cell) for _ in range(5)]
        )
    table = Table(data, colWidths=[24 * mm, 52 * mm, 19 * mm, 21 * mm, 21 * mm, 37 * mm], repeatRows=1)
    table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef0f8")),
                               ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#c9cedb")),
                               ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 3),
                               ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
    story.append(table)

    story.append(Paragraph("Product details", h2))
    embedded = 0
    for o in r["offers"][:10]:
        left = ""
        if fetcher and embedded < 8 and o["image"]["status"] == VERIFIED and o["image"]["value"]:
            res = fetcher.fetch_image(o["image"]["value"])
            if res.ok and res.content:
                try:
                    from PIL import Image as PILImage
                    pil = PILImage.open(io.BytesIO(res.content)).convert("RGB")
                    pil.thumbnail((300, 300))
                    out = io.BytesIO()
                    pil.save(out, "PNG")
                    out.seek(0)
                    w, h = pil.size
                    scale = min(32 * mm / w, 32 * mm / h)
                    left = Image(out, width=w * scale, height=h * scale)
                    embedded += 1
                except Exception:
                    left = ""
        lines = [Paragraph(f"<b>{txt(o['title'][:110])}</b>", base),
                 Paragraph(txt(f"{o['platform_label']} · {price(o['price']['value'], o['currency'])} · {STATUS_TEXT.get(o['price']['status'], '')}"), cell)]
        if o["url"] and o["url"].startswith(("http://", "https://")):
            lines.append(Paragraph(f'<link href="{html.escape(o["url"])}" color="#2b3a8f">{txt(o["url"][:95])}</link>', small))
        for label, items in (("Advantages", o["pros"]), ("Disadvantages / cautions", o["cons"])):
            if items:
                lines.append(Paragraph(f"<b>{label}</b>: " + txt("; ".join(items[:4])), cell))
        if o["specs"]:
            lines.append(Paragraph("<b>Specifications (retailer page)</b>: " + txt("; ".join(f"{k}: {v}" for k, v in list(o["specs"].items())[:6])), cell))
        for snip in o["review_snippets"][:2]:
            lines.append(Paragraph(txt(f"Review excerpt: “{snip['text'][:180]}”"), small))
        for f in o["flags"][:3]:
            lines.append(Paragraph("⚠ " + txt(f["detail"]) if unicode_ok else "Note: " + txt(f["detail"]), small))
        t = Table([[left, lines]], colWidths=[36 * mm, 134 * mm])
        t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOX", (0, 0), (-1, -1), 0.4, colors.HexColor("#c9cedb")),
                               ("LEFTPADDING", (0, 0), (-1, -1), 5), ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
        story += [KeepTogether([t]), Spacer(1, 5)]

    if r["unavailable"]:
        story.append(Paragraph("Unavailable, not found, or could not be verified", h2))
        for u in r["unavailable"]:
            kind = {"out_of_stock": "Out of stock", "not_found": "No listing found", "could_not_access": "Could not access"}.get(u["kind"], "")
            story.append(Paragraph(f"<b>{txt(u['platform'])}</b> ({kind}): {txt(u['reason'])}", cell))
    if r["excluded"]:
        story.append(Paragraph("Excluded by Supervisor verification", h2))
        for x in r["excluded"][:25]:
            story.append(Paragraph(f"{txt(x['platform'])}: {txt(x['title'][:70])} — {txt(x['reason'])}", cell))

    story.append(Paragraph("How the team worked", h2))
    for a in r["agents"]:
        story.append(Paragraph(f"<b>{txt(a['label'])}</b> [{txt(a['status'])}] {txt(a['summary'])} "
                               f"Pages opened: {a['pages_attempted']}, read successfully: {a['pages_verified']}.", cell))
    story.append(Paragraph("Supervisor verification checks", h2))
    for v in r["verification_log"]:
        story.append(Paragraph(txt(v["message"]), cell))

    story.append(Paragraph("Sources and retrieval times", h2))
    for s in r["sources"]:
        story.append(Paragraph(f"{txt(s['platform'])} ({txt(s['kind'].replace('_', ' '))}): {txt(s['url'][:100])} — {txt(_when(s['retrieved_at']))}", small))
    if r["limitations"]:
        story.append(Paragraph("Limitations", h2))
        for l in r["limitations"]:
            story.append(Paragraph("• " + txt(l), cell))
    if not unicode_ok:
        story.append(Paragraph("Note: no Unicode font was found on the server, so some symbols were simplified.", small))
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue(), "application/pdf", "pdf"


FORMATS: dict[str, Callable[[_Record], tuple[bytes, str, str]]] = {
    "pdf": to_pdf,
    "html": to_html,
    "csv": to_csv,
    "json": to_json,
    "md": to_markdown,
}


def export(
    record: _Record,
    fmt: str,
    fetcher: _ImageFetcher | None = None,
) -> tuple[bytes, str, str]:
    if fmt not in FORMATS:
        raise ValueError("Unsupported format")
    if not record.get("report"):
        raise ValueError("This run has no report yet")
    if fmt == "pdf":
        return to_pdf(record, fetcher)
    return FORMATS[fmt](record)
