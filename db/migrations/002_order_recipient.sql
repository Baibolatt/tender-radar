-- Получатель подарка и ключ идемпотентности заказа.

ALTER TABLE orders ADD COLUMN recipient_name TEXT;
ALTER TABLE orders ADD COLUMN recipient_phone TEXT;
ALTER TABLE orders ADD COLUMN is_gift INTEGER NOT NULL DEFAULT 0
    CHECK (is_gift IN (0, 1));
ALTER TABLE orders ADD COLUMN idempotency_key TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_store_idempotency
    ON orders(store_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;
