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

Run the handoff from the Foundry repository with an administrator operating
Crucible locally. `HANDOFF_DIR` must be a new private temporary directory;
`IMPORT_ID` is the id returned by submit.

```sh
cd tools/foundry-ledger
uv run foundry-ledger export --format crucible --dir "$HANDOFF_DIR"

cd /path/to/crucible
uv run crucible-admin --reason "prepare authority handoff for verification" \
  bootstrap submit \
  --file "$HANDOFF_DIR/crucible.json" --owner foundry
uv run crucible-admin bootstrap show "$IMPORT_ID"

# Stop here until the report's counts, state map, content hash, and field diff
# match the export and the operator's verbatim authorization is recorded.
uv run crucible-admin --reason "OPERATOR'S RECORDED WORDS" \
  bootstrap commit "$IMPORT_ID"

cd /path/to/foundry/tools/foundry-ledger
uv run foundry-ledger mark-migrated --crucible-import "$IMPORT_ID"
```

Export and submit are reversible: a verified import is not yet authoritative.
`bootstrap commit` is the irreversible authority handoff and requires the
operator's verbatim words. `mark-migrated` is the irreversible local freeze;
use the same recorded authorization and run it only after the committed
import is visible through `foundry-crucible tasks`. After both steps,
`foundry-crucible tasks`, `wakes`, and `task ID` replace bootstrap-ledger
reads, and all operational decisions go through Crucible.
