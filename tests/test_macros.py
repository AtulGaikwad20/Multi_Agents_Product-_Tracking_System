"""_macros.html must render correctly AND be backed by CSS that the pages actually load."""
import re
from pathlib import Path

import pytest
from flask import render_template_string

CSS = Path("app/static/css")
IMPORT = "{% from '_macros.html' import stars, external, evidence_badge %}"


def render(app, body):
    with app.test_request_context():
        return render_template_string(IMPORT + body).strip()


def test_stars_with_rating_and_count(app):
    html = render(app, "{{ stars(4.26, 1234) }}")
    assert 'class="rating"' in html and 'aria-label="Rated 4.3 out of 5"' in html
    assert "4.3" in html and "(1,234)" in html and 'class="star"' in html


@pytest.mark.parametrize("value", [None, 0, -1, 7, "x"])
def test_stars_ignores_missing_or_impossible_ratings(app, value):
    html = render(app, "{{ stars(v) }}".replace("v", repr(value)))
    assert "No ratings" in html and 'class="star"' not in html


def test_external_renders_only_real_web_links(app):
    ok = render(app, "{{ external('https://shop.example/p?a=1&b=2', 'Open', 'x') }}")
    assert 'href="https://shop.example/p?a=1&amp;b=2"' in ok and 'rel="noopener noreferrer nofollow"' in ok and "ext-link x" in ok
    for bad in ("javascript:alert(1)", "data:text/html,hi", "", "ftp://x"):
        html = render(app, "{{ external(u, 'Open') }}".replace("u", repr(bad)))
        assert "<a " not in html and "Open" in html


def test_external_escapes_text(app):
    assert "&lt;b&gt;" in render(app, "{{ external('https://a.example', '<b>x</b>') }}")


@pytest.mark.parametrize("status", ["verified", "reported", "unavailable", "unretrieved"])
def test_evidence_badge_statuses(app, status):
    assert f'class="badge {status}"' in render(app, f"{{{{ evidence_badge('{status}') }}}}")


def test_unknown_badge_status_renders_nothing(app):
    assert render(app, "{{ evidence_badge('bogus') }}") == ""


def test_every_css_class_the_macros_use_is_defined_in_the_playground_stylesheet():
    macros = Path("app/templates/_macros.html").read_text(encoding="utf-8")
    used = set(re.findall(r'class="([^"{]+)', macros)) | {"badge", "verified", "reported", "unavailable", "unretrieved"}
    classes = {c for chunk in used for c in chunk.split() if c not in ("{{",)}
    css = (CSS / "playground.css").read_text(encoding="utf-8")
    missing = [c for c in sorted(classes) if f".{c}" not in css]
    assert not missing, f"macros use classes missing from playground.css: {missing}"


def test_style_css_still_styles_the_rating_macro_for_the_login_pages():
    css = (CSS / "style.css").read_text(encoding="utf-8")
    assert all(f".{c}" in css for c in ("rating", "star", "rating-count"))


def test_playground_page_imports_the_macros_and_shows_the_legend(app, client):
    with client.session_transaction() as s:
        s["user_id"], s["name"], s["csrf"] = 1, "Asha", "t"
    html = client.get("/playground").get_data(as_text=True)
    for status in ("verified", "reported", "unavailable", "unretrieved"):
        assert f'class="badge {status}"' in html
