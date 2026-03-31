-- Kajima Mailroom Traceability Database Schema
-- SQLite with WAL mode for concurrent read/write safety.
-- Every file operation is logged here BEFORE the physical move.

PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS file_actions (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    filename          TEXT    NOT NULL,
    original_path     TEXT    NOT NULL,
    destination_path  TEXT,
    department        TEXT,
    document_type     TEXT,
    confidence        REAL,
    ai_reasoning      TEXT,
    metadata_json     TEXT,
    status            TEXT    NOT NULL CHECK(status IN ('classified','junk','undetermined','reverted','failed')),
    failure_reason    TEXT,
    checksum          TEXT    NOT NULL,
    file_size_bytes   INTEGER NOT NULL,
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    reverted_at       TIMESTAMP,
    reverted_by       TEXT
);

CREATE INDEX IF NOT EXISTS idx_file_actions_status     ON file_actions(status);
CREATE INDEX IF NOT EXISTS idx_file_actions_department  ON file_actions(department);
CREATE INDEX IF NOT EXISTS idx_file_actions_created     ON file_actions(created_at);
CREATE INDEX IF NOT EXISTS idx_file_actions_checksum    ON file_actions(checksum);
CREATE INDEX IF NOT EXISTS idx_file_actions_filename    ON file_actions(filename);

-- Notification log — tracks what was sent and when
CREATE TABLE IF NOT EXISTS notifications (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    file_action_id  INTEGER NOT NULL REFERENCES file_actions(id),
    channel         TEXT    NOT NULL,
    message         TEXT    NOT NULL,
    sent_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    success         BOOLEAN NOT NULL DEFAULT 1
);

-- Integration push log — CM9, TechnologyOne
CREATE TABLE IF NOT EXISTS integration_pushes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    file_action_id  INTEGER NOT NULL REFERENCES file_actions(id),
    target_system   TEXT    NOT NULL CHECK(target_system IN ('cm9','techone')),
    attempt_number  INTEGER NOT NULL DEFAULT 1,
    status          TEXT    NOT NULL CHECK(status IN ('success','failed','pending')),
    response_code   INTEGER,
    response_body   TEXT,
    pushed_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_integration_status ON integration_pushes(status);

-- Schema version tracking
CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

INSERT OR IGNORE INTO schema_version (version) VALUES (1);
