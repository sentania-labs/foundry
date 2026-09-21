from __future__ import annotations

import json
from pathlib import Path


def test_every_client_route_exists_in_checked_crucible_openapi() -> None:
    document = json.loads(
        (Path(__file__).parent / "fixtures" / "crucible-openapi.json").read_text(encoding="utf-8")
    )
    paths = document["paths"]
    routes = {
        ("get", "/v1/tasks"),
        ("get", "/v1/tasks/{task_id}"),
        ("get", "/v1/tasks/{task_id}/events"),
        ("get", "/v1/tasks/{task_id}/pull-request"),
        ("get", "/v1/attempts/{attempt_id}"),
        ("get", "/v1/attempts/{attempt_id}/gates"),
        ("get", "/v1/attempts/{attempt_id}/report"),
        ("get", "/v1/attempts/{attempt_id}/evidence"),
        ("get", "/v1/wakes"),
        ("post", "/v1/wakes/{wake_id}/ack"),
        ("post", "/v1/tasks"),
        ("post", "/v1/tasks/{task_id}/start"),
        ("post", "/v1/tasks/{task_id}/accept"),
        ("post", "/v1/tasks/{task_id}/review"),
        ("post", "/v1/tasks/{task_id}/dispositions"),
        ("post", "/v1/tasks/{task_id}/corrections"),
        ("post", "/v1/tasks/{task_id}/ci-decision"),
        ("post", "/v1/tasks/{task_id}/head-decision"),
        ("post", "/v1/tasks/{task_id}/decisions"),
        ("post", "/v1/tasks/{task_id}/cancel"),
        ("post", "/v1/tasks/{task_id}/close"),
        ("post", "/v1/tasks/{task_id}/republish"),
        ("get", "/v1/health"),
    }
    missing = sorted(
        f"{method.upper()} {path}" for method, path in routes if method not in paths.get(path, {})
    )
    assert not missing, "client routes absent from Crucible OpenAPI: " + ", ".join(missing)
