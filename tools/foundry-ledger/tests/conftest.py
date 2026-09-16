"""Shared fixtures. Every test uses a temp database; the default path is never touched."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

from foundry_ledger import db
from foundry_ledger.cli import main

FIXTURES = Path(__file__).parent / "fixtures"
# Verbatim copy of ledger/tasks and ledger/events.jsonl from main at branch time.
LEDGER_MAIN = FIXTURES / "ledger_main"
# Same files with three malformed records made schema-valid (see test_import_export).
LEDGER_REPAIRED = FIXTURES / "ledger_repaired"


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
    """A writable copy of the repaired fixture so tests can point at real paths."""
    dest = tmp_path / "source"
    shutil.copytree(LEDGER_REPAIRED, dest)
    return dest


@pytest.fixture
def imported(cli: Callable[..., int], source: Path) -> Path:
    assert cli("init") == 0
    assert cli("import", "--tasks", str(source / "tasks"), "--events", str(source / "events.jsonl")) == 0
    return source
