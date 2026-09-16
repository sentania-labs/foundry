"""Import the fixture ledger and export it back.

Normalization used for task YAML equality: both sides are parsed with
yaml.safe_load and compared as Python objects. Key order is preserved by the
export (README field order, which every source file follows), so the
normalization only absorbs formatting: quoting style, comments, list indent,
and line wrapping in the hand-written files EX-0001 and EX-0002. The
machine-written files EX-0003 and EX-0004 and events.jsonl are compared byte
for byte.

The shifted fixture reproduces a real defect seen in Foundry's own ledger
(fields shifted by one, leaving state null and blockers a string). The state
CHECK must reject it loudly and the import must write nothing.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path

import pytest
import yaml

from foundry_ledger import db, ledger
from foundry_ledger.model import LedgerError
from tests.conftest import LEDGER_CLEAN, LEDGER_SHIFTED


def _yaml_files(directory: Path) -> list[Path]:
    return sorted(directory.glob("*.yaml"))


def test_import_counts_and_manifest(imported: Path, db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 4
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 11
    manifest = conn.execute("SELECT kind, record_count FROM import_manifest").fetchall()
    assert sorted(manifest) == sorted([("task", 1)] * 4 + [("events", 11), ("transitions", 8)])


def test_export_round_trip_equals_source(imported: Path, cli: Callable[..., int], tmp_path: Path) -> None:
    out = tmp_path / "export"
    assert cli("export", "--dir", str(out)) == 0
    src_files = _yaml_files(imported / "tasks")
    out_files = _yaml_files(out / "tasks")
    assert [p.name for p in src_files] == [p.name for p in out_files]
    byte_equal: list[str] = []
    for s, o in zip(src_files, out_files, strict=True):
        assert yaml.safe_load(s.read_text()) == yaml.safe_load(o.read_text()), s.name
        assert list(yaml.safe_load(o.read_text()).keys()) == list(yaml.safe_load(s.read_text()).keys())
        if s.read_bytes() == o.read_bytes():
            byte_equal.append(s.name)
    # The machine-written source files come back byte for byte.
    assert byte_equal == ["EX-0003.yaml", "EX-0004.yaml"]
    assert (out / "events.jsonl").read_bytes() == (imported / "events.jsonl").read_bytes()


def test_export_of_reexport_is_stable(imported: Path, cli: Callable[..., int], tmp_path: Path) -> None:
    """Exporting, importing the export, and exporting again is byte-identical."""
    first = tmp_path / "first"
    assert cli("export", "--dir", str(first)) == 0
    second_db = tmp_path / "second.sqlite"
    assert ledger  # keep import explicit for readers
    conn, _ = db.init(second_db)
    ledger.import_sources(conn, first / "tasks", first / "events.jsonl")
    conn.close()
    second = tmp_path / "second"
    ledger.export(db.connect(second_db, readonly=True), second)
    for a, b in zip(_yaml_files(first / "tasks"), _yaml_files(second / "tasks"), strict=True):
        assert a.read_bytes() == b.read_bytes()
    assert (first / "events.jsonl").read_bytes() == (second / "events.jsonl").read_bytes()


def test_shifted_record_is_rejected_atomically(cli: Callable[..., int], db_path: Path) -> None:
    assert cli("init") == 0
    conn = db.connect(db_path)
    with pytest.raises(LedgerError, match=r"EX-0003.*state None"):
        ledger.import_sources(conn, LEDGER_SHIFTED / "tasks", LEDGER_SHIFTED / "events.jsonl")
    assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM import_manifest").fetchone()[0] == 0


def test_shifted_fixture_differs_from_clean_only_in_state_and_blockers() -> None:
    changed: dict[str, list[tuple[str, str]]] = {}
    for m in _yaml_files(LEDGER_CLEAN / "tasks"):
        r = LEDGER_SHIFTED / "tasks" / m.name
        diffs = [
            (a, b)
            for a, b in zip(m.read_text().splitlines(), r.read_text().splitlines(), strict=True)
            if a != b
        ]
        if diffs:
            changed[m.name] = diffs
    assert set(changed) == {"EX-0003.yaml"}
    assert {b for _, b in changed["EX-0003.yaml"]} == {"state: null", "blockers: proposed"}
    assert (LEDGER_CLEAN / "events.jsonl").read_bytes() == (LEDGER_SHIFTED / "events.jsonl").read_bytes()


def test_import_refuses_non_empty_ledger(imported: Path, cli: Callable[..., int]) -> None:
    assert cli("import", "--tasks", str(imported / "tasks"), "--events", str(imported / "events.jsonl")) == 1


def test_import_rejects_unknown_field_and_writes_nothing(cli: Callable[..., int], source: Path, db_path: Path) -> None:
    assert cli("init") == 0
    path = source / "tasks" / "EX-0001.yaml"
    path.write_text(path.read_text() + "extra_field: x\n")
    assert cli("import", "--tasks", str(source / "tasks"), "--events", str(source / "events.jsonl")) == 1
    conn = sqlite3.connect(db_path)
    assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0


def test_import_rejects_event_for_unknown_task(cli: Callable[..., int], source: Path) -> None:
    assert cli("init") == 0
    events = source / "events.jsonl"
    events.write_text(events.read_text() + '{"ts": "2026-09-16 01:00 CDT", "task": "EX-9999", "event": "x", "who": "foundry", "detail": ""}\n')
    assert cli("import", "--tasks", str(source / "tasks"), "--events", str(events)) == 1


def test_import_rejects_filename_id_mismatch(cli: Callable[..., int], source: Path) -> None:
    assert cli("init") == 0
    (source / "tasks" / "EX-0001.yaml").rename(source / "tasks" / "EX-0099.yaml")
    assert cli("import", "--tasks", str(source / "tasks"), "--events", str(source / "events.jsonl")) == 1


def test_export_refuses_overwrite_without_force(imported: Path, cli: Callable[..., int], tmp_path: Path) -> None:
    out = tmp_path / "export"
    assert cli("export", "--dir", str(out)) == 0
    assert cli("export", "--dir", str(out)) == 1
    assert cli("export", "--dir", str(out), "--force") == 0


def test_import_rejects_blank_lines_crlf_and_missing_trailing_newline(cli: Callable[..., int], source: Path) -> None:
    assert cli("init") == 0
    events = source / "events.jsonl"
    original = events.read_bytes()
    for variant in (original + b"\n", original.replace(b"\n", b"\r\n", 1), original.rstrip(b"\n")):
        events.write_bytes(variant)
        assert cli("import", "--tasks", str(source / "tasks"), "--events", str(events)) == 1


def test_import_rejects_stray_files_in_tasks_dir(cli: Callable[..., int], source: Path) -> None:
    assert cli("init") == 0
    (source / "tasks" / "EX-0099.yml").write_text("id: EX-0099\n")
    assert cli("import", "--tasks", str(source / "tasks"), "--events", str(source / "events.jsonl")) == 1


def test_import_rejects_wrong_json_field_shape(cli: Callable[..., int], source: Path) -> None:
    assert cli("init") == 0
    path = source / "tasks" / "EX-0004.yaml"
    path.write_text(path.read_text().replace("blockers: []", "blockers: proposed"))
    assert cli("import", "--tasks", str(source / "tasks"), "--events", str(source / "events.jsonl")) == 1
