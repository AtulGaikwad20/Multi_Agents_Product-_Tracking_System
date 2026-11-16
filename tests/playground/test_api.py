import json
import time
from datetime import datetime, timedelta, timezone

import pytest

from tests.playground.test_pipeline import tee_world
from tests.playground.fakes import FakeLlm
from app.playground.store import StoreError
from app.playground.supervisor import Supervisor


@pytest.fixture
def pg(app):
    search, fetcher = tee_world()
    sup = app.extensions["playground"].supervisor
    sup.search, sup.fetcher = search, fetcher
    return app


def login(client, user_id=1, csrf=None):
    with client.session_transaction() as s:
        s["user_id"], s["name"] = user_id, f"User{user_id}"
        s["csrf"] = csrf or "tok-" + str(user_id)
    return {"X-CSRF-Token": s["csrf"]}


def finish(client, run_id, h):
    for _ in range(100):
        r = client.get(f"/api/runs/{run_id}").get_json()
        if r["status"] in ("done", "error", "cancelled"):
            time.sleep(0.1)
            return r
        time.sleep(0.1)
    raise AssertionError("timeout")


def test_api_requires_login(client):
    assert client.get("/api/config").status_code == 401
    assert client.post("/api/runs", json={"text": "x"}).status_code == 401
    assert client.get("/playground").status_code == 302


def test_csrf_is_enforced_on_state_changing_calls(pg, client):
    login(client)
    assert client.post("/api/runs", json={"text": "sofa"}).status_code == 403
    assert client.post("/api/runs", json={"text": "sofa"}, headers={"X-CSRF-Token": "wrong"}).status_code == 403
    assert client.delete("/api/location").status_code == 403


def test_input_validation(pg, client):
    h = login(client)
    assert client.post("/api/runs", json={"text": "  "}, headers=h).status_code == 400
    assert client.post("/api/runs", json={"text": "x" * 501}, headers=h).status_code == 400
    assert client.post("/api/runs", data="not json", headers=h).status_code == 400


def test_new_research_reports_database_persistence_failure(pg, client, monkeypatch):
    headers = login(client)

    def fail_start(*args, **kwargs):
        raise StoreError("Research history database is unavailable.")

    monkeypatch.setattr(pg.extensions["playground"].supervisor, "start", fail_start)
    response = client.post("/api/runs", json={"text": "find a chair"}, headers=headers)

    assert response.status_code == 503
    assert "database is unavailable" in response.get_json()["error"]


def test_playground_page_serves_with_csp_and_permissions_policy(pg, client):
    login(client)
    r = client.get("/playground")
    assert r.status_code == 200 and b"Command Center" in r.data
    assert b'id="error-dialog"' in r.data and b'aria-modal="true"' in r.data
    assert b'id="mic-dialog"' in r.data and b'id="loc-dialog"' in r.data
    assert "script-src 'self'" in r.headers["Content-Security-Policy"] and "'unsafe-inline'" not in r.headers["Content-Security-Policy"]
    assert "geolocation=(self)" in r.headers["Permissions-Policy"]
    assert client.get("/manifest.webmanifest").status_code == 200 and client.get("/sw.js").headers["Service-Worker-Allowed"] == "/"


def test_config_reports_capability_state_honestly(pg, client):
    server_only_secrets = {
        "DB_PASSWORD": "server-only-db-password",
        "SERPAPI_API_KEY": "server-only-search-key",
        "ANTHROPIC_API_KEY": "server-only-llm-key",
    }
    pg.config.update(server_only_secrets)
    login(client)
    config_response = client.get("/api/config")
    c = config_response.get_json()
    assert c["status"]["llm_available"] is False and c["status"]["store_persistent"] is False
    agent_keys = {a["key"] for a in c["agents"]}
    assert {"internet", "blinkit", "croma"} <= agent_keys and "boat" not in agent_keys
    assert "hi-IN" in {l[0] for l in c["languages"]}
    frontend_content = config_response.get_data(as_text=True) + client.get("/playground").get_data(as_text=True)
    assert all(value not in frontend_content for value in server_only_secrets.values())
    assert all(name not in frontend_content for name in server_only_secrets)


def test_search_usage_reports_monthly_runs_for_current_user(pg, client):
    login(client, user_id=1)
    pg.config["SEARCH_LIMIT_PER_MONTH"] = 10
    store = pg.extensions["playground"].supervisor.store
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    previous_month = month_start - timedelta(days=1)
    for run_id, user_id, created_at in (
        ("a" * 32, 1, now),
        ("b" * 32, 1, now - timedelta(days=1)),
        ("c" * 32, 1, previous_month),
        ("d" * 32, 2, now),
    ):
        store.save({"id": run_id, "user_id": user_id, "created_at": created_at.isoformat()})

    response = client.get("/api/usage")

    assert response.status_code == 200
    assert response.get_json()["search_usage"] == {"used": 2, "limit": 10, "remaining": 8}


def test_full_run_over_http_with_sse_and_exports(pg, client):
    h = login(client)
    rid = client.post("/api/runs", json={"text": "cotton t-shirt for men under 1000 rupees", "language": "en-IN"}, headers=h).get_json()["run_id"]
    run = finish(client, rid, h)
    assert run["status"] == "done" and run["report"]["offers"]
    stream = client.get(f"/api/runs/{rid}/events?after=0").get_data(as_text=True)
    assert "event: meeting_message" in stream and "event: report_ready" in stream and stream.rstrip().endswith("data: {}")
    resumed = client.get(f"/api/runs/{rid}/events", headers={"Last-Event-ID": "100000"}).get_data(as_text=True)
    assert "report_ready" not in resumed                                    # resume after the end replays nothing
    for fmt, mime in (("pdf", "application/pdf"), ("csv", "text/csv"), ("html", "text/html"), ("md", "text/markdown"), ("json", "application/json")):
        r = client.get(f"/api/runs/{rid}/export?format={fmt}")
        assert r.status_code == 200 and r.headers["Content-Type"].startswith(mime) and "attachment" in r.headers["Content-Disposition"]
    assert client.get(f"/api/runs/{rid}/export?format=exe").status_code == 400
    assert any(r["id"] == rid for r in client.get("/api/runs").get_json()["runs"])


def test_other_users_cannot_read_stream_export_or_cancel(pg, client):
    h = login(client, 1)
    rid = client.post("/api/runs", json={"text": "cotton t-shirt"}, headers=h).get_json()["run_id"]
    finish(client, rid, h)
    h2 = login(client, 2)
    for r in (client.get(f"/api/runs/{rid}"), client.get(f"/api/runs/{rid}/events"), client.get(f"/api/runs/{rid}/export?format=pdf"),
              client.post(f"/api/runs/{rid}/cancel", headers=h2), client.post(f"/api/runs/{rid}/followup", json={"text": "why?"}, headers=h2)):
        assert r.status_code == 404
    assert client.get("/api/runs").get_json()["runs"] == []


def test_followup_over_http(pg, client):
    h = login(client)
    rid = client.post("/api/runs", json={"text": "cotton t-shirt for men"}, headers=h).get_json()["run_id"]
    finish(client, rid, h)
    a = client.post(f"/api/runs/{rid}/followup", json={"text": "which is the cheapest?"}, headers=h).get_json()
    assert a["action"] == "question" and "₹499" in a["answer"]


def test_location_consent_flow_over_http(pg, client):
    h = login(client)
    assert client.get("/api/location").get_json()["location"] is None
    denied = client.post("/api/location", json={"mode": "device", "lat": 18.5, "lng": 73.8}, headers=h)
    assert denied.status_code == 400 and "OK" in denied.get_json()["error"]
    ok = client.post("/api/location", json={"mode": "manual", "text": "Pune 411001"}, headers=h).get_json()["location"]
    assert ok["postal_code"] == "411001" and "lat" not in json.dumps(ok)
    assert client.get("/api/location").get_json()["location"]["label"] == "Pune 411001"
    client.delete("/api/location", headers=h)
    assert client.get("/api/location").get_json()["location"] is None
    assert client.post("/api/location", json={"mode": "???"}, headers=h).status_code == 400


def test_run_limit_per_user(pg, client):
    import threading
    gate = threading.Event()
    sup = pg.extensions["playground"].supervisor
    orig = sup.search.shopping
    sup.search.shopping = lambda *a, **k: (gate.wait(3), orig(*a, **k))[1]
    h = login(client)
    codes = [client.post("/api/runs", json={"text": f"cotton t-shirt {i}"}, headers=h).status_code for i in range(3)]
    gate.set()
    assert codes == [202, 202, 429]
