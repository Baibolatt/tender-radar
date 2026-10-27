-- Начальная схема базы данных.

CREATE TABLE IF NOT EXISTS stores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    florist_chat_id INTEGER,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    daily_dialog_limit INTEGER NOT NULL DEFAULT 100,
    max_ai_calls_per_dialog INTEGER NOT NULL DEFAULT 20,
    max_response_chars INTEGER NOT NULL DEFAULT 900,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS bouquets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id INTEGER NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    price_kzt INTEGER NOT NULL CHECK (price_kzt > 0),
    is_available INTEGER NOT NULL DEFAULT 1 CHECK (is_available IN (0, 1)),
    photo_file_id TEXT,
    sort_order INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_bouquets_store_available
    ON bouquets(store_id, is_available, sort_order);

CREATE TABLE IF NOT EXISTS order_counters (
    store_id INTEGER PRIMARY KEY REFERENCES stores(id) ON DELETE CASCADE,
    last_number INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    public_number TEXT NOT NULL UNIQUE,
    store_id INTEGER NOT NULL REFERENCES stores(id),
    source_update_id INTEGER NOT NULL,
    client_chat_id INTEGER NOT NULL,
    client_name TEXT NOT NULL,
    phone TEXT NOT NULL,
    address TEXT NOT NULL,
    delivery_date TEXT NOT NULL,
    delivery_time TEXT,
    bouquet_id INTEGER NOT NULL REFERENCES bouquets(id),
    bouquet_name TEXT NOT NULL,
    price_kzt INTEGER NOT NULL CHECK (price_kzt > 0),
    card_text TEXT NOT NULL DEFAULT '',
    comment TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'new'
        CHECK (status IN ('new', 'notified', 'in_progress', 'done', 'cancelled')),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (store_id, source_update_id)
);

CREATE INDEX IF NOT EXISTS idx_orders_store_status
    ON orders(store_id, status, created_at);

CREATE TABLE IF NOT EXISTS outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id INTEGER NOT NULL REFERENCES stores(id),
    order_id INTEGER REFERENCES orders(id) ON DELETE SET NULL,
    kind TEXT NOT NULL CHECK (kind IN ('order_notification', 'handoff')),
    chat_id INTEGER NOT NULL,
    payload TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'sent', 'failed')),
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    last_error TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    sent_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_outbox_pending
    ON outbox(status, next_attempt_at);

CREATE TABLE IF NOT EXISTS dialog_sessions (
    store_id INTEGER NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
    chat_id INTEGER NOT NULL,
    state TEXT NOT NULL DEFAULT 'chatting',
    draft_json TEXT NOT NULL DEFAULT '{}',
    history_json TEXT NOT NULL DEFAULT '[]',
    ai_calls INTEGER NOT NULL DEFAULT 0,
    counted_date TEXT,
    handoff_until TEXT,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (store_id, chat_id)
);

CREATE TABLE IF NOT EXISTS usage_daily (
    store_id INTEGER NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
    date TEXT NOT NULL,
    dialogs INTEGER NOT NULL DEFAULT 0,
    ai_calls INTEGER NOT NULL DEFAULT 0,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (store_id, date)
);
