-- 0001: initial schema for the Foundry bootstrap ledger.
-- Timestamps are local America/Chicago text, "YYYY-MM-DD HH:MM CDT|CST",
-- exactly the format used by the YAML and JSONL sources.

CREATE TABLE tasks (
    id                TEXT PRIMARY KEY NOT NULL,
    title             TEXT,
    parent            TEXT,
    project           TEXT,
    repository        TEXT,
    scope             TEXT,
    objective         TEXT,
    contract          TEXT NOT NULL CHECK (json_valid(contract)),
    model             TEXT,
    harness           TEXT,
    execution         TEXT,
    state             TEXT NOT NULL CHECK (state IN (
                          'proposed', 'dispatched', 'running', 'reported',
                          'accepted', 'rejected', 'blocked', 'missing',
                          'abandoned', 'done')),
    created           TEXT NOT NULL CHECK (created GLOB
                          '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9] [0-9][0-9]:[0-9][0-9] C[DS]T'),
    updated           TEXT NOT NULL CHECK (updated GLOB
                          '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9] [0-9][0-9]:[0-9][0-9] C[DS]T'),
    refs              TEXT NOT NULL CHECK (json_valid(refs)),
    last_report       TEXT,
    evidence          TEXT NOT NULL CHECK (json_valid(evidence)),
    blockers          TEXT NOT NULL CHECK (json_valid(blockers)),
    decisions_pending TEXT NOT NULL CHECK (json_valid(decisions_pending))
) WITHOUT ROWID;

CREATE TABLE events (
    seq    INTEGER PRIMARY KEY AUTOINCREMENT,
    ts     TEXT NOT NULL CHECK (ts GLOB
               '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9] [0-9][0-9]:[0-9][0-9] C[DS]T'),
    task   TEXT NOT NULL REFERENCES tasks(id),
    event  TEXT NOT NULL,
    who    TEXT NOT NULL,
    detail TEXT
);

CREATE INDEX events_task_seq ON events (task, seq);

CREATE TABLE state_transitions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    task       TEXT NOT NULL REFERENCES tasks(id),
    from_state TEXT CHECK (from_state IS NULL OR from_state IN (
                   'proposed', 'dispatched', 'running', 'reported',
                   'accepted', 'rejected', 'blocked', 'missing',
                   'abandoned', 'done')),
    to_state   TEXT NOT NULL CHECK (to_state IN (
                   'proposed', 'dispatched', 'running', 'reported',
                   'accepted', 'rejected', 'blocked', 'missing',
                   'abandoned', 'done')),
    ts         TEXT NOT NULL CHECK (ts GLOB
                   '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9] [0-9][0-9]:[0-9][0-9] C[DS]T'),
    source     TEXT NOT NULL CHECK (source IN ('event', 'update')),
    event_seq  INTEGER REFERENCES events(seq)
);

CREATE INDEX state_transitions_task ON state_transitions (task, id);

CREATE TABLE import_manifest (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    source_path    TEXT NOT NULL,
    kind           TEXT NOT NULL CHECK (kind IN ('task', 'events')),
    record_id      TEXT,
    source_sha256  TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    record_count   INTEGER NOT NULL,
    imported_at    TEXT NOT NULL
);
