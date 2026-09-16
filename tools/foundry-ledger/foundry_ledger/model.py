"""Record shapes, valid states, timestamps, and canonical hashing.

Everything here is pure: no database access.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final
from zoneinfo import ZoneInfo

LOCAL_ZONE: Final = ZoneInfo("America/Chicago")
TIMESTAMP_FORMAT: Final = "%Y-%m-%d %H:%M %Z"
TIMESTAMP_RE: Final = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2} C[DS]T")

# Lifecycle from ledger/README.md. Order here is the main path, then side states.
VALID_STATES: Final[tuple[str, ...]] = (
    "proposed",
    "dispatched",
    "running",
    "reported",
    "accepted",
    "rejected",
    "blocked",
    "missing",
    "abandoned",
    "done",
)
CLOSED_STATES: Final[frozenset[str]] = frozenset({"done", "abandoned"})

# Task record fields in source (README) order. Export emits keys in this order.
TASK_FIELDS: Final[tuple[str, ...]] = (
    "id",
    "title",
    "parent",
    "project",
    "repository",
    "scope",
    "objective",
    "contract",
    "model",
    "harness",
    "execution",
    "state",
    "created",
    "updated",
    "refs",
    "last_report",
    "evidence",
    "blockers",
    "decisions_pending",
)
JSON_FIELDS: Final[frozenset[str]] = frozenset(
    {"contract", "refs", "evidence", "blockers", "decisions_pending"}
)
MAPPING_FIELDS: Final[frozenset[str]] = frozenset({"contract", "refs"})
SCALAR_FIELDS: Final[tuple[str, ...]] = tuple(
    f for f in TASK_FIELDS if f not in JSON_FIELDS
)
EVENT_FIELDS: Final[tuple[str, ...]] = ("ts", "task", "event", "who", "detail")


class LedgerError(Exception):
    """Any user-facing failure. The CLI prints the message and exits 1."""


@dataclass(frozen=True, slots=True)
class Event:
    ts: str
    task: str
    event: str
    who: str
    detail: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts,
            "task": self.task,
            "event": self.event,
            "who": self.who,
            "detail": self.detail,
        }


def now_local() -> str:
    """Current time in America/Chicago, in the ledger's text format."""
    return datetime.now(LOCAL_ZONE).strftime(TIMESTAMP_FORMAT)


def check_timestamp(value: object, where: str) -> str:
    if not isinstance(value, str) or not TIMESTAMP_RE.fullmatch(value):
        raise LedgerError(
            f"{where}: timestamp {value!r} is not 'YYYY-MM-DD HH:MM CDT|CST'"
        )
    return value


def check_state(value: object, where: str) -> str:
    if not isinstance(value, str) or value not in VALID_STATES:
        raise LedgerError(
            f"{where}: state {value!r} is not one of {', '.join(VALID_STATES)}"
        )
    return value


def _check_json_value(value: object, where: str) -> None:
    """Reject values that json.dumps cannot represent losslessly."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return
    if isinstance(value, list):
        for i, item in enumerate(value):
            _check_json_value(item, f"{where}[{i}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise LedgerError(f"{where}: non-string key {key!r}")
            _check_json_value(item, f"{where}.{key}")
        return
    raise LedgerError(
        f"{where}: value of type {type(value).__name__} cannot be stored as JSON;"
        " quote it in the source"
    )


def validate_task(record: dict[str, Any], where: str) -> dict[str, Any]:
    """Validate a task record dict against the README shape.

    Returns the record with keys in TASK_FIELDS order. Rejects missing or
    extra keys, non-string scalars, invalid state, and bad timestamps, so
    nothing is silently dropped or coerced on the way into the database.
    """
    keys = list(record.keys())
    missing = [f for f in TASK_FIELDS if f not in record]
    extra = [k for k in keys if k not in TASK_FIELDS]
    if missing:
        raise LedgerError(f"{where}: missing fields {missing}")
    if extra:
        raise LedgerError(f"{where}: unknown fields {extra}")
    for field in SCALAR_FIELDS:
        value = record[field]
        if value is not None and not isinstance(value, str):
            raise LedgerError(
                f"{where}: field {field!r} must be a string or null,"
                f" got {type(value).__name__}"
            )
    if not isinstance(record["id"], str) or not record["id"]:
        raise LedgerError(f"{where}: id must be a non-empty string")
    check_state(record["state"], f"{where} ({record['id']})")
    check_timestamp(record["created"], f"{where}: created")
    check_timestamp(record["updated"], f"{where}: updated")
    for field in JSON_FIELDS:
        value = record[field]
        expected: type = dict if field in MAPPING_FIELDS else list
        if value is not None and not isinstance(value, expected):
            raise LedgerError(
                f"{where}: field {field!r} must be a {expected.__name__} or null,"
                f" got {type(value).__name__}"
            )
        _check_json_value(value, f"{where}: {field}")
    return {f: record[f] for f in TASK_FIELDS}


def validate_event(record: object, where: str) -> Event:
    if not isinstance(record, dict):
        raise LedgerError(f"{where}: event line is not a JSON object")
    keys = list(record.keys())
    missing = [f for f in EVENT_FIELDS if f not in record]
    extra = [k for k in keys if k not in EVENT_FIELDS]
    if missing:
        raise LedgerError(f"{where}: missing fields {missing}")
    if extra:
        raise LedgerError(f"{where}: unknown fields {extra}")
    for field in ("task", "event", "who"):
        if not isinstance(record[field], str) or not record[field]:
            raise LedgerError(f"{where}: field {field!r} must be a non-empty string")
    detail = record["detail"]
    if detail is not None and not isinstance(detail, str):
        raise LedgerError(f"{where}: field 'detail' must be a string or null")
    ts = check_timestamp(record["ts"], f"{where}: ts")
    return Event(
        ts=ts,
        task=str(record["task"]),
        event=str(record["event"]),
        who=str(record["who"]),
        detail=detail,
    )


def dump_json(value: Any) -> str:
    """Storage form for JSON columns. Key order is preserved for export."""
    return json.dumps(value, ensure_ascii=False)


def load_json(text: str) -> Any:
    return json.loads(text)


def event_line(event: Event) -> str:
    """One events.jsonl line, matching json.dumps defaults used by the source."""
    return json.dumps(event.as_dict())


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical(value: Any) -> str:
    # Key order is part of the content: the export reproduces it, so verify
    # must notice when it changes. Only whitespace is normalized.
    return json.dumps(value, sort_keys=False, ensure_ascii=False, separators=(",", ":"))


def task_content_hash(record: dict[str, Any]) -> str:
    """Content hash of a task, independent of YAML formatting but not of key order."""
    return sha256_bytes(_canonical({f: record[f] for f in TASK_FIELDS}).encode("utf-8"))


def events_content_hash(events: list[Event]) -> str:
    """Content hash of an ordered event sequence."""
    canonical = "\n".join(_canonical(e.as_dict()) for e in events)
    return sha256_bytes(canonical.encode("utf-8"))


def bundle_content_hash(tasks: list[dict[str, Any]], events: list[dict[str, Any]]) -> str:
    """Hash of the Crucible bundle payload: canonical JSON (sorted keys, no
    whitespace, UTF-8) of {"tasks": [...], "events": [...]}."""
    canonical = json.dumps(
        {"tasks": tasks, "events": events},
        sort_keys=True, ensure_ascii=False, separators=(",", ":"),
    )
    return sha256_bytes(canonical.encode("utf-8"))


def transitions_content_hash(transitions: list[tuple[Any, ...]]) -> str:
    """Content hash of an ordered (task, from_state, to_state, ts, source, event_seq) list."""
    canonical = "\n".join(_canonical(list(t)) for t in transitions)
    return sha256_bytes(canonical.encode("utf-8"))
