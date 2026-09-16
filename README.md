# Foundry

Foundry is a software-delivery orchestrator that runs inside an AI coding
harness (Claude Code, Codex, or AGY). It turns ideas, issues, and unfinished
projects into reviewed, finished work by writing bounded task contracts,
delegating implementation to worker agents, reviewing their evidence, and
escalating consequential decisions to its operator.

Foundry is judgment. Its companion service, [Crucible](https://github.com/sentania-labs/crucible),
is the deterministic supervisor that will launch workers in isolated
containers, persist lifecycle state, and enforce mechanical completion gates.
Until Crucible passes its readiness gate, Foundry keeps its own small
bootstrap ledger (a SQLite database in a private state directory) and
supervises through whatever delegation the active harness offers.

## What is here

```
identity/FOUNDRY.md      the portable, harness-neutral operating identity
docs/                    delivery policy, ledger design, harness continuity
tools/foundry-ledger/    the SQLite bootstrap ledger CLI
examples/                a sanitized state directory and operator rules
CLAUDE.md, AGENTS.md     one-line shims so each harness finds the identity
```

## What is deliberately not here

The operator's task ledger, private project inventory, personal policies,
worker history, credentials, or anything specific to one machine. Those live
in `FOUNDRY_STATE_DIR` (default `~/.local/state/foundry`), which this
repository's `.gitignore` refuses. See `examples/state-dir/` for the layout.

## Running it

1. Clone this repository.
2. Create the state directory from the example and write your own
   `operator-rules.md` and `bootstrap-contract.md` (what you want Foundry to
   do, in your words).
3. Install the ledger tool: `cd tools/foundry-ledger && uv sync`, then
   `uv run foundry-ledger init`.
4. Start Claude Code, Codex, or AGY in the repository root. The shim points
   the harness at the identity, and the identity's start-of-session
   procedure loads your state.

## License

MIT. See [LICENSE](LICENSE).
