-- Мультиканальность: клиентом может быть Telegram-чат или номер WhatsApp.
-- Идентификаторы клиента и сообщения теперь TEXT, канал хранится в channel.
-- Существующие строки остаются каналом telegram.
PRAGMA foreign_keys=OFF;

CREATE TABLE dialog_sessions_new (
    store_id INTEGER NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
    channel TEXT NOT NULL DEFAULT 'telegram',
    chat_id TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'chatting',
    draft_json TEXT NOT NULL DEFAULT '{}',
    history_json TEXT NOT NULL DEFAULT '[]',
    ai_calls INTEGER NOT NULL DEFAULT 0,
    counted_date TEXT,
    handoff_until TEXT,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (store_id, channel, chat_id)
);

INSERT INTO dialog_sessions_new (
    store_id, channel, chat_id, state, draft_json, history_json, ai_calls,
    counted_date, handoff_until, updated_at
)
SELECT store_id, 'telegram', CAST(chat_id AS TEXT), state, draft_json,
    history_json, ai_calls, counted_date, handoff_until, updated_at
FROM dialog_sessions;

DROP TABLE dialog_sessions;
ALTER TABLE dialog_sessions_new RENAME TO dialog_sessions;

CREATE TABLE orders_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    public_number TEXT NOT NULL UNIQUE,
    store_id INTEGER NOT NULL REFERENCES stores(id),
    channel TEXT NOT NULL DEFAULT 'telegram',
    source_update_id TEXT NOT NULL,
    client_chat_id TEXT NOT NULL,
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
    status TEXT NOT NULL DEFAULT 'new',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    recipient_name TEXT,
    recipient_phone TEXT,
    is_gift INTEGER NOT NULL DEFAULT 0 CHECK (is_gift IN (0, 1)),
    idempotency_key TEXT,
    UNIQUE (store_id, channel, source_update_id)
);

INSERT INTO orders_new (
    id, public_number, store_id, channel, source_update_id, client_chat_id,
    client_name, phone, address, delivery_date, delivery_time, bouquet_id,
    bouquet_name, price_kzt, card_text, comment, status, created_at,
    updated_at, recipient_name, recipient_phone, is_gift, idempotency_key
)
SELECT id, public_number, store_id, 'telegram', CAST(source_update_id AS TEXT),
    CAST(client_chat_id AS TEXT), client_name, phone, address, delivery_date,
    delivery_time, bouquet_id, bouquet_name, price_kzt, card_text, comment,
    status, created_at, updated_at, recipient_name, recipient_phone, is_gift,
    idempotency_key
FROM orders;

DROP TABLE orders;
ALTER TABLE orders_new RENAME TO orders;

CREATE INDEX IF NOT EXISTS idx_orders_store_status
    ON orders(store_id, status, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_store_idempotency
    ON orders(store_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE TABLE handoffs_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id INTEGER NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
    channel TEXT NOT NULL DEFAULT 'telegram',
    client_chat_id TEXT NOT NULL,
    client_name TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT '',
    last_message TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'new',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

INSERT INTO handoffs_new (
    id, store_id, channel, client_chat_id, client_name, reason, last_message,
    status, created_at, updated_at
)
SELECT id, store_id, 'telegram', CAST(client_chat_id AS TEXT), client_name,
    reason, last_message, status, created_at, updated_at
FROM handoffs;

DROP TABLE handoffs;
ALTER TABLE handoffs_new RENAME TO handoffs;

CREATE INDEX IF NOT EXISTS idx_handoffs_store_status
    ON handoffs(store_id, status, created_at);

PRAGMA foreign_keys=ON;
