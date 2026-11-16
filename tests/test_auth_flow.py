"""Login/registration now lead straight into the playground; the old dashboard is gone."""
from contextlib import contextmanager

import pytest

from app.routes import auth_routes


class Cur:
    def __init__(self, user): self.user = user
    def execute(self, *a, **k): pass
    def fetchone(self): return self.user


def fake_db(user):
    @contextmanager
    def db_cursor(commit=False):
        yield Cur(user)
    return db_cursor


USER = {"user_id": 7, "name": "Asha", "email": "a@x.in", "password_hash": None, "role": "user", "account_status": "active"}


def test_home_and_dashboard_lead_to_login_or_playground(client):
    assert client.get("/").headers["Location"].endswith("/login")
    assert client.get("/dashboard").headers["Location"].endswith("/playground")     # old bookmarks still work
    assert client.get("/playground").headers["Location"].endswith("/login")          # protected
    with client.session_transaction() as s:
        s["user_id"], s["name"] = 1, "Asha"
    assert client.get("/").headers["Location"].endswith("/playground")


def test_successful_login_goes_directly_to_the_playground(client, monkeypatch):
    from werkzeug.security import generate_password_hash
    monkeypatch.setattr(auth_routes, "db_cursor", fake_db({**USER, "password_hash": generate_password_hash("secret1")}))
    r = client.post("/login", data={"email": "a@x.in", "password": "secret1"})
    assert r.status_code == 302 and r.headers["Location"].endswith("/playground")
    assert client.get("/playground").status_code == 200


def test_wrong_password_stays_on_login(client, monkeypatch):
    from werkzeug.security import generate_password_hash
    monkeypatch.setattr(auth_routes, "db_cursor", fake_db({**USER, "password_hash": generate_password_hash("secret1")}))
    r = client.post("/login", data={"email": "a@x.in", "password": "nope"})
    assert r.status_code == 401 and b"Invalid email or password" in r.data


def test_signed_in_users_skip_login_and_register_pages(client):
    with client.session_transaction() as s:
        s["user_id"], s["name"] = 1, "Asha"
    for path in ("/login", "/register"):
        assert client.get(path).headers["Location"].endswith("/playground")


def test_register_still_leads_to_login_then_playground(client, monkeypatch):
    monkeypatch.setattr(auth_routes, "db_cursor", fake_db(None))
    r = client.post("/register", data={"name": "Asha", "email": "a@x.in", "password": "secret1", "confirm_password": "secret1"})
    assert r.status_code == 302 and "/login" in r.headers["Location"]


def test_playground_has_no_dead_links_and_offers_logout(client):
    with client.session_transaction() as s:
        s["user_id"], s["name"], s["csrf"] = 1, "Asha", "t"
    html = client.get("/playground").get_data(as_text=True)
    assert 'href="#"' not in html and "/logout" in html and "dashboard" not in html.lower()
