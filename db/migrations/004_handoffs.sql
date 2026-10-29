-- Заявки «нужен живой человек» для панели флориста.
CREATE TABLE IF NOT EXISTS handoffs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id INTEGER NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
    client_chat_id INTEGER NOT NULL,
    client_name TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT '',
    last_message TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'new',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_handoffs_store_status
    ON handoffs(store_id, status, created_at);
