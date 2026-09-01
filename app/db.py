"""SQLite persistence layer for tasks and runs."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

DATA_DIR = os.environ.get("DATA_DIR", "/config")
DB_PATH = os.path.join(DATA_DIR, "rsyncwebui.db")
LEGACY_DB_PATH = os.path.join(DATA_DIR, "rsyncweb.db")

_lock = threading.RLock()


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect():
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        with _lock:
            yield conn
            conn.commit()
    finally:
        conn.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT    NOT NULL,
    description  TEXT    NOT NULL DEFAULT '',
    source       TEXT    NOT NULL,
    destination  TEXT    NOT NULL,
    options      TEXT    NOT NULL DEFAULT '{}',
    schedule     TEXT    NOT NULL DEFAULT '',
    schedule_on  INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT    NOT NULL,
    updated_at   TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id      INTEGER REFERENCES tasks(id) ON DELETE CASCADE,
    task_name    TEXT    NOT NULL DEFAULT '',
    command      TEXT    NOT NULL DEFAULT '',
    trigger      TEXT    NOT NULL DEFAULT 'manual',
    status       TEXT    NOT NULL DEFAULT 'running',
    exit_code    INTEGER,
    started_at   TEXT    NOT NULL,
    finished_at  TEXT,
    log          TEXT    NOT NULL DEFAULT '',
    summary      TEXT    NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_runs_task ON runs(task_id, id DESC);
"""


def init_db() -> None:
    # Installations from the previous version keep their tasks.
    if os.path.exists(LEGACY_DB_PATH) and not os.path.exists(DB_PATH):
        os.makedirs(DATA_DIR, exist_ok=True)
        for suffix in ("", "-wal", "-shm"):
            old = LEGACY_DB_PATH + suffix
            if os.path.exists(old):
                os.rename(old, DB_PATH + suffix)

    with connect() as conn:
        conn.executescript(SCHEMA)


# --------------------------------------------------------------------------- tasks

def _task_row(row: sqlite3.Row) -> dict:
    task = dict(row)
    try:
        task["options"] = json.loads(task["options"] or "{}")
    except json.JSONDecodeError:
        task["options"] = {}
    task["schedule_on"] = bool(task["schedule_on"])
    return task


def list_tasks() -> list[dict]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM tasks ORDER BY name COLLATE NOCASE").fetchall()
        tasks = [_task_row(r) for r in rows]
        for task in tasks:
            last = conn.execute(
                "SELECT id, status, exit_code, started_at, finished_at FROM runs "
                "WHERE task_id = ? ORDER BY id DESC LIMIT 1",
                (task["id"],),
            ).fetchone()
            task["last_run"] = dict(last) if last else None
        return tasks


def get_task(task_id: int) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return _task_row(row) if row else None


def create_task(data: dict) -> int:
    now = utcnow()
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO tasks (name, description, source, destination, options, "
            "schedule, schedule_on, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (
                data["name"],
                data.get("description", ""),
                data["source"],
                data["destination"],
                json.dumps(data.get("options", {})),
                data.get("schedule", ""),
                int(bool(data.get("schedule_on"))),
                now,
                now,
            ),
        )
        return int(cur.lastrowid)


def update_task(task_id: int, data: dict) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE tasks SET name=?, description=?, source=?, destination=?, options=?, "
            "schedule=?, schedule_on=?, updated_at=? WHERE id=?",
            (
                data["name"],
                data.get("description", ""),
                data["source"],
                data["destination"],
                json.dumps(data.get("options", {})),
                data.get("schedule", ""),
                int(bool(data.get("schedule_on"))),
                utcnow(),
                task_id,
            ),
        )


def delete_task(task_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))


# ---------------------------------------------------------------------------- runs

def create_run(task_id: int | None, task_name: str, command: str, trigger: str) -> int:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO runs (task_id, task_name, command, trigger, status, started_at) "
            "VALUES (?,?,?,?, 'running', ?)",
            (task_id, task_name, command, trigger, utcnow()),
        )
        return int(cur.lastrowid)


def append_log(run_id: int, chunk: str) -> None:
    with connect() as conn:
        conn.execute("UPDATE runs SET log = log || ? WHERE id = ?", (chunk, run_id))


def finish_run(run_id: int, status: str, exit_code: int | None, summary: dict) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE runs SET status=?, exit_code=?, finished_at=?, summary=? WHERE id=?",
            (status, exit_code, utcnow(), json.dumps(summary), run_id),
        )


def get_run(run_id: int) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if not row:
            return None
        run = dict(row)
        try:
            run["summary"] = json.loads(run["summary"] or "{}")
        except json.JSONDecodeError:
            run["summary"] = {}
        return run


def list_runs(task_id: int | None = None, limit: int = 50) -> list[dict]:
    with connect() as conn:
        if task_id is None:
            rows = conn.execute(
                "SELECT id, task_id, task_name, trigger, status, exit_code, started_at, "
                "finished_at FROM runs ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, task_id, task_name, trigger, status, exit_code, started_at, "
                "finished_at FROM runs WHERE task_id = ? ORDER BY id DESC LIMIT ?",
                (task_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]


def mark_orphaned_runs() -> None:
    """Runs still flagged 'running' after a restart can never finish."""
    with connect() as conn:
        conn.execute(
            "UPDATE runs SET status='aborted', finished_at=? WHERE status='running'",
            (utcnow(),),
        )


def prune_runs(keep_per_task: int = 40) -> None:
    with connect() as conn:
        conn.execute(
            """
            DELETE FROM runs WHERE id IN (
                SELECT id FROM (
                    SELECT id, ROW_NUMBER() OVER (
                        PARTITION BY task_id ORDER BY id DESC
                    ) AS rn FROM runs
                ) WHERE rn > ?
            )
            """,
            (keep_per_task,),
        )
