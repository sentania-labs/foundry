from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path


def test_verify_passes_after_import(imported: Path, cli: Callable[..., int]) -> None:
    assert cli("verify") == 0


def test_verify_fails_after_editing_a_task_row(imported: Path, cli: Callable[..., int], db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE tasks SET title = 'tampered' WHERE id = 'EX-0002'")
    conn.commit()
    assert cli("verify") == 1


def test_verify_fails_after_editing_an_event_row(imported: Path, cli: Callable[..., int], db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE events SET detail = 'tampered' WHERE seq = 3")
    conn.commit()
    assert cli("verify") == 1


def test_verify_fails_after_deleting_a_task(imported: Path, cli: Callable[..., int], db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("DELETE FROM tasks WHERE id = 'EX-0004'")  # no events reference it
    conn.commit()
    assert cli("verify") == 1


def test_verify_fails_after_reordering_events(imported: Path, cli: Callable[..., int], db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    a = conn.execute("SELECT detail FROM events WHERE seq = 1").fetchone()[0]
    b = conn.execute("SELECT detail FROM events WHERE seq = 2").fetchone()[0]
    conn.execute("UPDATE events SET detail = ? WHERE seq = 1", (b,))
    conn.execute("UPDATE events SET detail = ? WHERE seq = 2", (a,))
    conn.commit()
    assert cli("verify") == 1


def test_verify_still_passes_after_appending_a_new_event(imported: Path, cli: Callable[..., int]) -> None:
    assert cli("event", "EX-0003", "note", "--who", "foundry", "--detail", "post-import") == 0
    assert cli("verify") == 0


def test_verify_without_import_is_nonzero(cli: Callable[..., int]) -> None:
    assert cli("init") == 0
    assert cli("verify") == 1


def test_verify_fails_after_deleting_transitions(imported: Path, cli: Callable[..., int], db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute("DELETE FROM state_transitions")
    conn.commit()
    assert cli("verify") == 1


def test_verify_fails_after_reordering_json_keys(imported: Path, cli: Callable[..., int], db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute("""UPDATE tasks SET contract = '{"verification": "x", "acceptance": "y"}' WHERE id = 'EX-0002'""")
    conn.commit()
    assert cli("verify") == 1
    conn.execute("""UPDATE tasks SET contract = '{"acceptance": "y", "verification": "x"}' WHERE id = 'EX-0002'""")
    conn.commit()
    assert cli("verify") == 1  # still differs from the source content


def test_verify_still_passes_after_adding_a_new_task(imported: Path, cli: Callable[..., int]) -> None:
    assert cli("add", "--title", "later") == 0
    assert cli("verify") == 0
