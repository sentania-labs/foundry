# foundry-ledger

SQLite bootstrap ledger for Foundry. One database file is the only writable
record; the YAML task files and `events.jsonl` become read-only bootstrap
evidence (imported once) or generated exports.

## Install

```sh
cd tools/foundry-ledger
uv sync                     # creates .venv with pyyaml, pytest, mypy
uv run foundry-ledger --help
```

Python 3.12, stdlib `sqlite3`, `pyyaml` for import and export only.

Database path: `--db PATH`, else `$FOUNDRY_LEDGER_DB`, else
`~/.local/state/foundry/ledger.sqlite`.

## Commands

| Command | What it does |
| --- | --- |
| `init` | Create the database file (and parent dirs) and apply all migrations. |
| `migrate` | Apply pending migrations in order. Idempotent; refuses a renamed or unknown migration. |
| `import --tasks DIR --events FILE` | Load `DIR/*.yaml` and `FILE` into an empty ledger in one transaction. Every file is validated first; any problem aborts with nothing written. Refuses a ledger that already holds rows. |
| `verify` | Recount and rehash the imported records against `import_manifest`. Exit 0 when intact, 1 on any mismatch. |
| `add [--id ID] [--title ...] [--contract JSON] ... [--who W --detail D]` | Create a task and append an event named after its initial state. Id defaults to the next `FDY-NNNN`, state to `proposed`, timestamps to now. |
| `update ID [--field value ...] [--clear FIELD] [--state S --who W --detail D]` | Change fields and bump `updated`. `--state` also appends an event named after the new state and records the transition. |
| `event ID NAME --who W [--detail D]` | Append an event. If `NAME` is a lifecycle state the task moves to it and a transition is recorded. A move to the task's current state is rejected. |
| `list` | All tasks: id, state, updated, title. |
| `live` | Tasks whose state is not `done` or `abandoned`. |
| `show ID` | The task as YAML, then its events and transitions. |
| `export --dir DIR [--force]` | Write `DIR/tasks/<id>.yaml` and `DIR/events.jsonl` in the source shape. Refuses to overwrite without `--force`. |
| `export --format crucible --dir DIR [--force]` | Write `DIR/crucible.json`, the handoff bundle for Crucible (below). |
| `mark-migrated --crucible-import ID` | Freeze the ledger after Crucible has imported it. One-way: from then on every write command (`import`, `add`, `update`, `event`, `mark-migrated`) refuses and names `ID`; reads, `verify`, and both exports keep working. |

Every write is one transaction. `list`, `live`, `show`, `verify`, and
`export` open the database read-only. Timestamps are written as
`YYYY-MM-DD HH:MM CDT|CST` in America/Chicago, the same text format as the
source files, and the schema rejects anything else.

Valid states (from `ledger/README.md`): `proposed`, `dispatched`, `running`,
`reported`, `accepted`, `rejected`, `blocked`, `missing`, `abandoned`, `done`.

## Crucible bundle

`export --format crucible` writes one JSON document:

| Key | Content |
| --- | --- |
| schema_version | `"1.0"` |
| source | `tool` (name and version), `db_sha256` (hash of the database file bytes), `exported_at` (local timestamp) |
| tasks | every task record, id order, same fields as the YAML |
| events | every event in `seq` order, with `seq` |
| counts | `tasks`, `events` |
| content_sha256 | sha256 of the canonical JSON of `{"tasks": [...], "events": [...]}`: keys sorted, separators `,` and `:`, UTF-8, no whitespace. Recomputable by the receiver from `tasks` and `events` alone. |

## Schema

Migrations live in `foundry_ledger/migrations/NNNN_name.sql` and are recorded
in `schema_migrations (version, name, applied_at)`.

### tasks

| Column | Type | Notes |
| --- | --- | --- |
| id | TEXT PK | stable, never reused |
| title, parent, project, repository, scope, objective, model, harness, execution, last_report | TEXT | nullable scalars, exactly as in the YAML |
| contract, refs, evidence, blockers, decisions_pending | TEXT JSON | `json_valid` CHECK; key order preserved |
| state | TEXT NOT NULL | CHECK against the valid states |
| created, updated | TEXT NOT NULL | local timestamp, format CHECK |

### events

| Column | Type | Notes |
| --- | --- | --- |
| seq | INTEGER PK AUTOINCREMENT | file order on import, append order after |
| ts | TEXT NOT NULL | local timestamp, format CHECK |
| task | TEXT NOT NULL | FK to tasks.id |
| event, who | TEXT NOT NULL | |
| detail | TEXT | nullable |

### state_transitions

| Column | Type | Notes |
| --- | --- | --- |
| id | INTEGER PK AUTOINCREMENT | |
| task | TEXT NOT NULL | FK to tasks.id |
| from_state | TEXT | nullable, CHECK against valid states |
| to_state | TEXT NOT NULL | CHECK against valid states |
| ts | TEXT NOT NULL | local timestamp, format CHECK |
| source | TEXT NOT NULL | `event` (derived from a lifecycle-named event) or `update` (`update --state`) |
| event_seq | INTEGER | FK to events.seq |

On import, transitions are derived from events whose name is a lifecycle
state, chained per task in file order (`from_state` is the previous derived
state, null for the first). After import, `from_state` is the task's current
state at the time of the change.

### migrated

| Column | Type | Notes |
| --- | --- | --- |
| id | INTEGER PK | always 1; at most one row |
| crucible_import | TEXT NOT NULL | the id Crucible assigned to its import |
| marked_at | TEXT NOT NULL | local timestamp |

### import_manifest

| Column | Type | Notes |
| --- | --- | --- |
| id | INTEGER PK AUTOINCREMENT | |
| source_path | TEXT NOT NULL | file imported |
| kind | TEXT NOT NULL | `task` (one row per YAML file), `events`, or `transitions` (the derived rows) |
| record_id | TEXT | task id for `task` rows |
| source_sha256 | TEXT NOT NULL | hash of the raw source bytes |
| content_sha256 | TEXT NOT NULL | hash of the canonical parsed content; what `verify` recomputes |
| record_count | INTEGER NOT NULL | 1 per task, line count for events, derived row count for transitions |
| imported_at | TEXT NOT NULL | local timestamp |

`verify` covers the imported records only: appended events, new tasks,
and later transitions are not checked. Any change to an imported task,
including a state move made by `update --state` or by a lifecycle-named
`event`, is reported as a mismatch by design: the manifest is a migration
receipt, and the intended sequence is import, verify, then start writing.
JSON key order is part of the hashed content because the export reproduces it.

Import is strict so nothing is coerced on the way in: every task file must
carry exactly the README fields, scalars must be strings or null, `contract`
and `refs` must be mappings, the list fields must be lists, timestamps and
state must be valid, and `events.jsonl` must have one object per line with no
blank lines, no CRLF, and a trailing newline. Any violation aborts the whole
import with nothing written.

## Development

```sh
uv run pytest
uv run mypy --strict foundry_ledger
```

Tests use a temp database and synthetic fixtures under `tests/fixtures/`
(`ledger_clean`, and `ledger_shifted` with one deliberately malformed record
that the state CHECK must reject).
