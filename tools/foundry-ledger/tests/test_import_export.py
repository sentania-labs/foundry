"""Import the fixture copy of the real ledger and export it back.

Normalization used for task YAML equality: both sides are parsed with
yaml.safe_load and compared as Python objects. Key order is preserved by the
export (README field order, which every source file already follows), so the
normalization only absorbs formatting: quoting style, comments, list indent,
and line wrapping in the hand-written files FDY-0001..0004 and the mixed
quoting in FDY-0006/0007. events.jsonl is compared byte for byte.

Why two fixture copies: on main, FDY-0006 and FDY-0007 carry `state: null`
and FDY-0008 carries `state: session 451a530d` (fields shifted by one, with
`blockers: proposed`). The contract mandates a CHECK on state, so those three
records cannot enter the database as written. `ledger_main` is verbatim and
proves the rejection is loud and atomic; `ledger_repaired` changes only those
two lines per file (state -> proposed, blockers -> []) and proves the
round trip.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path

import pytest
import yaml

from foundry_ledger import db, ledger
from foundry_ledger.model import LedgerError
from tests.conftest import LEDGER_MAIN, LEDGER_REPAIRED


def _yaml_files(directory: Path) -> list[Path]:
    return sorted(directory.glob("*.yaml"))


def test_import_counts_and_manifest(imported: Path, db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 8
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 17
    manifest = conn.execute("SELECT kind, record_count FROM import_manifest").fetchall()
    assert sorted(manifest) == sorted([("task", 1)] * 8 + [("events", 17), ("transitions", 12)])


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
    assert "FDY-0005.yaml" in byte_equal and "FDY-0008.yaml" in byte_equal
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


def test_verbatim_main_fixture_is_rejected_atomically(cli: Callable[..., int], db_path: Path) -> None:
    assert cli("init") == 0
    conn = db.connect(db_path)
    with pytest.raises(LedgerError, match=r"FDY-0006.*state None"):
        ledger.import_sources(conn, LEDGER_MAIN / "tasks", LEDGER_MAIN / "events.jsonl")
    assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM import_manifest").fetchone()[0] == 0


def test_repaired_fixture_differs_from_main_only_in_state_and_blockers() -> None:
    changed: dict[str, list[tuple[str, str]]] = {}
    for m in _yaml_files(LEDGER_MAIN / "tasks"):
        r = LEDGER_REPAIRED / "tasks" / m.name
        diffs = [
            (a, b)
            for a, b in zip(m.read_text().splitlines(), r.read_text().splitlines(), strict=True)
            if a != b
        ]
        if diffs:
            changed[m.name] = diffs
    assert set(changed) == {"FDY-0006.yaml", "FDY-0007.yaml", "FDY-0008.yaml"}
    for diffs in changed.values():
        assert {b for _, b in diffs} == {"state: proposed", "blockers: []"}
    assert (LEDGER_MAIN / "events.jsonl").read_bytes() == (LEDGER_REPAIRED / "events.jsonl").read_bytes()


def test_import_refuses_non_empty_ledger(imported: Path, cli: Callable[..., int]) -> None:
    assert cli("import", "--tasks", str(imported / "tasks"), "--events", str(imported / "events.jsonl")) == 1


def test_import_rejects_unknown_field_and_writes_nothing(cli: Callable[..., int], source: Path, db_path: Path) -> None:
    assert cli("init") == 0
    path = source / "tasks" / "FDY-0001.yaml"
    path.write_text(path.read_text() + "extra_field: x\n")
    assert cli("import", "--tasks", str(source / "tasks"), "--events", str(source / "events.jsonl")) == 1
    conn = sqlite3.connect(db_path)
    assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0


def test_import_rejects_event_for_unknown_task(cli: Callable[..., int], source: Path) -> None:
    assert cli("init") == 0
    events = source / "events.jsonl"
    events.write_text(events.read_text() + '{"ts": "2026-09-16 01:00 CDT", "task": "FDY-9999", "event": "x", "who": "foundry", "detail": ""}\n')
    assert cli("import", "--tasks", str(source / "tasks"), "--events", str(events)) == 1


def test_import_rejects_filename_id_mismatch(cli: Callable[..., int], source: Path) -> None:
    assert cli("init") == 0
    (source / "tasks" / "FDY-0001.yaml").rename(source / "tasks" / "FDY-0099.yaml")
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
    (source / "tasks" / "FDY-0099.yml").write_text("id: FDY-0099\n")
    assert cli("import", "--tasks", str(source / "tasks"), "--events", str(source / "events.jsonl")) == 1


def test_import_rejects_wrong_json_field_shape(cli: Callable[..., int], source: Path) -> None:
    assert cli("init") == 0
    path = source / "tasks" / "FDY-0005.yaml"
    path.write_text(path.read_text().replace("blockers: []", "blockers: proposed"))
    assert cli("import", "--tasks", str(source / "tasks"), "--events", str(source / "events.jsonl")) == 1
