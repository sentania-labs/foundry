"""The Crucible handoff: bundle export and the one-way migrated marker."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from pathlib import Path

import pytest

from foundry_ledger import db, ledger
from foundry_ledger.model import TIMESTAMP_RE, LedgerError, bundle_content_hash, sha256_bytes


def test_crucible_bundle_shape_and_hashes(imported: Path, cli: Callable[..., int], db_path: Path, tmp_path: Path) -> None:
    out = tmp_path / "handoff"
    assert cli("export", "--format", "crucible", "--dir", str(out)) == 0
    bundle = json.loads((out / "crucible.json").read_text(encoding="utf-8"))
    assert list(bundle) == ["schema_version", "source", "tasks", "events", "counts", "content_sha256"]
    assert bundle["schema_version"] == "1.0"
    assert bundle["source"]["tool"].startswith("foundry-ledger ")
    assert bundle["source"]["db_sha256"] == sha256_bytes(db_path.read_bytes())
    assert TIMESTAMP_RE.fullmatch(bundle["source"]["exported_at"])
    assert bundle["counts"] == {"tasks": 4, "events": 11}
    assert [t["id"] for t in bundle["tasks"]] == ["EX-0001", "EX-0002", "EX-0003", "EX-0004"]
    assert [e["seq"] for e in bundle["events"]] == list(range(1, 12))
    assert set(bundle["events"][0]) == {"seq", "ts", "task", "event", "who", "detail"}
    # The receiver can recompute the content hash from tasks and events alone.
    assert bundle["content_sha256"] == bundle_content_hash(bundle["tasks"], bundle["events"])
    # Tasks in the bundle equal the YAML export's records.
    conn = db.connect(db_path, readonly=True)
    assert bundle["tasks"] == ledger.list_tasks(conn)


def test_crucible_bundle_hash_changes_with_content(imported: Path, cli: Callable[..., int], db_path: Path, tmp_path: Path) -> None:
    a = ledger.crucible_bundle(db.connect(db_path, readonly=True), db_path)
    assert cli("event", "EX-0003", "note", "--who", "foundry", "--detail", "x") == 0
    b = ledger.crucible_bundle(db.connect(db_path, readonly=True), db_path)
    assert a["content_sha256"] != b["content_sha256"]
    assert a["source"]["db_sha256"] != b["source"]["db_sha256"]


def test_crucible_export_refuses_overwrite_and_is_readonly(imported: Path, cli: Callable[..., int], db_path: Path, tmp_path: Path) -> None:
    out = tmp_path / "handoff"
    before = db_path.read_bytes()
    assert cli("export", "--format", "crucible", "--dir", str(out)) == 0
    assert cli("export", "--format", "crucible", "--dir", str(out)) == 1
    assert cli("export", "--format", "crucible", "--dir", str(out), "--force") == 0
    assert db_path.read_bytes() == before


def test_mark_migrated_freezes_every_write(imported: Path, cli: Callable[..., int], db_path: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli("mark-migrated", "--crucible-import", "crucible-import-42") == 0
    assert "crucible-import-42" in capsys.readouterr().out
    conn = db.connect(db_path)
    assert ledger.migrated_marker(conn) == "crucible-import-42"
    row = sqlite3.connect(db_path).execute("SELECT id, crucible_import, marked_at FROM migrated").fetchone()
    assert row[0] == 1 and row[1] == "crucible-import-42" and TIMESTAMP_RE.fullmatch(row[2])

    snapshot = db_path.read_bytes()
    writes = [
        ("add", "--title", "late"),
        ("update", "EX-0003", "--title", "late"),
        ("update", "EX-0003", "--state", "running"),
        ("event", "EX-0003", "running", "--who", "foundry"),
        ("import", "--tasks", str(imported / "tasks"), "--events", str(imported / "events.jsonl")),
        ("mark-migrated", "--crucible-import", "another"),
    ]
    for args in writes:
        capsys.readouterr()
        assert cli(*args) == 1, args
        assert "crucible-import-42" in capsys.readouterr().err, args
    assert db_path.read_bytes() == snapshot

    for fn in (
        lambda: ledger.add_task(conn, {"title": "x"}),
        lambda: ledger.update_task(conn, "EX-0003", {"title": "x"}),
        lambda: ledger.append_event(conn, "EX-0003", "note", who="foundry", detail=None),
        lambda: ledger.mark_migrated(conn, "another"),
    ):
        with pytest.raises(LedgerError, match="crucible-import-42"):
            fn()

    # Reads, verify, and both exports keep working on a frozen ledger.
    assert cli("list") == 0 and cli("live") == 0 and cli("show", "EX-0001") == 0
    assert cli("verify") == 0
    assert cli("export", "--dir", str(tmp_path / "yaml")) == 0
    assert cli("export", "--format", "crucible", "--dir", str(tmp_path / "bundle")) == 0


def test_mark_migrated_rejects_empty_id(imported: Path, cli: Callable[..., int]) -> None:
    assert cli("mark-migrated", "--crucible-import", "  ") == 1


def test_older_db_needs_migrate_before_mark_migrated(cli: Callable[..., int], db_path: Path) -> None:
    assert cli("init") == 0
    conn = sqlite3.connect(db_path)
    conn.execute("DROP TABLE migrated")
    conn.execute("DELETE FROM schema_migrations WHERE version = 2")
    conn.commit()
    assert cli("mark-migrated", "--crucible-import", "x") == 1  # migrations pending
    assert cli("migrate") == 0
    assert cli("mark-migrated", "--crucible-import", "x") == 0
