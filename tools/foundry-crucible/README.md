# foundry-crucible

Plain command-line access to the Crucible `/v1` operations that Foundry uses.
It has no runtime dependencies outside Python 3.12. The base URL comes only
from `CRUCIBLE_URL`; the bearer token comes only from `CRUCIBLE_TOKEN` and is
redacted from all client error output.

```sh
cd tools/foundry-crucible
uv sync
CRUCIBLE_URL=http://127.0.0.1:8080 \
  CRUCIBLE_TOKEN="$(read-token-safely)" \
  uv run foundry-crucible tasks --table
```

JSON is the default output. Put `--table` before the subcommand for a short
human view whose timestamps use America/Chicago local time.

## Commands

| Command | Crucible operation |
| --- | --- |
| `tasks [--state STATE]` | List tasks. |
| `task ID [--events] [--pull-request] [--attempts] [--gates] [--report] [--evidence]` | Read one task and optionally its related records. |
| `wakes` | List pending wakes. |
| `wakes ack ID --reason TEXT` | Acknowledge a wake with what Foundry did. |
| `submit FILE --reason TEXT` | Submit a TaskContractV1 JSON document. |
| `start ID --policy-version N [routing options] --reason TEXT` | Record Foundry's dispatch decision. |
| `accept ID --verdict VERDICT [--head-sha SHA] --reason TEXT` | Record acceptance. |
| `review ID FILE --reason TEXT` | Submit a ReviewRequest JSON document. |
| `dispositions ID FILE --reason TEXT` | Submit a ReviewDisposition JSON document. |
| `corrections ID FILE --reason TEXT` | Submit a correction JSON document. |
| `ci-decision ID --cause CAUSE --action ACTION --reason TEXT` | Decide a failed CI certification. |
| `head-decision ID --action ACTION --reason TEXT` | Decide a divergent head. |
| `decisions ID --kind KIND --verbatim TEXT --resolves TEXT --reason TEXT` | Record a decision. |
| `cancel ID --verbatim TEXT [--decided-by NAME] --reason TEXT` | Cancel a task. |
| `close ID --reason TEXT` | Close a completed task. |
| `republish ID --reason TEXT` | Retry publication. Older servers return a clear unsupported error. |
| `health` | Check process liveness. |

Every mutation refuses locally without `--reason`. The client sends that
reason in `X-Foundry-Reason`; where Crucible's request contract has a matching
reason, reasoning, or note field, it is also placed there.

Exit codes are 0 for success, 1 for a server refusal, 2 for command or input
usage, and 3 when the server cannot be reached.

## Development

```sh
uv run ruff format --check foundry_crucible tests
uv run ruff check foundry_crucible tests
uv run mypy --strict foundry_crucible tests
uv run pytest
```

`tests/fixtures/crucible-openapi.json` is captured from a running Crucible.
Its exact source commit is recorded in `tests/fixtures/README.md`.

## Notes

A repository CI workflow should run the four development commands above and
gitleaks over the checked-out tree and branch history. This task does not add a
workflow because the repository currently has none and the task excludes it.
