# Contributing

Foundry is an identity plus a small amount of tooling. Contributions are
welcome to the identity text (clarity, harness portability), the ledger tool,
and the documentation.

- Nothing else consumes this repository's `main`, so small documentation
  changes may land directly. Changes to `tools/` go through a pull request
  with tests green.
- `tools/foundry-ledger`: typed Python 3.12, `uv run pytest`, `uv run mypy
  --strict foundry_ledger`, `uv run ruff check`.
- Never add operator-specific state, paths, project names, or policies to
  the tracked tree. Put an example under `examples/` instead.
- No em-dashes in prose, comments, or commit messages.
