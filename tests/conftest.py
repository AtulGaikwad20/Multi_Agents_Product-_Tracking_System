import os

import pymysql
import pytest

from app import create_app

TEST_DB = {
    "DB_HOST": os.getenv("TEST_DB_HOST", "127.0.0.1"),
    "DB_PORT": int(os.getenv("TEST_DB_PORT", "3306")),
    "DB_USER": os.getenv("TEST_DB_USER"),
    "DB_PASSWORD": os.getenv("TEST_DB_PASSWORD"),
    "DB_NAME": os.getenv("TEST_DB_NAME"),
}

TEST_SETTINGS = {
    "TESTING": True,
    "DEBUG": False,
    "SECRET_KEY": "test-secret-key-test-secret-key-1234",
    "PLAYGROUND_STORE": "memory",
    "AI_REPORT_ENABLED": False,
    "CACHE_TTL_SECONDS": 60,
    "MAX_RESULTS_PER_SEARCH": 40,
    "HTTP_TIMEOUT_SECONDS": 5,
}

USERS_TABLE = """
CREATE TABLE IF NOT EXISTS users (
    user_id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    email VARCHAR(255) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    role VARCHAR(20) NOT NULL DEFAULT 'user',
    account_status VARCHAR(20) NOT NULL DEFAULT 'active'
)
"""

test_database_is_safe = bool(
    TEST_DB["DB_USER"] and TEST_DB["DB_NAME"] and "test" in TEST_DB["DB_NAME"].lower()
)

needs_database = pytest.mark.skipif(
    not test_database_is_safe,
    reason="Set TEST_DB_USER, TEST_DB_PASSWORD and TEST_DB_NAME to run database tests. "
    "The database name must contain the word test, so a real database is never used.",
)


@pytest.fixture
def app():
    return create_app({**TEST_SETTINGS, **TEST_DB})


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def database(app):
    connection = pymysql.connect(
        host=TEST_DB["DB_HOST"],
        port=TEST_DB["DB_PORT"],
        user=TEST_DB["DB_USER"],
        password=TEST_DB["DB_PASSWORD"],
        database=TEST_DB["DB_NAME"],
        autocommit=True,
    )

    with connection.cursor() as cursor:
        cursor.execute("DROP TABLE IF EXISTS users")
        cursor.execute(USERS_TABLE)

    yield connection

    connection.close()
