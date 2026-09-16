"""Shared fixtures. Every test uses a temp database; the default path is never touched."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

from foundry_ledger import db
from foundry_ledger.cli import main

FIXTURES = Path(__file__).parent / "fixtures"
# Synthetic ledger in the real shape: EX-0001 and EX-0002 hand-written (quoted
# timestamps, indented lists, a comment), EX-0003 and EX-0004 machine-written.
LEDGER_CLEAN = FIXTURES / "ledger_clean"
# Same set with EX-0003 carrying a shifted record (state null, blockers a string).
LEDGER_SHIFTED = FIXTURES / "ledger_shifted"


@pytest.fixture
def db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "ledger.sqlite"
    # Belt and braces: even a call that forgets --db lands in the temp dir.
    monkeypatch.setenv(db.ENV_VAR, str(path))
    monkeypatch.setenv("HOME", str(tmp_path))
    return path


@pytest.fixture
def cli(db_path: Path) -> Callable[..., int]:
    def _run(*args: str) -> int:
        return main(["--db", str(db_path), *args])

    return _run


@pytest.fixture
def source(tmp_path: Path) -> Path:
    """A writable copy of the clean fixture so tests can point at real paths."""
    dest = tmp_path / "source"
    shutil.copytree(LEDGER_CLEAN, dest)
    return dest


@pytest.fixture
def imported(cli: Callable[..., int], source: Path) -> Path:
    assert cli("init") == 0
    assert cli("import", "--tasks", str(source / "tasks"), "--events", str(source / "events.jsonl")) == 0
    return source
