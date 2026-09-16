-- 0002: one-way handoff marker. Once a row exists here the ledger is frozen:
-- every write command refuses and names the Crucible import id. Reads,
-- verify, and export keep working so the record stays inspectable.

CREATE TABLE migrated (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    crucible_import TEXT NOT NULL CHECK (length(crucible_import) > 0),
    marked_at       TEXT NOT NULL CHECK (marked_at GLOB
                        '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9] [0-9][0-9]:[0-9][0-9] C[DS]T')
);
