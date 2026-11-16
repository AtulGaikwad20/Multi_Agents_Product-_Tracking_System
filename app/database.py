from contextlib import contextmanager
from collections.abc import Mapping
from typing import Any, Generator, cast

import pymysql
from flask import current_app
from pymysql.connections import Connection
from pymysql.cursors import DictCursor


def get_db_connection() -> Connection:
    settings: Mapping[str, Any] = current_app.config

    return pymysql.connect(
        host=cast(str, settings["DB_HOST"]),
        port=cast(int, settings["DB_PORT"]),
        user=cast(str | None, settings["DB_USER"]),
        password=cast(str, settings["DB_PASSWORD"]),
        database=cast(str | None, settings["DB_NAME"]),
        cursorclass=pymysql.cursors.DictCursor,
    )


@contextmanager
def db_cursor(commit: bool = False) -> Generator[DictCursor, None, None]:
    connection = get_db_connection()

    try:
        with connection.cursor() as cursor:
            yield cast(DictCursor, cursor)

        if commit:
            connection.commit()

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()
