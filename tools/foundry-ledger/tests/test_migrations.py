from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path

import pytest

from foundry_ledger import db
from foundry_ledger.model import LedgerError


def test_init_applies_all_migrations_and_records_them(cli: Callable[..., int], db_path: Path) -> None:
    assert cli("init") == 0
    conn = sqlite3.connect(db_path)
    versions = [r[0] for r in conn.execute("SELECT version FROM schema_migrations ORDER BY version")]
    assert versions == [v for v, _, _ in db.list_migrations()]
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"tasks", "events", "state_transitions", "import_manifest", "schema_migrations"} <= tables


def test_migrate_is_idempotent(cli: Callable[..., int], db_path: Path) -> None:
    assert cli("init") == 0
    conn = db.connect(db_path)
    before = conn.execute("SELECT version, name, applied_at FROM schema_migrations").fetchall()
    assert db.migrate(conn) == []
    assert cli("migrate") == 0
    after = conn.execute("SELECT version, name, applied_at FROM schema_migrations").fetchall()
    assert [tuple(r) for r in before] == [tuple(r) for r in after]


def test_migrate_refuses_renamed_migration(cli: Callable[..., int], db_path: Path) -> None:
    assert cli("init") == 0
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE schema_migrations SET name = 'something_else' WHERE version = 1")
    conn.commit()
    with pytest.raises(LedgerError, match="recorded as"):
        db.migrate(db.connect(db_path))


def test_migrate_refuses_unknown_future_migration(cli: Callable[..., int], db_path: Path) -> None:
    assert cli("init") == 0
    conn = sqlite3.connect(db_path)
    conn.execute("INSERT INTO schema_migrations VALUES (99, 'future', '2026-09-16 00:00 CDT')")
    conn.commit()
    with pytest.raises(LedgerError, match="does not know"):
        db.migrate(db.connect(db_path))


def test_commands_refuse_unmigrated_db(db_path: Path, cli: Callable[..., int]) -> None:
    sqlite3.connect(db_path).close()  # empty file, no schema
    assert cli("list") == 1


def test_migrate_needs_init_first(cli: Callable[..., int]) -> None:
    assert cli("migrate") == 1
