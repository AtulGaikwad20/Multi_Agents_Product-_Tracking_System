import json
from datetime import datetime, timezone

import pymysql
import pytest

from app.playground.store import MySqlStore, StoreError
from app.playground.supervisor import Supervisor
from tests.playground.fakes import FakeFetcher, FakeLlm, FakeSearch


class FakeCursor:
    def __init__(self):
        self.sql = None
        self.args = None
        self.statements = []
        self.executions = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, args=()):
        self.sql, self.args = sql, args
        self.statements.append(sql)
        self.executions.append((sql, args))

    def fetchone(self):
        return None

    def fetchall(self):
        return []


class FakeConnection:
    def __init__(self):
        self.query = FakeCursor()
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def cursor(self):
        return self.query

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


def test_mysql_store_checks_required_tables_and_saves_full_run_record():
    connection = FakeConnection()
    store = MySqlStore(lambda: connection)

    store.check()
    record = {
        "id": "a" * 32,
        "user_id": 7,
        "text": "find a chair",
        "language": "en-IN",
        "status": "done",
        "parent_run_id": None,
        "location_label": None,
        "events": [{"seq": 1, "type": "run_started"}],
        "brief": {"tags": ["home_furniture"]},
        "report": {"offers": [
            {"title": "Chair", "brand": "Acme"},
            {"title": "chair", "brand": "acme"},
        ]},
    }
    store.save(record)

    assert connection.query.statements[:2] == [
        "SELECT user_id FROM users LIMIT 0",
        "SELECT run_id FROM research_runs LIMIT 0",
    ]
    assert len(connection.query.statements) == 3
    assert connection.query.statements[2].startswith("INSERT INTO research_runs")
    assert connection.query.executions[2][1][:7] == (
        record["id"], 7, "find a chair", "en-IN", "done", None, None
    )
    assert json.loads(connection.query.executions[2][1][7]) == record
    assert connection.committed and connection.closed


def test_mysql_store_saves_report_without_a_product_catalog():
    connection = FakeConnection()
    store = MySqlStore(lambda: connection)
    record = {
        "id": "b" * 32,
        "user_id": 7,
        "text": "find a chair",
        "language": "en-IN",
        "status": "queued",
        "report": {"offers": [{"title": "Chair", "brand": "Acme"}]},
    }

    store.save(record)

    assert len(connection.query.statements) == 1
    assert connection.query.sql.startswith("INSERT INTO research_runs")
    assert connection.committed


def test_mysql_store_counts_runs_since_utc_month_start():
    connection = FakeConnection()
    connection.query.fetchone = lambda: {"used": 4}
    store = MySqlStore(lambda: connection)
    since = datetime(2026, 11, 1, tzinfo=timezone.utc)

    assert store.count_since(7, since) == 4
    sql, args = connection.query.executions[0]
    assert sql == "SELECT COUNT(*) AS used FROM research_runs WHERE user_id=%s AND created_at >= %s"
    assert args == (7, datetime(2026, 11, 1))


def test_mysql_store_wraps_database_failures_and_rolls_back():
    connection = FakeConnection()

    def fail_query(sql, args=()):
        raise pymysql.OperationalError(2003, "database unavailable")

    connection.query.execute = fail_query
    store = MySqlStore(lambda: connection)

    with pytest.raises(StoreError, match="MySQL persistence operation failed"):
        store._run("SELECT run_id FROM research_runs LIMIT 0")

    assert connection.rolled_back and connection.closed


def test_supervisor_saves_search_before_starting_background_worker(monkeypatch):
    order = []

    class RecordingStore:
        def save(self, record):
            order.append(("save", record["status"], record["text"]))

    class DeferredThread:
        def __init__(self, target, args, daemon, name):
            self.target, self.args = target, args

        def start(self):
            order.append(("worker",))

    monkeypatch.setattr("app.playground.supervisor.threading.Thread", DeferredThread)
    supervisor = Supervisor(FakeSearch(), FakeFetcher(), FakeLlm(), RecordingStore())

    run = supervisor.start(7, "find a chair", "en-IN")

    assert run.status == "queued"
    assert order == [("save", "queued", "find a chair"), ("worker",)]
