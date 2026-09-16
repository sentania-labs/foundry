# Bootstrap ledger

Until Crucible is authoritative, Foundry's system of record is one SQLite
file, `ledger.sqlite`, in the state directory, managed by
`tools/foundry-ledger`. It replaced an earlier YAML-per-task layout; those
files are retained read-only as bootstrap evidence and never written again.

## Why SQLite

Transactions, uniqueness on task IDs, an ordered event sequence, explicit
migrations, and a one-to-one mapping onto Crucible's bootstrap import
contract. No service to run. One writable ledger, ever.

## Shape

- `tasks`: one row per task with the full contract, state, timestamps,
  references, last report, evidence, blockers, pending decisions. State is
  CHECK-constrained to the lifecycle below.
- `events`: append-only, `seq` ordered, with timestamp, task, event, who,
  detail. The operator's verbatim go for any consequential action is an
  event recorded before the action.
- `state_transitions`: derived from events and updates.
- `import_manifest` and `schema_migrations`: provenance and versioning.

## Lifecycle

`proposed` -> `dispatched` -> `running` -> `reported` -> `accepted` |
`rejected`; side states `blocked`, `missing`, `abandoned`; terminal `done`.
`reported` means the worker claims completion. `accepted` means Foundry
looked at the evidence. Only Foundry moves a task past `reported`.

## Commands

See `tools/foundry-ledger/README.md`. The ones Foundry uses every session:
`live`, `show`, `add`, `update`, `event`, `verify`, `export`.

## Handoff to Crucible

`foundry-ledger export --format crucible` produces a `BootstrapExportV1`
bundle. Crucible imports, verifies counts and content hash, and commits it as
authoritative; `foundry-ledger mark-migrated` then makes this database
read-only. The procedure is specified in Crucible's repository.
