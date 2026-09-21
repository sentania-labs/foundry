"""A local fake HTTP server. No test can reach the operator's Crucible service."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest


class FakeCrucible:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.status = 200
        self.response: Any = {"schema_version": "1.0", "status": "ok"}
        self.response_headers: dict[str, str] = {}
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        port = self.server.server_port
        return f"http://127.0.0.1:{port}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                self._handle()

            def do_POST(self) -> None:
                self._handle()

            def _handle(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length)
                owner.requests.append(
                    {
                        "method": self.command,
                        "path": self.path,
                        "headers": dict(self.headers),
                        "body": json.loads(raw) if raw else None,
                    }
                )
                body = json.dumps(owner.response).encode("utf-8")
                self.send_response(owner.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                for name, value in owner.response_headers.items():
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format: str, *args: object) -> None:
                return

        return Handler


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeCrucible]:
    server = FakeCrucible()
    monkeypatch.setenv("CRUCIBLE_URL", server.url)
    monkeypatch.setenv("CRUCIBLE_TOKEN", "test-token-value")
    try:
        yield server
    finally:
        server.close()
