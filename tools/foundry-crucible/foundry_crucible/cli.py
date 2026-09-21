"""A small, dependency-free client for the Crucible orchestrator API."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, NoReturn
from zoneinfo import ZoneInfo

URL_ENV = "CRUCIBLE_URL"
TOKEN_ENV = "CRUCIBLE_TOKEN"
LOCAL_ZONE = ZoneInfo("America/Chicago")


class UsageError(Exception):
    """Invalid local input that argparse could not express."""


@dataclass(frozen=True)
class Call:
    method: str
    path: str
    body: Any = None
    republish: bool = False


def _reason(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--reason", required=True, help="why Foundry is making this decision")


def _file(parser: argparse.ArgumentParser, name: str = "file") -> None:
    parser.add_argument(name, type=Path, metavar="FILE")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="foundry-crucible",
        description="Foundry client for Crucible's /v1 orchestrator API.",
    )
    parser.add_argument("--table", action="store_true", help="print a short human table")
    sub = parser.add_subparsers(dest="command", required=True)

    tasks = sub.add_parser("tasks", help="list tasks")
    tasks.add_argument("--state")

    task = sub.add_parser("task", help="show a task and selected related records")
    task.add_argument("id")
    task.add_argument("--events", action="store_true")
    task.add_argument("--pull-request", action="store_true")
    task.add_argument("--attempts", action="store_true")
    task.add_argument("--gates", action="store_true")
    task.add_argument("--report", action="store_true")
    task.add_argument("--evidence", action="store_true")

    wakes = sub.add_parser("wakes", help="list or acknowledge wakes")
    wakes_sub = wakes.add_subparsers(dest="wakes_command")
    ack = wakes_sub.add_parser("ack", help="acknowledge a wake")
    ack.add_argument("id")
    _reason(ack)

    submit = sub.add_parser("submit", help="submit a TaskContractV1 JSON document")
    _file(submit)
    _reason(submit)

    start = sub.add_parser("start", help="start a submitted task")
    start.add_argument("id")
    start.add_argument("--policy-version", required=True, type=int)
    start.add_argument("--harness")
    start.add_argument("--model")
    start.add_argument("--provider")
    start.add_argument("--image")
    start.add_argument("--effort")
    _reason(start)

    accept = sub.add_parser("accept", help="record Foundry's acceptance decision")
    accept.add_argument("id")
    accept.add_argument(
        "--verdict",
        required=True,
        choices=("accepted", "rejected", "needs_more_work"),
    )
    accept.add_argument("--head-sha")
    _reason(accept)

    review = sub.add_parser("review", help="request review using a ReviewRequest JSON document")
    review.add_argument("id")
    _file(review)
    _reason(review)

    for name, help_text in (
        ("dispositions", "record a ReviewDisposition JSON document"),
        ("corrections", "attach a correction JSON document"),
    ):
        command = sub.add_parser(name, help=help_text)
        command.add_argument("id")
        _file(command)
        _reason(command)

    ci = sub.add_parser("ci-decision", help="record Foundry's CI decision")
    ci.add_argument("id")
    ci.add_argument("--cause", required=True)
    ci.add_argument("--action", required=True)
    _reason(ci)

    head = sub.add_parser("head-decision", help="record Foundry's divergent-head decision")
    head.add_argument("id")
    head.add_argument("--action", required=True)
    _reason(head)

    decisions = sub.add_parser("decisions", help="record a Foundry decision")
    decisions.add_argument("id")
    decisions.add_argument("--kind", required=True)
    decisions.add_argument("--verbatim", required=True)
    decisions.add_argument("--resolves", required=True)
    decisions.add_argument("--escalation-id")
    decisions.add_argument("--reschedule", action="store_true")
    _reason(decisions)

    cancel = sub.add_parser("cancel", help="cancel a task")
    cancel.add_argument("id")
    cancel.add_argument("--verbatim", required=True)
    cancel.add_argument("--decided-by", default="foundry")
    _reason(cancel)

    close = sub.add_parser("close", help="close a completed task")
    close.add_argument("id")
    _reason(close)

    republish = sub.add_parser("republish", help="retry publication after a failure")
    republish.add_argument("id")
    _reason(republish)

    sub.add_parser("health", help="check Crucible liveness")
    return parser


def _read_json(path: Path) -> Any:
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError) as exc:
        raise UsageError(f"cannot read JSON from {path}: {exc}") from exc


def _without_none(document: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in document.items() if value is not None}


def _validate_base_url(value: str) -> str:
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise UsageError(f"{URL_ENV} must be an http or https URL")
    if parsed.query or parsed.fragment:
        raise UsageError(f"{URL_ENV} must not contain a query or fragment")
    return value.rstrip("/")


def _call_for(args: argparse.Namespace) -> Call:
    command: str = args.command
    if command == "tasks":
        query = urllib.parse.urlencode(_without_none({"state": args.state}))
        return Call("GET", "/tasks" + (f"?{query}" if query else ""))
    if command == "task":
        return Call("GET", f"/tasks/{args.id}")
    if command == "wakes":
        if args.wakes_command == "ack":
            return Call("POST", f"/wakes/{args.id}/ack", {"note": args.reason})
        return Call("GET", "/wakes")
    if command == "submit":
        return Call("POST", "/tasks", _read_json(args.file))
    if command == "start":
        return Call(
            "POST",
            f"/tasks/{args.id}/start",
            _without_none(
                {
                    "harness": args.harness,
                    "model": args.model,
                    "provider": args.provider,
                    "image": args.image,
                    "policy_version": args.policy_version,
                    "effort": args.effort,
                }
            ),
        )
    if command == "accept":
        return Call(
            "POST",
            f"/tasks/{args.id}/accept",
            _without_none(
                {"verdict": args.verdict, "reasoning": args.reason, "head_sha": args.head_sha}
            ),
        )
    if command in ("review", "dispositions", "corrections"):
        return Call("POST", f"/tasks/{args.id}/{command}", _read_json(args.file))
    if command == "ci-decision":
        return Call(
            "POST",
            f"/tasks/{args.id}/ci-decision",
            {"cause": args.cause, "action": args.action, "reasoning": args.reason},
        )
    if command == "head-decision":
        return Call(
            "POST",
            f"/tasks/{args.id}/head-decision",
            {"action": args.action, "reasoning": args.reason},
        )
    if command == "decisions":
        return Call(
            "POST",
            f"/tasks/{args.id}/decisions",
            _without_none(
                {
                    "kind": args.kind,
                    "verbatim": args.verbatim,
                    "resolves": args.resolves,
                    "escalation_id": args.escalation_id,
                    "reschedule": args.reschedule,
                }
            ),
        )
    if command == "cancel":
        return Call(
            "POST",
            f"/tasks/{args.id}/cancel",
            {"reason": args.reason, "verbatim": args.verbatim, "decided_by": args.decided_by},
        )
    if command == "close":
        return Call("POST", f"/tasks/{args.id}/close", {"note": args.reason})
    if command == "republish":
        return Call("POST", f"/tasks/{args.id}/republish", {"reason": args.reason}, republish=True)
    if command == "health":
        return Call("GET", "/health")
    raise UsageError(f"unknown command: {command}")


def _redact(text: str, token: str) -> str:
    if not token:
        return text
    return text.replace(f"Bearer {token}", "Bearer [REDACTED]").replace(token, "[REDACTED]")


class Client:
    def __init__(self, base_url: str, token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token

    def request(self, call: Call, *, reason: str | None = None) -> Any:
        data = None
        if call.body is not None:
            data = json.dumps(call.body, separators=(",", ":")).encode("utf-8")
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.token}",
        }
        if data is not None:
            headers["Content-Type"] = "application/json"
        if reason is not None:
            headers["X-Foundry-Reason"] = reason
        request = urllib.request.Request(
            f"{self.base_url}/v1{call.path}", data=data, headers=headers, method=call.method
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            detail = _redact(raw or str(exc), self.token)
            if call.republish and exc.code == 404:
                detail = f"republish is unavailable on this Crucible server: {detail}"
            raise ServerRefusal(detail) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise Unreachable(_redact(str(exc), self.token)) from exc
        if not raw:
            return None
        try:
            return json.loads(raw)
        except ValueError as exc:
            raise ServerRefusal("Crucible returned a non-JSON response") from exc


class ServerRefusal(Exception):
    """Crucible answered but refused or violated its JSON contract."""


class Unreachable(Exception):
    """Crucible could not be reached."""


def _related(client: Client, args: argparse.Namespace, task: Any) -> Any:
    if not isinstance(task, dict):
        return task
    requested = any(
        getattr(args, flag)
        for flag in ("events", "pull_request", "attempts", "gates", "report", "evidence")
    )
    if not requested:
        return task
    result: dict[str, Any] = {"task": task}
    if args.events:
        result["events"] = client.request(Call("GET", f"/tasks/{args.id}/events"))
    if args.pull_request:
        result["pull_request"] = client.request(Call("GET", f"/tasks/{args.id}/pull-request"))
    if args.attempts:
        result["attempts"] = [
            client.request(Call("GET", f"/attempts/{attempt['id']}"))
            for execution in task.get("executions", [])
            for attempt in execution.get("attempts", [])
        ]
    latest = task.get("latest_attempt") or {}
    attempt_id = latest.get("id")
    for flag in ("gates", "report", "evidence"):
        if getattr(args, flag):
            if not attempt_id:
                raise UsageError(f"task {args.id} has no latest attempt for --{flag}")
            result[flag] = client.request(Call("GET", f"/attempts/{attempt_id}/{flag}"))
    return result


def _local_time(value: Any) -> str:
    if not isinstance(value, str):
        return str(value)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return value
    if parsed.tzinfo is None:
        return value
    return parsed.astimezone(LOCAL_ZONE).strftime("%Y-%m-%d %I:%M:%S %p %Z")


def _rows(document: Any) -> list[dict[str, Any]]:
    if isinstance(document, dict) and isinstance(document.get("items"), list):
        return [item for item in document["items"] if isinstance(item, dict)]
    if isinstance(document, list):
        return [item for item in document if isinstance(item, dict)]
    if isinstance(document, dict):
        return [document]
    return [{"value": document}]


def _table(document: Any) -> None:
    rows = _rows(document)
    if not rows:
        print("(none)")
        return
    preferred = (
        "id",
        "external_id",
        "state",
        "reason",
        "status",
        "title",
        "summary",
        "updated_at",
        "created_at",
        "acked_at",
        "version",
    )
    columns = [name for name in preferred if any(name in row for row in rows)]
    if not columns:
        columns = list(rows[0])[:6]
    rendered: list[list[str]] = []
    for row in rows:
        values = []
        for column in columns:
            value = row.get(column, "")
            if column.endswith("_at") or column in ("ts", "created", "updated"):
                value = _local_time(value)
            elif isinstance(value, (dict, list)):
                value = json.dumps(value, sort_keys=True, separators=(",", ":"))
            values.append(str(value))
        rendered.append(values)
    widths = [
        max(len(column), *(len(row[index]) for row in rendered))
        for index, column in enumerate(columns)
    ]
    print("  ".join(column.upper().ljust(widths[index]) for index, column in enumerate(columns)))
    for rendered_row in rendered:
        print("  ".join(value.ljust(widths[index]) for index, value in enumerate(rendered_row)))


def _die(message: str, code: int) -> NoReturn:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(code)


def run(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    normalized = list(sys.argv[1:] if argv is None else argv)
    if "--table" in normalized:
        normalized.remove("--table")
        normalized.insert(0, "--table")
    args = parser.parse_args(normalized)
    base_url = os.environ.get(URL_ENV, "").strip()
    token = os.environ.get(TOKEN_ENV, "").strip()
    if not base_url:
        _die(f"{URL_ENV} is required", 2)
    if not token:
        _die(f"{TOKEN_ENV} is required", 2)
    try:
        base_url = _validate_base_url(base_url)
        call = _call_for(args)
        client = Client(base_url, token)
        document = client.request(call, reason=getattr(args, "reason", None))
        if args.command == "task":
            document = _related(client, args, document)
    except UsageError as exc:
        _die(_redact(str(exc), token), 2)
    except ServerRefusal as exc:
        _die(_redact(str(exc), token), 1)
    except Unreachable as exc:
        _die(_redact(str(exc), token), 3)
    if args.table:
        _table(document)
    else:
        print(json.dumps(document, indent=2, sort_keys=True))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return run(argv)
    except KeyboardInterrupt:
        print("error: interrupted", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
