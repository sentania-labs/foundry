from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any

import pytest

from foundry_crucible.cli import main, run
from tests.conftest import FakeCrucible

TOKEN = "test-token-value"


def _document(tmp_path: Path, name: str = "input.json") -> Path:
    path = tmp_path / name
    path.write_text('{"schema_version":"1.0","value":"input"}\n', encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("argv", "method", "path", "body"),
    [
        (["tasks"], "GET", "/v1/tasks", None),
        (["tasks", "--state", "running"], "GET", "/v1/tasks?state=running", None),
        (["task", "T1"], "GET", "/v1/tasks/T1", None),
        (["wakes"], "GET", "/v1/wakes", None),
        (
            ["wakes", "ack", "W1", "--reason", "handled"],
            "POST",
            "/v1/wakes/W1/ack",
            {"note": "handled"},
        ),
        (
            ["start", "T1", "--policy-version", "2", "--model", "model-a", "--reason", "go"],
            "POST",
            "/v1/tasks/T1/start",
            {"model": "model-a", "policy_version": 2},
        ),
        (
            ["accept", "T1", "--verdict", "accepted", "--head-sha", "abc", "--reason", "ok"],
            "POST",
            "/v1/tasks/T1/accept",
            {"verdict": "accepted", "reasoning": "ok", "head_sha": "abc"},
        ),
        (
            ["ci-decision", "T1", "--cause", "code", "--action", "correct", "--reason", "fix"],
            "POST",
            "/v1/tasks/T1/ci-decision",
            {"cause": "code", "action": "correct", "reasoning": "fix"},
        ),
        (
            ["head-decision", "T1", "--action", "recollect", "--reason", "inspect"],
            "POST",
            "/v1/tasks/T1/head-decision",
            {"action": "recollect", "reasoning": "inspect"},
        ),
        (
            [
                "decisions",
                "T1",
                "--kind",
                "scope",
                "--verbatim",
                "Proceed",
                "--resolves",
                "E1",
                "--reschedule",
                "--reason",
                "operator decided",
            ],
            "POST",
            "/v1/tasks/T1/decisions",
            {"kind": "scope", "verbatim": "Proceed", "resolves": "E1", "reschedule": True},
        ),
        (
            [
                "cancel",
                "T1",
                "--verbatim",
                "Cancel it",
                "--decided-by",
                "operator",
                "--reason",
                "obsolete",
            ],
            "POST",
            "/v1/tasks/T1/cancel",
            {"reason": "obsolete", "verbatim": "Cancel it", "decided_by": "operator"},
        ),
        (
            ["close", "T1", "--reason", "complete"],
            "POST",
            "/v1/tasks/T1/close",
            {"note": "complete"},
        ),
        (
            ["republish", "T1", "--reason", "retry fixed provider"],
            "POST",
            "/v1/tasks/T1/republish",
            {"reason": "retry fixed provider"},
        ),
        (["health"], "GET", "/v1/health", None),
    ],
)
def test_command_request_shapes(
    fake: FakeCrucible,
    capsys: pytest.CaptureFixture[str],
    argv: list[str],
    method: str,
    path: str,
    body: Any,
) -> None:
    assert run(argv) == 0
    request = fake.requests[-1]
    assert (request["method"], request["path"], request["body"]) == (method, path, body)
    assert request["headers"]["Authorization"] == f"Bearer {TOKEN}"
    if "--reason" in argv:
        assert request["headers"]["X-Foundry-Reason"] == argv[argv.index("--reason") + 1]
    assert TOKEN not in capsys.readouterr().out


@pytest.mark.parametrize("command", ["submit", "review", "dispositions", "corrections"])
def test_file_command_request_shapes(
    fake: FakeCrucible,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    command: str,
) -> None:
    document = _document(tmp_path)
    argv = [command]
    expected_path = "/v1/tasks"
    if command != "submit":
        argv.append("T1")
        expected_path = f"/v1/tasks/T1/{command}"
    argv.extend([str(document), "--reason", "needed"])
    assert run(argv) == 0
    request = fake.requests[-1]
    assert request["method"] == "POST"
    assert request["path"] == expected_path
    assert request["body"] == {"schema_version": "1.0", "value": "input"}
    assert request["headers"]["X-Foundry-Reason"] == "needed"
    captured = capsys.readouterr()
    assert TOKEN not in captured.out + captured.err


MUTATIONS = [
    ["wakes", "ack", "W1"],
    ["submit", "missing.json"],
    ["start", "T1", "--policy-version", "2"],
    ["accept", "T1", "--verdict", "accepted"],
    ["review", "T1", "missing.json"],
    ["dispositions", "T1", "missing.json"],
    ["corrections", "T1", "missing.json"],
    ["ci-decision", "T1", "--cause", "code", "--action", "correct"],
    ["head-decision", "T1", "--action", "reject"],
    ["decisions", "T1", "--kind", "scope", "--verbatim", "yes", "--resolves", "E1"],
    ["cancel", "T1", "--verbatim", "cancel"],
    ["close", "T1"],
    ["republish", "T1"],
]


@pytest.mark.parametrize("argv", MUTATIONS)
def test_every_mutation_refuses_without_reason(
    fake: FakeCrucible,
    capsys: pytest.CaptureFixture[str],
    argv: list[str],
) -> None:
    with pytest.raises(SystemExit) as raised:
        run(argv)
    assert raised.value.code == 2
    assert not fake.requests
    assert "--reason" in capsys.readouterr().err


def test_task_related_flags_reach_all_records(fake: FakeCrucible) -> None:
    fake.response = {
        "schema_version": "1.0",
        "id": "T1",
        "latest_attempt": {"id": "A1"},
        "executions": [{"attempts": [{"id": "A1"}]}],
    }
    assert (
        run(
            [
                "task",
                "T1",
                "--events",
                "--pull-request",
                "--attempts",
                "--gates",
                "--report",
                "--evidence",
            ]
        )
        == 0
    )
    assert [(request["method"], request["path"]) for request in fake.requests] == [
        ("GET", "/v1/tasks/T1"),
        ("GET", "/v1/tasks/T1/events"),
        ("GET", "/v1/tasks/T1/pull-request"),
        ("GET", "/v1/attempts/A1"),
        ("GET", "/v1/attempts/A1/gates"),
        ("GET", "/v1/attempts/A1/report"),
        ("GET", "/v1/attempts/A1/evidence"),
    ]


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 422, 500])
def test_server_problem_is_exit_one_and_token_is_redacted(
    fake: FakeCrucible,
    capsys: pytest.CaptureFixture[str],
    status: int,
) -> None:
    fake.status = status
    fake.response = {"type": "refused", "detail": f"bad Bearer {TOKEN} and {TOKEN}"}
    with pytest.raises(SystemExit) as raised:
        run(["tasks"])
    assert raised.value.code == 1
    captured = capsys.readouterr()
    assert "refused" in captured.err
    assert TOKEN not in captured.out + captured.err


def test_republish_404_names_missing_server_capability(
    fake: FakeCrucible,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake.status = 404
    fake.response = {"detail": "Not Found"}
    with pytest.raises(SystemExit) as raised:
        run(["republish", "T1", "--reason", "retry"])
    assert raised.value.code == 1
    assert "republish is unavailable on this Crucible server" in capsys.readouterr().err


def test_unreachable_is_exit_three_and_token_is_absent(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    monkeypatch.setenv("CRUCIBLE_URL", f"http://127.0.0.1:{port}")
    monkeypatch.setenv("CRUCIBLE_TOKEN", TOKEN)
    with pytest.raises(SystemExit) as raised:
        run(["tasks"])
    assert raised.value.code == 3
    captured = capsys.readouterr()
    assert TOKEN not in captured.out + captured.err


@pytest.mark.parametrize("missing", ["CRUCIBLE_URL", "CRUCIBLE_TOKEN"])
def test_missing_environment_is_usage_exit_two(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    missing: str,
) -> None:
    monkeypatch.setenv("CRUCIBLE_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("CRUCIBLE_TOKEN", TOKEN)
    monkeypatch.delenv(missing)
    with pytest.raises(SystemExit) as raised:
        run(["tasks"])
    assert raised.value.code == 2
    assert missing in capsys.readouterr().err


def test_invalid_base_url_is_usage_exit_two(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("CRUCIBLE_URL", "not-a-url")
    monkeypatch.setenv("CRUCIBLE_TOKEN", TOKEN)
    with pytest.raises(SystemExit) as raised:
        run(["health"])
    assert raised.value.code == 2
    assert "must be an http or https URL" in capsys.readouterr().err


def test_table_converts_timestamps_to_chicago(
    fake: FakeCrucible,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake.response = {
        "schema_version": "1.0",
        "items": [
            {
                "id": "T1",
                "state": "running",
                "updated_at": "2026-09-21T16:00:00+00:00",
                "title": "test",
            }
        ],
        "next_cursor": None,
    }
    assert run(["tasks", "--table"]) == 0
    output = capsys.readouterr().out
    assert "2026-09-21 11:00:00 AM CDT" in output
    assert "+00:00" not in output


def test_default_output_is_json(fake: FakeCrucible, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["health"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "ok"


def test_token_is_redacted_even_from_success_response(
    fake: FakeCrucible,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake.response = {"detail": f"server accidentally returned {TOKEN}"}
    assert run(["health"]) == 0
    captured = capsys.readouterr()
    assert TOKEN not in captured.out + captured.err
    assert "[REDACTED]" in captured.out
