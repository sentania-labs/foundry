"""Database location, connections, transactions, and migrations."""

from __future__ import annotations

import os
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from importlib import resources
from pathlib import Path

from .model import LedgerError, now_local

ENV_VAR = "FOUNDRY_LEDGER_DB"
DEFAULT_RELATIVE = Path(".local") / "state" / "foundry" / "ledger.sqlite"
MIGRATION_RE = re.compile(r"^(\d{4})_([A-Za-z0-9_]+)\.sql$")


def resolve_db_path(explicit: str | None) -> Path:
    """--db wins, then FOUNDRY_LEDGER_DB, then ~/.local/state/foundry/ledger.sqlite."""
    if explicit is not None:
        if explicit == "":
            raise LedgerError("--db is empty")
        return Path(explicit).expanduser()
    env = os.environ.get(ENV_VAR)
    if env is not None:
        if env == "":
            raise LedgerError(f"{ENV_VAR} is set but empty")
        return Path(env).expanduser()
    return Path.home() / DEFAULT_RELATIVE


def connect(path: Path, *, readonly: bool = False, create: bool = False) -> sqlite3.Connection:
    """Open the ledger. Transactions are explicit (see `transaction`).

    Only `init` passes create=True; every other command refuses a missing
    file rather than letting sqlite3 leave an empty one behind.
    """
    if not path.exists() and not create:
        raise LedgerError(f"no ledger at {path}; run 'foundry-ledger init' first")
    if readonly:
        uri = path.resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, isolation_level=None)
    else:
        conn = sqlite3.connect(path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """One write = one transaction. Any exception rolls everything back."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def list_migrations() -> list[tuple[int, str, str]]:
    """(version, name, sql) for every packaged migration, in version order."""
    found: list[tuple[int, str, str]] = []
    root = resources.files("foundry_ledger") / "migrations"
    for entry in root.iterdir():
        match = MIGRATION_RE.match(entry.name)
        if not match:
            continue
        found.append((int(match.group(1)), match.group(2), entry.read_text("utf-8")))
    found.sort(key=lambda item: item[0])
    versions = [v for v, _, _ in found]
    if versions != list(range(1, len(versions) + 1)):
        raise LedgerError(f"migration versions must be 1..N without gaps, got {versions}")
    return found


def applied_migrations(conn: sqlite3.Connection) -> dict[int, str]:
    """Recorded migrations. Safe on a read-only connection (no DDL issued)."""
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'"
    ).fetchone()
    if exists is None:
        return {}
    rows = conn.execute("SELECT version, name FROM schema_migrations ORDER BY version")
    return {int(r["version"]): str(r["name"]) for r in rows}


def migrate(conn: sqlite3.Connection) -> list[int]:
    """Apply pending migrations in order. Idempotent. Returns versions applied."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " version INTEGER PRIMARY KEY,"
        " name TEXT NOT NULL,"
        " applied_at TEXT NOT NULL)"
    )
    applied = applied_migrations(conn)
    available = list_migrations()
    done: list[int] = []
    for version, name, sql in available:
        if version in applied:
            if applied[version] != name:
                raise LedgerError(
                    f"migration {version:04d} recorded as {applied[version]!r}"
                    f" but packaged as {name!r}"
                )
            continue
        script = (
            "BEGIN;\n"
            f"{sql}\n"
            "INSERT INTO schema_migrations (version, name, applied_at)"
            f" VALUES ({version}, '{name}', '{now_local()}');\n"
            "COMMIT;\n"
        )
        try:
            conn.executescript(script)
        except sqlite3.Error as exc:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise LedgerError(f"migration {version:04d}_{name} failed: {exc}") from exc
        done.append(version)
    unknown = sorted(set(applied) - {v for v, _, _ in available})
    if unknown:
        raise LedgerError(
            f"database has migrations {unknown} this tool does not know; upgrade the tool"
        )
    return done


def init(path: Path) -> tuple[sqlite3.Connection, list[int]]:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path, create=True)
    try:
        return conn, migrate(conn)
    except BaseException:
        conn.close()
        raise


def require_migrated(conn: sqlite3.Connection) -> None:
    """Refuse to operate on a database that is behind the packaged migrations."""
    applied = applied_migrations(conn)
    pending = [v for v, _, _ in list_migrations() if v not in applied]
    if pending:
        raise LedgerError(f"migrations pending: {pending}; run 'foundry-ledger migrate'")
