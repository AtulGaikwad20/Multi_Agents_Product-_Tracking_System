import pymysql
import pytest

from check_setup import (
    check_data_sources,
    check_database,
    check_flask,
    check_history_storage,
    check_live,
    describe_database_error,
)
from app.playground.search import SearchUnavailable
from tests.conftest import needs_database


def statuses(results):
    return [status for status, _ in results]


@pytest.mark.parametrize(
    "code,fragment",
    [(1045, "username or password"), (1049, "does not exist"), (2003, "Could not reach"), (1044, "does not exist"), (9999, "code 9999")],
)
def test_database_errors_are_explained(code, fragment):
    assert fragment in describe_database_error(pymysql.OperationalError(code, "raw text with details"))


def test_raw_error_text_is_never_repeated():
    assert "secret-host" not in describe_database_error(pymysql.OperationalError(1045, "Access denied for secret-host"))


def test_flask_secret_checks():
    assert statuses(check_flask({"SECRET_KEY": ""})) == ["ACTION"]
    assert statuses(check_flask({"SECRET_KEY": "short"})) == ["ACTION"]
    assert statuses(check_flask({"SECRET_KEY": "x" * 40})) == ["OK"]


def test_database_configuration_rejects_invalid_port_and_store():
    from config import configuration_problems

    errors, _ = configuration_problems({
        "SECRET_KEY": "x" * 40,
        "DB_USER": "user",
        "DB_NAME": "research",
        "DB_PORT": "not-a-port",
        "PLAYGROUND_STORE": "unknown",
        "SEARCH_LIMIT_PER_MONTH": 0,
        "SEARCH_PROVIDER": "serpapi",
        "SERPAPI_API_KEY": "test-key",
        "AI_REPORT_ENABLED": False,
    })

    assert any("DB_PORT" in error for error in errors)
    assert any("PLAYGROUND_STORE" in error for error in errors)
    assert any("SEARCH_LIMIT_PER_MONTH" in error for error in errors)


def test_data_source_checks():
    assert "ACTION" in statuses(check_data_sources({"AI_REPORT_ENABLED": False}))          # no search key
    ok = check_data_sources({"SERPAPI_API_KEY": "k", "AI_REPORT_ENABLED": False, "USER_AGENT": "Bot/1 (+https://x.example)"})
    assert "ACTION" not in statuses(ok) and "OK" in statuses(ok)


def test_history_storage_reports_nonpersistent_mode():
    assert statuses(check_history_storage({"PLAYGROUND_STORE": "memory"})) == ["ACTION"]
    assert statuses(check_history_storage({"PLAYGROUND_STORE": "mysql"})) == ["OK"]


def test_unreachable_server_is_reported(app):
    app.config["DB_PORT"] = 1

    results = check_database(app)

    assert statuses(results) == ["ACTION"] and "Could not reach" in results[0][1]


@needs_database
def test_healthy_database_passes(app, database):
    results = check_database(app)

    assert "ACTION" not in statuses(results)
    assert sum(1 for status, text in results if status == "OK" and "every column" in text) == 2


@needs_database
def test_wrong_password_is_reported(app, database):
    app.config["DB_PASSWORD"] = "definitely-wrong"

    results = check_database(app)

    assert statuses(results) == ["ACTION"] and "username or password" in results[0][1]


@needs_database
def test_unknown_database_is_reported(app, database):
    app.config["DB_NAME"] = "no_such_database_here"

    results = check_database(app)

    assert statuses(results) == ["ACTION"]


@needs_database
def test_missing_required_table_and_column_are_reported(app, database):
    with database.cursor() as cursor:
        cursor.execute("DROP TABLE research_runs")
        cursor.execute("ALTER TABLE users DROP COLUMN account_status")

    texts = [text for _, text in check_database(app)]

    assert any('"research_runs" was not found' in text for text in texts)
    assert any("missing columns: account_status" in text for text in texts)


@needs_database
def test_check_never_changes_the_database(app, database):
    with database.cursor() as cursor:
        cursor.execute("INSERT INTO users (name, email, password_hash) VALUES ('A B', 'a@b.co', 'x')")
        cursor.execute("SHOW TABLES")
        before = sorted(row[0] for row in cursor.fetchall())

    check_database(app)

    with database.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM users")
        count = cursor.fetchone()[0]
        cursor.execute("SHOW TABLES")
        after = sorted(row[0] for row in cursor.fetchall())

    assert count == 1 and before == after


def test_live_check_reports_provider_problems(app):
    class Broken:
        configured = True

        def shopping(self, query, **kw):
            raise SearchUnavailable("The key was rejected.")

    app.extensions["playground"].supervisor.search = Broken()

    assert check_live(app) == [("ACTION", "The key was rejected.")]
