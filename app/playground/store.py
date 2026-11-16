"""Run persistence so searches and reports follow the user across devices.

MySQL (the project's existing database) is the production store. The memory
store exists for tests and single-process development only and is reported as
such by /api/config.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone

import pymysql


class StoreError(RuntimeError):
    """A safe, user-facing failure from the persistent research store."""


class MemoryStore:
    name = "memory"
    persistent = False

    def __init__(self):
        self._runs = {}
        self._lock = threading.Lock()

    def save(self, record):
        with self._lock:
            self._runs[record["id"]] = json.loads(json.dumps(record, default=str))

    def get(self, user_id, run_id):
        with self._lock:
            rec = self._runs.get(run_id)
        return rec if rec and rec["user_id"] == user_id else None

    def list(self, user_id, limit=30):
        with self._lock:
            rows = [r for r in self._runs.values() if r["user_id"] == user_id]
        rows.sort(key=lambda r: r["updated_at"], reverse=True)
        return [self._summary(r) for r in rows[:limit]]

    def count_since(self, user_id, since):
        with self._lock:
            rows = [r for r in self._runs.values() if r["user_id"] == user_id]
        count = 0
        for row in rows:
            created_at = datetime.fromisoformat(str(row["created_at"]).replace("Z", "+00:00"))
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            if created_at >= since:
                count += 1
        return count

    @staticmethod
    def _summary(r):
        return {"id": r["id"], "text": r["text"], "status": r["status"], "language": r["language"],
                "created_at": r["created_at"], "updated_at": r["updated_at"],
                "offers": len((r.get("report") or {}).get("offers", [])),
                "parent_run_id": r.get("parent_run_id")}

    def delete(self, user_id, run_id):
        with self._lock:
            rec = self._runs.get(run_id)
            if rec and rec["user_id"] == user_id:
                del self._runs[run_id]
                return True
        return False


class MySqlStore:
    name = "mysql"
    persistent = True

    def __init__(self, connect):
        self._connect = connect   # callable returning a pymysql connection (DictCursor)

    def check(self):
        try:
            self._run("SELECT user_id FROM users LIMIT 0")
            self._run("SELECT run_id FROM research_runs LIMIT 0")
        except StoreError as error:
            raise StoreError(
                "The application database is unavailable or incomplete. Verify the DB_* "
                "settings and apply migrations/000_base_tables.sql followed by "
                "migrations/001_research_runs.sql."
            ) from error

    def _run(self, sql, args=(), fetch=None, commit=False):
        conn = None
        try:
            conn = self._connect()
            with conn.cursor() as cur:
                cur.execute(sql, args)
                out = cur.fetchone() if fetch == "one" else cur.fetchall() if fetch == "all" else None
            if commit:
                conn.commit()
            return out
        except pymysql.MySQLError as error:
            if conn is not None:
                conn.rollback()
            raise StoreError(
                "MySQL persistence operation failed. Check database settings, "
                "permissions, and the database migrations."
            ) from error
        finally:
            if conn is not None:
                conn.close()

    def save(self, record):
        self._run(
            """INSERT INTO research_runs
               (run_id, user_id, query_text, language, status, parent_run_id, location_label, record_json)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
               ON DUPLICATE KEY UPDATE status=VALUES(status), language=VALUES(language),
                 location_label=VALUES(location_label), record_json=VALUES(record_json)""",
            (record["id"], record["user_id"], record["text"][:500], record["language"][:12], record["status"],
             record.get("parent_run_id"), record.get("location_label"),
             json.dumps(record, ensure_ascii=False, default=str)),
            commit=True)

    def get(self, user_id, run_id):
        row = self._run("SELECT record_json FROM research_runs WHERE run_id=%s AND user_id=%s",
                        (run_id, user_id), fetch="one")
        return json.loads(row["record_json"]) if row else None

    def list(self, user_id, limit=30):
        rows = self._run(
            """SELECT run_id, query_text, status, language, created_at, updated_at, parent_run_id,
                      JSON_LENGTH(JSON_EXTRACT(record_json, '$.report.offers')) AS offers
               FROM research_runs WHERE user_id=%s ORDER BY updated_at DESC LIMIT %s""",
            (user_id, int(limit)), fetch="all") or []
        return [{"id": r["run_id"], "text": r["query_text"], "status": r["status"], "language": r["language"],
                 "created_at": r["created_at"].isoformat() if hasattr(r["created_at"], "isoformat") else str(r["created_at"]),
                 "updated_at": r["updated_at"].isoformat() if hasattr(r["updated_at"], "isoformat") else str(r["updated_at"]),
                 "offers": r["offers"] or 0, "parent_run_id": r["parent_run_id"]} for r in rows]

    def count_since(self, user_id, since):
        since_utc = since.astimezone(timezone.utc).replace(tzinfo=None)
        row = self._run(
            "SELECT COUNT(*) AS used FROM research_runs WHERE user_id=%s AND created_at >= %s",
            (user_id, since_utc), fetch="one")
        return int(row["used"])

    def delete(self, user_id, run_id):
        self._run("DELETE FROM research_runs WHERE run_id=%s AND user_id=%s", (run_id, user_id), commit=True)
        return True
