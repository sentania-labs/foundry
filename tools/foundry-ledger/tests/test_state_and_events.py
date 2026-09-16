from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable
from pathlib import Path

import pytest

from foundry_ledger import db, ledger
from foundry_ledger.model import TIMESTAMP_RE, LedgerError, now_local

STAMP = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2} C[DS]T$")


def test_state_check_rejects_invalid_state_at_sql_level(imported: Path, db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        conn.execute("UPDATE tasks SET state = 'bogus' WHERE id = 'FDY-0001'")
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        conn.execute("INSERT INTO state_transitions (task, to_state, ts, source) VALUES ('FDY-0001', 'bogus', '2026-09-16 01:00 CDT', 'update')")


def test_state_check_rejects_invalid_state_via_api(imported: Path, db_path: Path) -> None:
    conn = db.connect(db_path)
    with pytest.raises(LedgerError, match="not one of"):
        ledger.update_task(conn, "FDY-0001", {"state": "bogus"})
    with pytest.raises(LedgerError, match="not one of"):
        ledger.add_task(conn, {"id": "FDY-0100", "title": "t", "state": "bogus"})


def test_cli_rejects_invalid_state(imported: Path, cli: Callable[..., int]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli("update", "FDY-0001", "--state", "bogus")
    assert exc.value.code == 2


def test_timestamp_check_rejects_drift(imported: Path, db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    for bad in ("2026-09-16T01:00:00Z", "2026-09-16 01:00", "2026-09-16 01:00:00 CDT", "2026-09-16 01:00 UTC"):
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            conn.execute("UPDATE tasks SET updated = ? WHERE id = 'FDY-0001'", (bad,))
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            conn.execute("INSERT INTO events (ts, task, event, who) VALUES (?, 'FDY-0001', 'x', 'foundry')", (bad,))


def test_now_local_matches_source_format() -> None:
    assert TIMESTAMP_RE.match(now_local())


def test_event_order_is_file_order_not_timestamp_order(imported: Path, db_path: Path) -> None:
    conn = db.connect(db_path)
    source_lines = [json.loads(l) for l in (imported / "events.jsonl").read_text().splitlines()]
    stored = [e.as_dict() for e in ledger.list_events(conn)]
    assert stored == source_lines
    seqs = [r[0] for r in conn.execute("SELECT seq FROM events ORDER BY seq")]
    assert seqs == list(range(1, len(source_lines) + 1))
    # Source has equal timestamps in a row and a later event with an earlier ts
    # than the last one is still appended after it.
    ledger.append_event(conn, "FDY-0005", "note", who="foundry", detail="late", ts="2026-09-15 00:00 CDT")
    last = conn.execute("SELECT seq, ts FROM events ORDER BY seq DESC LIMIT 1").fetchone()
    assert last["seq"] == len(source_lines) + 1 and last["ts"] == "2026-09-15 00:00 CDT"


def test_transitions_derived_from_lifecycle_events_in_order(imported: Path, db_path: Path) -> None:
    conn = db.connect(db_path)
    t = ledger.list_transitions(conn, "FDY-0002")
    assert [(x["from_state"], x["to_state"], x["source"]) for x in t] == [
        (None, "dispatched", "event"),
        ("dispatched", "accepted", "event"),
        ("accepted", "done", "event"),
    ]
    # Non-lifecycle events ("go", "unblocked") produce no transition.
    assert [x["to_state"] for x in ledger.list_transitions(conn, "FDY-0004")] == ["proposed"]
    assert ledger.list_transitions(conn, "FDY-0008") == []


def test_update_state_bumps_updated_records_transition_and_event(imported: Path, db_path: Path) -> None:
    conn = db.connect(db_path)
    before = ledger.get_task(conn, "FDY-0005")
    after = ledger.update_task(conn, "FDY-0005", {"state": "dispatched"}, who="foundry", detail="go")
    assert after["state"] == "dispatched"
    assert STAMP.match(after["updated"]) and after["created"] == before["created"]
    t = ledger.list_transitions(conn, "FDY-0005")
    assert [(x["from_state"], x["to_state"], x["source"]) for x in t] == [("proposed", "dispatched", "update")]
    last = ledger.list_events(conn, "FDY-0005")[-1]
    assert (last.event, last.who, last.detail, last.ts) == ("dispatched", "foundry", "go", after["updated"])
    assert t[0]["event_seq"] == conn.execute("SELECT MAX(seq) FROM events").fetchone()[0]


def test_update_scalar_bumps_updated_without_transition(imported: Path, db_path: Path) -> None:
    conn = db.connect(db_path)
    events_before = len(ledger.list_events(conn))
    after = ledger.update_task(conn, "FDY-0005", {"title": "renamed", "refs": {"branch": "x"}})
    assert after["title"] == "renamed" and after["refs"] == {"branch": "x"}
    assert STAMP.match(after["updated"])
    assert len(ledger.list_events(conn)) == events_before
    assert ledger.list_transitions(conn, "FDY-0005") == []


def test_update_rejects_immutable_fields(imported: Path, db_path: Path) -> None:
    conn = db.connect(db_path)
    for field in ("id", "created", "updated"):
        with pytest.raises(LedgerError, match="cannot be changed"):
            ledger.update_task(conn, "FDY-0005", {field: "x"})


def test_event_with_lifecycle_name_moves_task(imported: Path, cli: Callable[..., int], db_path: Path) -> None:
    assert cli("event", "FDY-0005", "running", "--who", "foundry", "--detail", "started") == 0
    conn = db.connect(db_path)
    task = ledger.get_task(conn, "FDY-0005")
    assert task["state"] == "running" and STAMP.match(task["updated"])
    t = ledger.list_transitions(conn, "FDY-0005")
    assert [(x["from_state"], x["to_state"], x["source"]) for x in t] == [("proposed", "running", "event")]


def test_event_for_unknown_task_is_rejected(imported: Path, cli: Callable[..., int]) -> None:
    assert cli("event", "FDY-9999", "running", "--who", "foundry") == 1


def test_add_auto_id_and_defaults(imported: Path, cli: Callable[..., int], db_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli("add", "--title", "New", "--project", "foundry", "--contract", '{"acceptance": ["x"]}') == 0
    assert capsys.readouterr().out.strip() == "FDY-0009"
    task = ledger.get_task(db.connect(db_path), "FDY-0009")
    assert task["state"] == "proposed" and task["evidence"] == [] and task["parent"] is None
    assert task["contract"] == {"acceptance": ["x"]}
    assert task["created"] == task["updated"] and STAMP.match(task["created"])


def test_add_duplicate_id_rejected(imported: Path, cli: Callable[..., int]) -> None:
    assert cli("add", "--id", "FDY-0001", "--title", "dup") == 1


def test_add_rejects_bad_json(imported: Path, cli: Callable[..., int]) -> None:
    assert cli("add", "--title", "x", "--refs", "{not json") == 1


def test_live_excludes_done_and_abandoned(imported: Path, cli: Callable[..., int], db_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ledger.update_task(db.connect(db_path), "FDY-0005", {"state": "abandoned"})
    assert cli("live") == 0
    ids = [line.split()[0] for line in capsys.readouterr().out.splitlines()]
    assert ids == ["FDY-0004", "FDY-0006", "FDY-0007", "FDY-0008"]
    assert cli("list") == 0
    assert len(capsys.readouterr().out.splitlines()) == 8


def test_show_prints_record_events_transitions(imported: Path, cli: Callable[..., int], capsys: pytest.CaptureFixture[str]) -> None:
    assert cli("show", "FDY-0002") == 0
    out = capsys.readouterr().out
    assert out.startswith("id: FDY-0002\n") and "events:" in out and "dispatched -> accepted" in out
    assert cli("show", "FDY-9999") == 1


def test_clear_sets_scalar_null(imported: Path, cli: Callable[..., int], db_path: Path) -> None:
    assert cli("update", "FDY-0001", "--clear", "last_report") == 0
    assert ledger.get_task(db.connect(db_path), "FDY-0001")["last_report"] is None
    assert cli("update", "FDY-0001", "--clear", "state") == 1


def test_write_failure_rolls_back_whole_transaction(imported: Path, db_path: Path) -> None:
    conn = db.connect(db_path)
    before = ledger.get_task(conn, "FDY-0005")
    with pytest.raises(LedgerError):
        ledger.update_task(conn, "FDY-0005", {"title": "changed", "state": "bogus"})
    assert ledger.get_task(conn, "FDY-0005") == before


def test_db_path_resolution(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv(db.ENV_VAR, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert db.resolve_db_path(None) == tmp_path / ".local" / "state" / "foundry" / "ledger.sqlite"
    monkeypatch.setenv(db.ENV_VAR, str(tmp_path / "env.sqlite"))
    assert db.resolve_db_path(None) == tmp_path / "env.sqlite"
    assert db.resolve_db_path(str(tmp_path / "flag.sqlite")) == tmp_path / "flag.sqlite"
