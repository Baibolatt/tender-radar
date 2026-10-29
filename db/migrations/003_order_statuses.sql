-- Новые статусы: new → assembling → ready → delivering → delivered (+ cancelled).
-- Старые значения переносим, CHECK на status убираем — валидация в коде (web/statuses.py).
PRAGMA foreign_keys=OFF;

CREATE TABLE orders_new (
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
    status TEXT NOT NULL DEFAULT 'new',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    recipient_name TEXT,
    recipient_phone TEXT,
    is_gift INTEGER NOT NULL DEFAULT 0 CHECK (is_gift IN (0, 1)),
    idempotency_key TEXT,
    UNIQUE (store_id, source_update_id)
);

INSERT INTO orders_new (
    id, public_number, store_id, source_update_id, client_chat_id, client_name,
    phone, address, delivery_date, delivery_time, bouquet_id, bouquet_name,
    price_kzt, card_text, comment, status, created_at, updated_at,
    recipient_name, recipient_phone, is_gift, idempotency_key
)
SELECT id, public_number, store_id, source_update_id, client_chat_id, client_name,
    phone, address, delivery_date, delivery_time, bouquet_id, bouquet_name,
    price_kzt, card_text, comment,
    CASE status
        WHEN 'notified' THEN 'new'
        WHEN 'in_progress' THEN 'assembling'
        WHEN 'done' THEN 'delivered'
        ELSE status
    END,
    created_at, updated_at, recipient_name, recipient_phone, is_gift, idempotency_key
FROM orders;

DROP TABLE orders;
ALTER TABLE orders_new RENAME TO orders;

CREATE INDEX IF NOT EXISTS idx_orders_store_status
    ON orders(store_id, status, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_store_idempotency
    ON orders(store_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

PRAGMA foreign_keys=ON;
