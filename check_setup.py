import logging
import sys
from collections.abc import Mapping, Sequence
from typing import Any, Callable, Protocol, cast

import pymysql
from flask import Flask

import app as _app
from app.database import get_db_connection


class _AppModule(Protocol):
    create_app: Callable[[Mapping[str, object] | None], Flask]


create_app = cast(_AppModule, _app).create_app

REQUIRED_TABLES: dict[str, list[str]] = {
    "users": ["user_id", "name", "email", "password_hash", "role", "account_status"],
    "research_runs": ["run_id", "user_id", "query_text", "language", "status", "parent_run_id",
                      "location_label", "record_json", "created_at", "updated_at"],
}

DATABASE_HELP: dict[int, str] = {
    1044: "This MySQL user cannot use that database, or the database does not exist. Check DB_USER and DB_NAME in .env.",
    1045: "MySQL rejected the username or password. Check DB_USER and DB_PASSWORD in .env.",
    1049: "That database does not exist on the server. Check DB_NAME in .env.",
    2003: "Could not reach the MySQL server. Check DB_HOST and DB_PORT in .env, and that MySQL is running.",
    2005: "The DB_HOST name could not be found. Check DB_HOST in .env.",
}


def describe_database_error(error: pymysql.MySQLError) -> str:
    code = error.args[0] if error.args and isinstance(error.args[0], int) else None

    if code in DATABASE_HELP:
        return DATABASE_HELP[code]

    if code is not None:
        return f"MySQL reported an error (code {code}). Check the DB_ settings in .env."

    return "The database connection failed. Check the DB_ settings in .env."


def check_flask(settings: Mapping[str, Any]) -> list[tuple[str, str]]:
    secret = settings.get("SECRET_KEY") or ""

    if not secret:
        return [("ACTION", "SECRET_KEY is empty. Add a long random value to .env.")]

    if len(secret) < 32:
        return [("ACTION", "SECRET_KEY is shorter than 32 characters. Use a longer random value.")]

    return [("OK", "SECRET_KEY is set.")]


def check_database(app: Flask) -> list[tuple[str, str]]:
    results: list[tuple[str, str]] = []

    with app.app_context():
        try:
            connection = get_db_connection()
        except pymysql.MySQLError as error:
            return [("ACTION", describe_database_error(error))]
        except Exception as error:
            return [("ACTION", f"The database connection failed ({type(error).__name__}). Check the DB_ settings in .env.")]

        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT VERSION() AS version, DATABASE() AS name")
                row = cast(dict[str, Any], cursor.fetchone())

                results.append(("OK", f'Connected to MySQL {row["version"]}, database "{row["name"]}".'))

                if "mariadb" in row["version"].lower():
                    results.append(("NOTE", "This server is MariaDB, not MySQL. The app works on it, but you asked for MySQL."))

                for table, columns in REQUIRED_TABLES.items():
                    cursor.execute("SHOW TABLES LIKE %s", (table,))

                    if not cursor.fetchone():
                        results.append(("ACTION", f'Table "{table}" was not found in this database.'))
                        continue

                    cursor.execute(f"SHOW COLUMNS FROM `{table}`")
                    present = {
                        cast(str, cast(Mapping[str, Any], item)["Field"])
                        for item in cursor.fetchall()
                    }
                    missing = [name for name in columns if name not in present]

                    if missing:
                        results.append(("ACTION", f'Table "{table}" is missing columns: {", ".join(missing)}.'))
                    else:
                        results.append(("OK", f'Table "{table}" has every column the app uses.'))
                        if table == "research_runs":
                            cursor.execute("SELECT COUNT(*) AS count FROM research_runs")
                            saved_runs = cast(
                                int, cast(dict[str, Any], cursor.fetchone())["count"]
                            )
                            results.append((
                                "OK",
                                f"Found {saved_runs} saved research run(s) in research_runs "
                                "(this is the app's search history and report table).",
                            ))

        except pymysql.MySQLError as error:
            results.append(("ACTION", describe_database_error(error)))

        finally:
            connection.close()

    return results


def check_history_storage(settings: Mapping[str, Any]) -> list[tuple[str, str]]:
    if settings.get("PLAYGROUND_STORE", "mysql") != "mysql":
        return [("ACTION", "PLAYGROUND_STORE is not mysql; searches are kept in memory and will not persist.")]

    return [("OK", "Search history is configured to persist in MySQL research_runs.")]


def check_data_sources(settings: Mapping[str, Any]) -> list[tuple[str, str]]:
    results: list[tuple[str, str]] = []

    provider = settings.get("SEARCH_PROVIDER", "serpapi")
    key_name = {"serpapi": "SERPAPI_API_KEY", "serper": "SERPER_API_KEY", "brave": "BRAVE_API_KEY", "zenserp": "ZENSERP_API_KEY", "searxng": "SEARXNG_URL", "google_cse": "GOOGLE_CSE_API_KEY"}.get(provider)
    if key_name is None:
        results.append(("ACTION", "SEARCH_PROVIDER must be serpapi, serper, zenserp, brave, searxng or google_cse."))
    elif settings.get(key_name):
        results.append(("OK", f"{key_name} is set (provider: {provider}), so live product research is possible."))
    else:
        results.append(("ACTION", f"{key_name} is empty. The playground cannot research products without it (there is no sample-data mode)."))

    if not settings.get("AI_REPORT_ENABLED"):
        results.append(("NOTE", "AI_REPORT_ENABLED is false, so multilingual understanding and non-English summaries are off."))
    elif settings.get("LLM_PROVIDER") == "openai_compat":
        ready = settings.get("LLM_BASE_URL") and settings.get("LLM_MODEL")
        results.append(("OK", f"AI model configured via {settings.get('LLM_BASE_URL')}.") if ready else
                       ("ACTION", "LLM_PROVIDER=openai_compat needs LLM_BASE_URL and LLM_MODEL (and LLM_API_KEY unless it is local)."))
    elif settings.get("ANTHROPIC_API_KEY"):
        results.append(("OK", "ANTHROPIC_API_KEY is set (multilingual understanding and spoken summaries on)."))
    else:
        results.append(("NOTE", "ANTHROPIC_API_KEY is empty: only English rule-based understanding is available."))

    if "set-a-contact-url" in (settings.get("USER_AGENT") or ""):
        results.append(("NOTE", "USER_AGENT has no contact URL. Set one so website owners can reach you."))

    return results


def check_live(app: Flask) -> list[tuple[str, str]]:
    from app.playground.search import SearchUnavailable

    results: list[tuple[str, str]] = []
    supervisor = app.extensions["playground"].supervisor

    try:
        hits = supervisor.search.shopping("wireless headphones")
        if hits:
            results.append(("OK", f"The search provider answered with {len(hits)} shopping listings."))
        else:
            hits = supervisor.search.web("wireless headphones buy online")
            results.append(("OK", f"The search provider answered with {len(hits)} web results (no shopping feed)."))
    except SearchUnavailable as error:
        results.append(("ACTION", error.user_message))

    if supervisor.llm.available:
        reply = supervisor.llm.text("Reply with one short sentence.", "connection test", max_tokens=40)
        results.append(("OK", "The AI service answered.") if reply else
                       ("ACTION", "The AI service did not answer. Check the key, model name and (for a local model) that it is running."))

    return results


def print_section(title: str, results: Sequence[tuple[str, str]]) -> None:
    print(title)

    for status, message in results:
        print(f"  {status:<7} {message}")

    print()


def main(arguments: Sequence[str]) -> int:
    logging.getLogger("app").setLevel(logging.ERROR)

    try:
        # Allow this diagnostic to inspect incomplete schemas; normal app startup
        # still checks the required tables before serving requests.
        app: Flask = create_app({"SKIP_PLAYGROUND_STORE_CHECK": True})
    except RuntimeError as error:
        print(error)
        return 1

    print("RESQUORA setup check\n")

    sections = [
        ("Flask", check_flask(app.config)),
        ("History storage", check_history_storage(app.config)),
        ("MySQL", check_database(app)),
        ("Data sources", check_data_sources(app.config)),
    ]

    if "--live" in arguments:
        sections.append(("Live test (uses one search call and one short AI reply)", check_live(app)))

    problems = 0

    for title, results in sections:
        print_section(title, results)
        problems += sum(1 for status, _ in results if status == "ACTION")

    if problems:
        print(f"{problems} thing(s) need attention.")
        return 1

    print("Everything checks out.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
