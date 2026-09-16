# Harness continuity

Foundry is portable to a harness only once a fresh session in that harness
has been observed loading the identity, reading the ledger, and reporting
live tasks correctly. The probe is read-only and `git status` afterward is
the check that it stayed read-only.

## Probe

From the repository root, in each harness, run: "Follow your
start-of-session procedure and list live tasks with their state. Do not
write anything." Record the harness version, the command used, and the
observed output in the state directory (not here).

## Verified behaviors and findings (September 2026)

| Harness | Discovery | Result |
|---|---|---|
| Claude Code 2.1.x | `CLAUDE.md` shim | `claude -p --permission-mode plan` loaded the identity and listed live tasks with correct states |
| Codex 0.15x | `AGENTS.md` shim | `codex exec --sandbox read-only` failed before reading anything on a host that denies user namespaces (`bwrap` error). Without the sandbox it loaded the identity, listed tasks, and caught a real ledger inconsistency |
| AGY 1.2.x | `AGENTS.md` shim plus its global instruction file | `agy -p --mode plan --add-dir <repo>` loaded the identity and listed tasks |

Findings carried into Crucible's design:

- Codex's built-in sandbox cannot be relied on. Worker isolation must come
  from the execution environment.
- All three harnesses honor a one-line root shim. No identity content needs
  to live in a repository beyond that pointer.
- A "do not write" instruction was respected by all three, but that is a
  claim; `git status` was the check.
