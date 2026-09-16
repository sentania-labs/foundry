"""Ledger operations. Every write is one transaction; reads open read-only."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

import yaml

import os

from .db import require_migrated, transaction
from .model import (
    CLOSED_STATES,
    JSON_FIELDS,
    SCALAR_FIELDS,
    TASK_FIELDS,
    VALID_STATES,
    Event,
    LedgerError,
    bundle_content_hash,
    check_state,
    dump_json,
    event_line,
    events_content_hash,
    load_json,
    now_local,
    sha256_bytes,
    task_content_hash,
    transitions_content_hash,
    validate_event,
    validate_task,
)

# ---------------------------------------------------------------- rows <-> records


def row_to_task(row: sqlite3.Row) -> dict[str, Any]:
    record: dict[str, Any] = {}
    for field in TASK_FIELDS:
        value = row[field]
        record[field] = load_json(value) if field in JSON_FIELDS else value
    return record


def task_to_params(record: Mapping[str, Any]) -> dict[str, Any]:
    params: dict[str, Any] = {}
    for field in TASK_FIELDS:
        value = record[field]
        params[field] = dump_json(value) if field in JSON_FIELDS else value
    return params


def row_to_event(row: sqlite3.Row) -> Event:
    return Event(
        ts=row["ts"], task=row["task"], event=row["event"], who=row["who"], detail=row["detail"]
    )


def get_task(conn: sqlite3.Connection, task_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        raise LedgerError(f"no task {task_id}")
    return row_to_task(row)


def list_tasks(conn: sqlite3.Connection, *, live_only: bool = False) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM tasks ORDER BY id").fetchall()
    tasks = [row_to_task(r) for r in rows]
    if live_only:
        tasks = [t for t in tasks if t["state"] not in CLOSED_STATES]
    return tasks


def list_events(conn: sqlite3.Connection, task_id: str | None = None) -> list[Event]:
    if task_id is None:
        rows = conn.execute("SELECT * FROM events ORDER BY seq").fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM events WHERE task = ? ORDER BY seq", (task_id,)
        ).fetchall()
    return [row_to_event(r) for r in rows]


def list_transitions(conn: sqlite3.Connection, task_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT id, from_state, to_state, ts, source, event_seq"
        " FROM state_transitions WHERE task = ? ORDER BY id",
        (task_id,),
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- write guard


def migrated_marker(conn: sqlite3.Connection) -> str | None:
    """The Crucible import id if this ledger has been handed off, else None."""
    row = conn.execute("SELECT crucible_import FROM migrated WHERE id = 1").fetchone()
    return None if row is None else str(row["crucible_import"])


@contextmanager
def _write(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """A write transaction that refuses once the ledger is marked migrated.

    The check runs inside BEGIN IMMEDIATE so a concurrent mark cannot slip
    between the check and the write.
    """
    require_migrated(conn)
    with transaction(conn):
        marker = migrated_marker(conn)
        if marker is not None:
            raise LedgerError(
                f"ledger is frozen: migrated to Crucible as import {marker}; writes refused"
            )
        yield conn


def mark_migrated(conn: sqlite3.Connection, crucible_import: str) -> str:
    """Record the handoff. One-way: a second call refuses like any other write."""
    crucible_import = crucible_import.strip()
    if not crucible_import:
        raise LedgerError("--crucible-import must not be empty")
    stamp = now_local()
    with _write(conn):
        conn.execute(
            "INSERT INTO migrated (id, crucible_import, marked_at) VALUES (1, ?, ?)",
            (crucible_import, stamp),
        )
    return stamp


# ---------------------------------------------------------------- writes


def _insert_event(conn: sqlite3.Connection, event: Event) -> int:
    cur = conn.execute(
        "INSERT INTO events (ts, task, event, who, detail) VALUES (?, ?, ?, ?, ?)",
        (event.ts, event.task, event.event, event.who, event.detail),
    )
    seq = cur.lastrowid
    if seq is None:
        raise LedgerError("event insert returned no seq")
    return int(seq)


def _insert_transition(
    conn: sqlite3.Connection,
    task_id: str,
    from_state: str | None,
    to_state: str,
    ts: str,
    source: str,
    event_seq: int | None,
) -> None:
    conn.execute(
        "INSERT INTO state_transitions"
        " (task, from_state, to_state, ts, source, event_seq)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (task_id, from_state, to_state, ts, source, event_seq),
    )


def _set_state(
    conn: sqlite3.Connection, task_id: str, new_state: str, ts: str, source: str, event_seq: int
) -> None:
    """Move a task to new_state, recording the transition. Caller owns the txn.

    `ts` is the transition time as recorded on the event; `updated` is always
    the wall clock so a backdated event cannot move it backwards.
    """
    current = get_task(conn, task_id)["state"]
    if current == new_state:
        raise LedgerError(f"task {task_id} is already {new_state}")
    conn.execute(
        "UPDATE tasks SET state = ?, updated = ? WHERE id = ?", (new_state, now_local(), task_id)
    )
    _insert_transition(conn, task_id, current, new_state, ts, source, event_seq)


def next_task_id(conn: sqlite3.Connection, prefix: str = "FDY") -> str:
    rows = conn.execute("SELECT id FROM tasks WHERE id LIKE ?", (f"{prefix}-%",)).fetchall()
    highest = 0
    for row in rows:
        suffix = str(row["id"]).split("-", 1)[1]
        if suffix.isdigit():
            highest = max(highest, int(suffix))
    return f"{prefix}-{highest + 1:04d}"


def add_task(
    conn: sqlite3.Connection,
    fields: Mapping[str, Any],
    *,
    who: str = "foundry",
    detail: str | None = "",
) -> dict[str, Any]:
    """Create a task and append an event named after its initial state.

    Missing scalars are null, missing JSON fields are empty. The event gives
    every task a history from its first state (transition null -> state).
    """
    now = now_local()
    record: dict[str, Any] = {f: None for f in TASK_FIELDS}
    record.update({"contract": {}, "refs": {}, "evidence": [], "blockers": [],
                   "decisions_pending": [], "state": "proposed",
                   "created": now, "updated": now})
    for key, value in fields.items():
        if key not in TASK_FIELDS:
            raise LedgerError(f"unknown task field {key!r}")
        record[key] = value
    with _write(conn):
        if not record["id"]:
            record["id"] = next_task_id(conn)
        record = validate_task(record, f"add {record['id']}")
        if conn.execute("SELECT 1 FROM tasks WHERE id = ?", (record["id"],)).fetchone():
            raise LedgerError(f"task {record['id']} already exists")
        _insert_task(conn, record)
        seq = _insert_event(conn, Event(now, record["id"], record["state"], who, detail))
        _insert_transition(conn, record["id"], None, record["state"], now, "event", seq)
    return record


def _insert_task(conn: sqlite3.Connection, record: Mapping[str, Any]) -> None:
    cols = ", ".join(TASK_FIELDS)
    marks = ", ".join(f":{f}" for f in TASK_FIELDS)
    conn.execute(f"INSERT INTO tasks ({cols}) VALUES ({marks})", task_to_params(record))


def update_task(
    conn: sqlite3.Connection,
    task_id: str,
    changes: Mapping[str, Any],
    *,
    who: str = "foundry",
    detail: str | None = "",
) -> dict[str, Any]:
    """Apply field changes and bump `updated`.

    A `state` change also appends an event named after the new state and
    records the transition, so the event history stays complete.
    """
    for key in changes:
        if key not in TASK_FIELDS:
            raise LedgerError(f"unknown task field {key!r}")
        if key in ("id", "created", "updated"):
            raise LedgerError(f"field {key!r} cannot be changed with update")
    now = now_local()
    with _write(conn):
        current = get_task(conn, task_id)
        merged = dict(current)
        merged.update({k: v for k, v in changes.items() if k != "state"})
        merged["updated"] = now
        merged = validate_task(merged, f"update {task_id}")
        sets = ", ".join(f"{f} = :{f}" for f in TASK_FIELDS if f != "id")
        conn.execute(f"UPDATE tasks SET {sets} WHERE id = :id", task_to_params(merged))
        if "state" in changes:
            new_state = check_state(changes["state"], f"update {task_id}")
            seq = _insert_event(conn, Event(now, task_id, new_state, who, detail))
            _set_state(conn, task_id, new_state, now, "update", seq)
        return get_task(conn, task_id)


def append_event(
    conn: sqlite3.Connection,
    task_id: str,
    name: str,
    *,
    who: str,
    detail: str | None,
    ts: str | None = None,
) -> Event:
    """Append an event. A lifecycle-state name also moves the task to that state."""
    stamp = ts if ts is not None else now_local()
    event = validate_event(
        {"ts": stamp, "task": task_id, "event": name, "who": who, "detail": detail},
        "event",
    )
    with _write(conn):
        get_task(conn, task_id)
        seq = _insert_event(conn, event)
        if name in VALID_STATES:
            _set_state(conn, task_id, name, stamp, "event", seq)
    return event


# ---------------------------------------------------------------- import


def _read_task_file(path: Path) -> tuple[bytes, dict[str, Any]]:
    raw = path.read_bytes()
    try:
        loaded = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise LedgerError(f"{path}: not valid YAML: {exc}") from exc
    if not isinstance(loaded, dict):
        raise LedgerError(f"{path}: top level is not a mapping")
    record = validate_task(loaded, str(path))
    if path.stem != record["id"]:
        raise LedgerError(f"{path}: file name does not match id {record['id']!r}")
    return raw, record


def _read_events_file(path: Path) -> tuple[bytes, list[Event]]:
    raw = path.read_bytes()
    events: list[Event] = []
    text = raw.decode("utf-8")
    if text and not text.endswith("\n"):
        raise LedgerError(f"{path}: last line has no trailing newline")
    for lineno, line in enumerate(text.split("\n")[:-1], start=1):
        if line == "" or line.endswith("\r"):
            raise LedgerError(f"{path}:{lineno}: blank line or CRLF line ending")
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise LedgerError(f"{path}:{lineno}: not valid JSON: {exc}") from exc
        events.append(validate_event(obj, f"{path}:{lineno}"))
    return raw, events


def import_sources(conn: sqlite3.Connection, tasks_dir: Path, events_file: Path) -> dict[str, int]:
    """Import YAML tasks and JSONL events into an empty ledger, in one transaction.

    Refuses to run on a ledger that already holds tasks, events, or a manifest,
    so a re-run can never duplicate or overwrite history. Every source file is
    fully validated before any row is written.
    """
    if not tasks_dir.is_dir():
        raise LedgerError(f"tasks dir {tasks_dir} is not a directory")
    if not events_file.is_file():
        raise LedgerError(f"events file {events_file} is not a file")
    task_files = sorted(p for p in tasks_dir.iterdir() if p.suffix == ".yaml" and p.is_file())
    if not task_files:
        raise LedgerError(f"no *.yaml task files in {tasks_dir}")
    stray = sorted(p.name for p in tasks_dir.iterdir() if p.suffix != ".yaml")
    if stray:
        raise LedgerError(f"{tasks_dir}: only *.yaml files are imported; found {stray}")

    tasks: list[tuple[Path, bytes, dict[str, Any]]] = []
    seen: set[str] = set()
    for path in task_files:
        raw, record = _read_task_file(path)
        if record["id"] in seen:
            raise LedgerError(f"{path}: duplicate task id {record['id']}")
        seen.add(record["id"])
        tasks.append((path, raw, record))
    events_raw, events = _read_events_file(events_file)
    for i, ev in enumerate(events, start=1):
        if ev.task not in seen:
            raise LedgerError(f"{events_file}:{i}: event for unknown task {ev.task}")

    imported_at = now_local()
    with _write(conn):
        for table in ("tasks", "events", "import_manifest"):
            if conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone():
                raise LedgerError(
                    f"ledger already has rows in {table}; import only runs into an empty ledger"
                )
        for path, raw, record in tasks:
            _insert_task(conn, record)
            conn.execute(
                "INSERT INTO import_manifest (source_path, kind, record_id, source_sha256,"
                " content_sha256, record_count, imported_at) VALUES (?, 'task', ?, ?, ?, 1, ?)",
                (str(path), record["id"], sha256_bytes(raw), task_content_hash(record), imported_at),
            )
        last_state: dict[str, str] = {}
        transitions: list[tuple[Any, ...]] = []
        for ev in events:
            seq = _insert_event(conn, ev)
            if ev.event in VALID_STATES:
                row = (ev.task, last_state.get(ev.task), ev.event, ev.ts, "event", seq)
                _insert_transition(conn, *row)
                transitions.append(row)
                last_state[ev.task] = ev.event
        conn.execute(
            "INSERT INTO import_manifest (source_path, kind, record_id, source_sha256,"
            " content_sha256, record_count, imported_at) VALUES (?, 'events', NULL, ?, ?, ?, ?)",
            (str(events_file), sha256_bytes(events_raw), events_content_hash(events),
             len(events), imported_at),
        )
        # Derived data gets a receipt too, hashed over the same rows just written.
        conn.execute(
            "INSERT INTO import_manifest (source_path, kind, record_id, source_sha256,"
            " content_sha256, record_count, imported_at)"
            " VALUES (?, 'transitions', NULL, ?, ?, ?, ?)",
            (str(events_file), sha256_bytes(events_raw), transitions_content_hash(transitions),
             len(transitions), imported_at),
        )
    return {"tasks": len(tasks), "events": len(events), "transitions": len(transitions)}


# ---------------------------------------------------------------- verify


def verify(conn: sqlite3.Connection) -> list[str]:
    """Recount and rehash imported records against the manifest.

    Returns a list of problems; empty means the import is intact. Records
    written after the import (new tasks, appended events) are not covered;
    an `update` to an imported task is reported as a difference by design.
    """
    rows = conn.execute("SELECT * FROM import_manifest ORDER BY id").fetchall()
    if not rows:
        return ["import manifest is empty; nothing to verify"]
    problems: list[str] = []
    for row in rows:
        kind = row["kind"]
        if kind == "task":
            task_id = row["record_id"]
            found = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if found is None:
                problems.append(f"task {task_id}: missing (manifest count 1, found 0)")
                continue
            actual = task_content_hash(row_to_task(found))
            if actual != row["content_sha256"]:
                problems.append(f"task {task_id}: content hash mismatch")
        elif kind == "events":
            expected_count = int(row["record_count"])
            imported = conn.execute(
                "SELECT * FROM events ORDER BY seq LIMIT ?", (expected_count,)
            ).fetchall()
            if len(imported) != expected_count:
                problems.append(
                    f"events: count mismatch (manifest {expected_count}, found {len(imported)})"
                )
                continue
            actual = events_content_hash([row_to_event(r) for r in imported])
            if actual != row["content_sha256"]:
                problems.append("events: content hash mismatch over imported sequence")
        elif kind == "transitions":
            expected_count = int(row["record_count"])
            imported = conn.execute(
                "SELECT task, from_state, to_state, ts, source, event_seq"
                " FROM state_transitions ORDER BY id LIMIT ?",
                (expected_count,),
            ).fetchall()
            if len(imported) != expected_count:
                problems.append(
                    f"transitions: count mismatch (manifest {expected_count}, found {len(imported)})"
                )
                continue
            actual = transitions_content_hash([tuple(r) for r in imported])
            if actual != row["content_sha256"]:
                problems.append("transitions: content hash mismatch over imported sequence")
        else:
            problems.append(f"manifest row {row['id']}: unknown kind {kind!r}")
    return problems


# ---------------------------------------------------------------- export


def task_yaml(record: Mapping[str, Any]) -> str:
    ordered = {f: record[f] for f in TASK_FIELDS}
    return yaml.safe_dump(ordered, sort_keys=False, allow_unicode=True, default_flow_style=False)


def events_jsonl(events: list[Event]) -> str:
    return "".join(event_line(e) + "\n" for e in events)


def export(conn: sqlite3.Connection, out_dir: Path, *, force: bool = False) -> dict[str, int]:
    """Write tasks/<id>.yaml and events.jsonl under out_dir. Never overwrites unless forced."""
    tasks_dir = out_dir / "tasks"
    events_path = out_dir / "events.jsonl"
    if not force:
        clashes = [p for p in (tasks_dir, events_path) if p.exists()]
        if clashes:
            raise LedgerError(
                f"refusing to overwrite {', '.join(str(p) for p in clashes)}; pass --force"
            )
    tasks = list_tasks(conn)
    events = list_events(conn)
    tasks_dir.mkdir(parents=True, exist_ok=True)
    for record in tasks:
        (tasks_dir / f"{record['id']}.yaml").write_text(task_yaml(record), encoding="utf-8")
    events_path.write_text(events_jsonl(events), encoding="utf-8")
    return {"tasks": len(tasks), "events": len(events)}


# ---------------------------------------------------------------- crucible bundle

BUNDLE_SCHEMA_VERSION = "1.0"


def _tool_version() -> str:
    try:
        return version("foundry-ledger")
    except PackageNotFoundError:  # pragma: no cover
        return "unknown"


def crucible_bundle(conn: sqlite3.Connection, db_path: Path) -> dict[str, Any]:
    """The handoff bundle: every task and event, counts, and a content hash.

    `content_sha256` covers the canonical JSON of tasks plus events (see
    model.bundle_content_hash) so the receiver can recompute it without the
    `source` block, whose `exported_at` changes on every run.

    The rows and the file bytes are read inside one deferred transaction so
    the SHARED lock holds off writers: `db_sha256` describes exactly the
    database state the rows came from.
    """
    require_migrated(conn)
    conn.execute("BEGIN")
    try:
        tasks = list_tasks(conn)
        rows = conn.execute("SELECT seq, ts, task, event, who, detail FROM events ORDER BY seq")
        events = [dict(r) for r in rows]
        marker = migrated_marker(conn)
        db_bytes = db_path.read_bytes()
    finally:
        conn.execute("COMMIT")
    return {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "source": {
            "tool": f"foundry-ledger {_tool_version()}",
            "db_sha256": sha256_bytes(db_bytes),
            "exported_at": now_local(),
            "migrated": marker,
        },
        "tasks": tasks,
        "events": events,
        "counts": {"tasks": len(tasks), "events": len(events)},
        "content_sha256": bundle_content_hash(tasks, events),
    }


def export_crucible(
    conn: sqlite3.Connection, db_path: Path, out_dir: Path, *, force: bool = False
) -> dict[str, int]:
    """Write out_dir/crucible.json. Never overwrites unless forced."""
    target = out_dir / "crucible.json"
    if target.exists() and not force:
        raise LedgerError(f"refusing to overwrite {target}; pass --force")
    bundle = crucible_bundle(conn, db_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = out_dir / "crucible.json.tmp"
    tmp.write_text(json.dumps(bundle, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, target)  # a crash mid-write cannot leave a partial crucible.json
    counts: dict[str, int] = bundle["counts"]
    return counts


# ---------------------------------------------------------------- helpers for the CLI


def parse_json_field(field: str, text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise LedgerError(f"--{field.replace('_', '-')}: not valid JSON: {exc}") from exc


def scalar_fields_for_cli() -> tuple[str, ...]:
    return tuple(f for f in SCALAR_FIELDS if f not in ("id", "created", "updated"))
