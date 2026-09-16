"""argparse front end. Exit codes: 0 ok, 1 error or verify mismatch, 2 usage."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from . import db, ledger
from .model import JSON_FIELDS, VALID_STATES, LedgerError


def _opt(field: str) -> str:
    return "--" + field.replace("_", "-")


def _add_field_options(parser: argparse.ArgumentParser, *, for_update: bool) -> None:
    for field in ledger.scalar_fields_for_cli():
        if field == "state":
            parser.add_argument("--state", choices=VALID_STATES, default=None)
        else:
            parser.add_argument(_opt(field), default=None, metavar="TEXT")
    for field in sorted(JSON_FIELDS):
        parser.add_argument(_opt(field), default=None, metavar="JSON")
    if for_update:
        parser.add_argument(
            "--clear",
            action="append",
            default=[],
            metavar="FIELD",
            help="set a scalar field to null (repeatable)",
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="foundry-ledger",
        description="SQLite bootstrap ledger for Foundry.",
    )
    parser.add_argument(
        "--db",
        default=None,
        help="ledger path (default: $FOUNDRY_LEDGER_DB, then ~/.local/state/foundry/ledger.sqlite)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="create the database and apply all migrations")
    sub.add_parser("migrate", help="apply pending migrations (idempotent)")

    p = sub.add_parser("import", help="import YAML tasks and JSONL events into an empty ledger")
    p.add_argument("--tasks", required=True, metavar="DIR")
    p.add_argument("--events", required=True, metavar="FILE")

    sub.add_parser("verify", help="recount and rehash imported records against the manifest")

    p = sub.add_parser("add", help="create a task (id auto-assigned unless --id)")
    p.add_argument("--id", default=None)
    _add_field_options(p, for_update=False)
    p.add_argument("--who", default="foundry", choices=("foundry", "scott", "worker"), help="author of the initial-state event")
    p.add_argument("--detail", default="", help="detail of the initial-state event")

    p = sub.add_parser("update", help="change task fields; bumps updated")
    p.add_argument("id")
    _add_field_options(p, for_update=True)
    p.add_argument("--who", default="foundry", choices=("foundry", "scott", "worker"), help="author of the event a --state change appends")
    p.add_argument("--detail", default="", help="detail of the event a --state change appends")

    p = sub.add_parser("event", help="append an event; a lifecycle-state name moves the task")
    p.add_argument("id")
    p.add_argument("name")
    p.add_argument("--who", required=True, choices=("foundry", "scott", "worker"))
    p.add_argument("--detail", default="")

    sub.add_parser("list", help="all tasks")
    sub.add_parser("live", help="tasks not done or abandoned")
    p = sub.add_parser("show", help="one task with its events and transitions")
    p.add_argument("id")

    p = sub.add_parser("export", help="write tasks/*.yaml and events.jsonl")
    p.add_argument("--dir", required=True, metavar="DIR")
    p.add_argument("--force", action="store_true", help="overwrite an existing export")
    return parser


def _collect_fields(args: argparse.Namespace) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for field in ledger.scalar_fields_for_cli():
        value = getattr(args, field)
        if value is not None:
            fields[field] = value
    for field in JSON_FIELDS:
        value = getattr(args, field)
        if value is not None:
            fields[field] = ledger.parse_json_field(field, value)
    for field in getattr(args, "clear", []):
        if field not in ledger.scalar_fields_for_cli() or field == "state":
            raise LedgerError(f"--clear: {field!r} is not a nullable scalar field")
        if field in fields:
            raise LedgerError(f"--clear {field} conflicts with {_opt(field)}")
        fields[field] = None
    return fields


def _print_table(tasks: list[dict[str, Any]]) -> None:
    if not tasks:
        print("(no tasks)")
        return
    width = max(len(t["id"]) for t in tasks)
    for t in tasks:
        print(f"{t['id']:<{width}}  {t['state']:<10}  {t['updated']}  {t['title'] or ''}")


def run(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    path = db.resolve_db_path(args.db)
    cmd: str = args.command

    if cmd == "init":
        conn, applied = db.init(path)
        conn.close()
        print(f"{path}: applied migrations {applied or 'none (up to date)'}")
        return 0

    if cmd == "migrate":
        conn = db.connect(path)
        try:
            applied = db.migrate(conn)
        finally:
            conn.close()
        print(f"{path}: applied migrations {applied or 'none (up to date)'}")
        return 0

    readonly = cmd in ("verify", "list", "live", "show", "export")
    conn = db.connect(path, readonly=readonly)
    try:
        db.require_migrated(conn)
        if cmd == "import":
            counts = ledger.import_sources(conn, Path(args.tasks), Path(args.events))
            print(
                f"imported {counts['tasks']} tasks, {counts['events']} events,"
                f" {counts['transitions']} derived transitions"
            )
        elif cmd == "verify":
            problems = ledger.verify(conn)
            if problems:
                for line in problems:
                    print(f"MISMATCH {line}")
                return 1
            rows = conn.execute("SELECT COUNT(*) AS n FROM import_manifest").fetchone()
            print(f"verify ok: {rows['n']} manifest entries intact")
        elif cmd == "add":
            fields = _collect_fields(args)
            if args.id:
                fields["id"] = args.id
            record = ledger.add_task(conn, fields, who=args.who, detail=args.detail)
            print(record["id"])
        elif cmd == "update":
            changes = _collect_fields(args)
            if not changes:
                raise LedgerError("update: nothing to change")
            record = ledger.update_task(conn, args.id, changes, who=args.who, detail=args.detail)
            print(f"{record['id']} {record['state']} updated {record['updated']}")
        elif cmd == "event":
            event = ledger.append_event(conn, args.id, args.name, who=args.who, detail=args.detail)
            print(f"{event.task} {event.event} at {event.ts}")
        elif cmd == "list":
            _print_table(ledger.list_tasks(conn))
        elif cmd == "live":
            _print_table(ledger.list_tasks(conn, live_only=True))
        elif cmd == "show":
            record = ledger.get_task(conn, args.id)
            sys.stdout.write(ledger.task_yaml(record))
            events = ledger.list_events(conn, args.id)
            print("events:")
            for e in events:
                print(f"  {e.ts}  {e.event}  ({e.who}) {e.detail or ''}")
            print("transitions:")
            for t in ledger.list_transitions(conn, args.id):
                print(f"  {t['ts']}  {t['from_state']} -> {t['to_state']}  [{t['source']}]")
        elif cmd == "export":
            counts = ledger.export(conn, Path(args.dir), force=args.force)
            print(f"exported {counts['tasks']} tasks, {counts['events']} events to {args.dir}")
        else:  # pragma: no cover
            parser.error(f"unknown command {cmd}")
    finally:
        conn.close()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return run(argv)
    except LedgerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except sqlite3.Error as exc:
        print(f"error: database: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
