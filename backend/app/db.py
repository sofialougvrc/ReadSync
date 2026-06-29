import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .config import settings


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def loads(value: str | None, fallback: Any = None) -> Any:
    if value is None:
        return fallback
    try:
        return json.loads(value)
    except Exception:
        return fallback


@contextmanager
def connect():
    settings.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(settings.sqlite_path, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA busy_timeout = 30000")
    try:
        yield con
        con.commit()
    finally:
        con.close()


def one(query: str, params: Iterable[Any] = ()):
    with connect() as con:
        row = con.execute(query, tuple(params)).fetchone()
        return dict(row) if row else None


def all_rows(query: str, params: Iterable[Any] = ()):
    with connect() as con:
        return [dict(row) for row in con.execute(query, tuple(params)).fetchall()]


def execute(query: str, params: Iterable[Any] = ()) -> int:
    with connect() as con:
        cur = con.execute(query, tuple(params))
        return int(cur.lastrowid)


def init_db() -> None:
    schema_path = Path(__file__).with_name("schema.sql")
    with connect() as con:
        con.executescript(schema_path.read_text())
        con.execute(
            "INSERT OR IGNORE INTO settings(key, value, updated_at) VALUES (?, ?, ?)",
            ("ollama_endpoint", settings.ollama_endpoint, utcnow()),
        )
        con.execute(
            "INSERT OR IGNORE INTO settings(key, value, updated_at) VALUES (?, ?, ?)",
            ("ollama_model", settings.ollama_model, utcnow()),
        )


def log_activity(kind: str, title: str, detail: str = "", ref_type: str = "", ref_id: int | None = None) -> None:
    execute(
        "INSERT INTO activity(kind, title, detail, ref_type, ref_id, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (kind, title, detail, ref_type, ref_id, utcnow()),
    )


def log_error(stage: str, message: str, payload: Any = None, paper_id: int | None = None) -> None:
    execute(
        "INSERT INTO errors(stage, message, payload_json, paper_id, created_at) VALUES (?, ?, ?, ?, ?)",
        (stage, message[:4000], dumps(payload or {}), paper_id, utcnow()),
    )
