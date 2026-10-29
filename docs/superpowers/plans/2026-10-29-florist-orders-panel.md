# Веб-панель заказов флориста — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Веб-панель, где флорист видит заказы из Telegram-бота в реальном времени, меняет статус одной кнопкой, ищет и фильтрует заказы.

**Architecture:** Отдельный asyncio-процесс `python -m web` на aiohttp (уже в зависимостях). Читает ту же SQLite-базу, что и бот. Статусы заказа — единый источник правды в `web/statuses.py`; фронтенд получает их через `/api/statuses` и рисует кнопки динамически. Реальное время — SSE: сервер раз в ~1.5 с сверяет отпечаток таблицы (count/max id/max updated_at) и шлёт `refresh`, клиент перечитывает список.

**Tech Stack:** Python 3.12, aiohttp, aiosqlite, pydantic-settings, pytest + pytest-asyncio. Фронтенд — чистые HTML/CSS/JS (без сборки).

**Spec:** требования заказчика + утверждённый макет (`/workspaces/tender-radar/.preview/florist-orders-mockup.html`). Статусы: `new → assembling → ready → delivering → delivered` + `cancelled` (по-русски: Новый → Собирается → Готов → Доставляется → Доставлен + Отменён).

## Global Constraints

- Отвечать и писать комментарии в коде по-русски; имена переменных, функций, файлов, коммиты — по-английски.
- Никаких API-ключей/паролей в файлах — только переменные окружения / `.env`.
- Статусы не хардкодить в SQL (без CHECK на status) — валидация только в `web/statuses.py`, чтобы добавить статус можно было правкой одного файла.
- Все запросы к заказам фильтруются по `store_id` (для будущих магазинов).
- Пароль панели — из `PANEL_PASSWORD` (SecretStr), сравнение через `secrets.compare_digest`.
- Коммиты заканчивать строкой `Co-Authored-By: Claude Code <noreply@anthropic.com>`.
- Не запускать второй экземпляр бота; веб-процесс не трогает блокировку `data/bot.db.lock`.

## Review Focus

1. Смена статуса на недопустимый (`"bogus"`) — API должен вернуть 400, а не записать мусор в БД.
2. `search` в `/api/orders` по части имени или телефона находит заказ (например `Айг` или `70012`).
3. Новый заказ от бота появляется в `/api/orders` без перезапуска веб-процесса (SSE-отпечаток ловит `MAX(id)`).
4. После `mark_sent` заказ остаётся `new`, а не `notified` (старый статус удалён).
5. `/api/*` без валидной cookie возвращает 401, а не данные.

---

## Task 1: Статусы, миграция и методы репозитория

**Files:**
- Create: `web/__init__.py` (пустой), `web/statuses.py`
- Create: `db/migrations/003_order_statuses.sql`
- Modify: `db/repositories.py` (добавить `list_orders`, `set_status` в `OrderRepository`; убрать перевод в `notified` из `OutboxRepository.mark_sent`)
- Modify: `tests/test_db.py` (строка `assert row[0] == 2` → `== 3`)
- Modify: `tests/test_notifier.py` (тест `test_sends_order_notification_and_marks_notified`)
- Test: `tests/test_order_repository.py` (новый)

**Interfaces:**
- Produces (используют Task 2, 3):
  - `web/statuses.py`:
    ```python
    STATUSES: list[dict] = [
        {"key": "new", "label": "Новый", "next_action": "Взять в работу"},
        {"key": "assembling", "label": "Собирается", "next_action": "Готово"},
        {"key": "ready", "label": "Готов", "next_action": "В доставку"},
        {"key": "delivering", "label": "Доставляется", "next_action": "Доставлен"},
        {"key": "delivered", "label": "Доставлен", "next_action": None},
        {"key": "cancelled", "label": "Отменён", "next_action": None},
    ]
    def keys() -> tuple[str, ...]
    def is_valid(key: str) -> bool
    def next_status(key: str) -> str | None   # следующий по порядку списка, None у terminal
    ```
  - `OrderRepository.list_orders(store_id: int, status: str | None = None, search: str = "") -> list[dict[str, Any]]` — статусы новые сверху (`ORDER BY created_at DESC, id DESC`); `status` — точный ключ или `None`/`""`/`"all"` (все); `search` — подстрока по `client_name` ИЛИ `phone` (`LIKE '%' || ? || '%'`).
  - `OrderRepository.set_status(order_id: int, status: str) -> dict[str, Any] | None` — обновляет `status` и `updated_at = utc_now()`, возвращает строку заказа или `None`, если заказа нет. Статус не валидирует (валидирует вызывающий через `web.statuses`).

- [ ] **Step 1: Напиши падающий тест** `tests/test_order_repository.py`

```python
import pytest
from db.database import Database
from db.repositories import BouquetRepository, OrderDraft, OrderRepository, StoreRepository

@pytest.fixture
async def db(tmp_path):
    database = Database(tmp_path / "test.db")
    await database.connect()
    await database.migrate()
    yield database
    await database.close()

async def _make(db, name="Айгуль", phone="+77001234567", update_id=1):
    store_id = await StoreRepository(db).create("TR", "Магазин", florist_chat_id=999)
    bouquet_id = await BouquetRepository(db).create(store_id, "Розы", 15000)
    order = await OrderRepository(db).create(
        OrderDraft(store_id=store_id, source_update_id=update_id, client_chat_id=555,
                   client_name=name, phone=phone, address="Тараз", delivery_date="2026-10-30",
                   bouquet_id=bouquet_id),
        florist_chat_id=999,
    )
    return store_id, order

async def test_list_orders_newest_first(db):
    store_id, first = await _make(db, "Айгуль", update_id=1)
    _, second = await _make(db, "Болат", "+77009998877", update_id=2)
    rows = await OrderRepository(db).list_orders(store_id)
    assert [r["id"] for r in rows] == [second.id, first.id]

async def test_list_orders_filter_by_status(db):
    store_id, order = await _make(db)
    await OrderRepository(db).set_status(order.id, "ready")
    assert await OrderRepository(db).list_orders(store_id, status="ready") != []
    assert await OrderRepository(db).list_orders(store_id, status="delivering") == []

async def test_list_orders_search_by_name_or_phone(db):
    store_id, _ = await _make(db, "Айгуль", "+77001234567")
    assert len(await OrderRepository(db).list_orders(store_id, search="Айг")) == 1
    assert len(await OrderRepository(db).list_orders(store_id, search="70012")) == 1
    assert await OrderRepository(db).list_orders(store_id, search="неттакого") == []

async def test_set_status_updates_and_returns_row(db):
    store_id, order = await _make(db)
    row = await OrderRepository(db).set_status(order.id, "assembling")
    assert row is not None and row["status"] == "assembling"
    assert await OrderRepository(db).get(order.id)["status"] == "assembling"
    assert await OrderRepository(db).set_status(999999, "new") is None
```

- [ ] **Step 2: Запусти тест, убедись что падает**
  Run: `pytest tests/test_order_repository.py -v`
  Expected: FAIL (нет `list_orders` / `set_status`)

- [ ] **Step 3: Создай `web/statuses.py` и `db/migrations/003_order_statuses.sql`, добавь методы в `db/repositories.py`**

  `web/statuses.py` — содержимое из блоков выше (STATUSES + keys/is_valid/next_status). `next_status` идёт по порядку `STATUSES`, пропускает `cancelled` и terminal (`next_action is None` возвращает `None`).

  `db/migrations/003_order_statuses.sql` — целиком (пересоздание `orders`, маппинг статусов, без CHECK):

```sql
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
```

  В `db/repositories.py`:
  - В `OrderRepository` добавить `list_orders` и `set_status` (подписи выше). `set_status` использует `async with self.db.transaction()` и после `UPDATE` делает `SELECT *` и возвращает `dict(row)` (или `None`).
  - В `OutboxRepository.mark_sent` удалить блок `if order_id is not None: UPDATE orders SET status='notified' ...` (оставить только `UPDATE outbox SET status='sent', sent_at=?`). Параметр `order_id` оставить, но не использовать.

- [ ] **Step 4: Обнови существующие тесты**

  `tests/test_db.py`: `test_migrations_are_idempotent` → `assert row[0] == 3`.
  `tests/test_notifier.py`: переименовать `test_sends_order_notification_and_marks_notified` в `test_sends_order_notification_keeps_status_new`, в конце заменить `assert order_row["status"] == "notified"` на `assert order_row["status"] == "new"`.

- [ ] **Step 5: Запусти тесты**
  Run: `pytest tests/test_order_repository.py tests/test_db.py tests/test_notifier.py -v`
  Expected: PASS

- [ ] **Step 6: Коммит**
```bash
git add web/statuses.py web/__init__.py db/migrations/003_order_statuses.sql db/repositories.py tests/test_order_repository.py tests/test_db.py tests/test_notifier.py
git commit -m "feat: add order statuses, migration and panel repository methods"
```

---

## Task 2: Веб-бэкенд (aiohttp) и тесты API

**Files:**
- Modify: `config.py` (добавить `panel_password`, `panel_host`, `panel_port`)
- Modify: `.env.example`
- Create: `web/__main__.py`, `web/app.py`, `web/auth.py`, `web/handlers.py`
- Test: `tests/test_web_api.py` (новый)

**Interfaces:**
- Consumes: `web.statuses` (Task 1), `db.repositories.OrderRepository.list_orders/set_status` (Task 1), `db.database.Database`.
- Produces (использует Task 3 — фронтенд):

  JSON заказа (ключи в точности): `id, public_number, client_name, phone, address, delivery_date, delivery_time, bouquet_name, price_kzt, card_text, comment, status, created_at, is_gift, recipient_name, recipient_phone`.

  Эндпоинты:
  - `POST /login` body `{"password": str}` → 200 `{"ok": true}` + cookie `session` (HttpOnly, SameSite=Lax); 401 `{"error": "неверный пароль"}`.
  - `POST /logout` → 200 `{"ok": true}`, удаляет сессию.
  - `GET /api/me` → 200 `{"authenticated": true}`; без сессии 401.
  - `GET /api/orders?status=<key|all>&q=<str>` → 200 `{"orders": [...]}` (по `created_at DESC`).
  - `POST /api/orders/{id}/status` body `{"status": str}` → 200 `{"order": {...}}`; недопустимый статус 400 `{"error": "..."}`; заказ не найден 404 `{"error": "..."}`.
  - `GET /api/statuses` → 200 `{"statuses": [{"key","label","next_action"}, ...]}`.
  - `GET /api/events` → SSE `text/event-stream`; каждые ~1.5 с сверяет отпечаток `(SELECT COUNT(*), MAX(id), MAX(updated_at) FROM orders WHERE store_id=?)`; при изменении шлёт `data: refresh\n\n`; раз в ~15 с шлёт `: ping\n\n`.
  - `GET /` → статический `web/static/index.html`; `GET /static/{path}` → файлы из `web/static/`.

  Auth: middleware проверяет cookie на всех `/api/*` (кроме `/api/me` который тоже проверяет и возвращает 401), и возвращает 401 JSON без валидной сессии. Сессии в памяти: `dict[str, float]` token → время истечения (24 ч). Токен — `secrets.token_urlsafe(32)`.

- [ ] **Step 1: Напиши падающий тест** `tests/test_web_api.py`

  Используй `aiohttp.test_utils.TestClient` + `TestServer` с `web.app.create_app(settings, db)`. Настройка: `Settings(_env_file=None, panel_password="secret", database_path=tmp_path/"t.db", bot_token="123:test", ai_api_key="x")`, `Database` с `connect()+migrate()`, магазин и букет создать через репозитории. Проверь минимум:
  - `test_login_wrong_password_returns_401` — `POST /login` с неверным паролем → 401.
  - `test_login_sets_cookie_and_me_ok` — верный пароль → 200, затем `GET /api/me` с cookie → 200.
  - `test_api_requires_auth` — `GET /api/orders` без cookie → 401.
  - `test_orders_list_and_search` — создать 2 заказа, `GET /api/orders?q=Айг` → 1 заказ.
  - `test_set_status_valid` — `POST /api/orders/{id}/status` `{"status":"ready"}` → 200, `status=="ready"`.
  - `test_set_status_invalid_returns_400` — `{"status":"bogus"}` → 400.
  - `test_statuses_endpoint` — `GET /api/statuses` → 6 статусов, первый `key=="new"`.

- [ ] **Step 2: Запусти тест, убедись что падает**
  Run: `pytest tests/test_web_api.py -v`
  Expected: FAIL (нет модуля `web.app`)

- [ ] **Step 3: Реализуй `config.py`, `web/app.py`, `web/auth.py`, `web/handlers.py`, `web/__main__.py`, `.env.example`**

  `config.py`: в `Settings` добавить `panel_password: SecretStr = Field(default=SecretStr(""), alias="PANEL_PASSWORD")`, `panel_host: str = Field(default="127.0.0.1", alias="PANEL_HOST")`, `panel_port: int = Field(default=8000, alias="PANEL_PORT")`.

  `web/app.py`:
  ```python
  def create_app(settings: Settings, db: Database) -> web.Application:
      """Собирает aiohttp-приложение панели."""
  ```
  Роуты по контракту выше; middleware для auth; `app.router.add_static("/static", "web/static")`; `app["settings"] = settings`, `app["db"] = db`, `app["sessions"] = {}`.

  `web/auth.py` — функции `new_session() -> str` (токен), `is_authorized(request) -> bool`, `require_auth` middleware-декоратор; сравнение пароля через `secrets.compare_digest(provided, settings.panel_password.get_secret_value())`.

  `web/__main__.py` — `asyncio.run(main())`: читает `get_settings()`, `Database.connect()+migrate()`, `web.run_app(create_app(settings, db), host=settings.panel_host, port=settings.panel_port)`.

  SSE-хендлер: асинхронный генератор, `StreamResponse` с `Content-Type: text/event-stream` и `Cache-Control: no-cache`; цикл `asyncio.sleep(1.5)` с проверкой отпечатка, `write(b"data: refresh\n\n")`, раз в 15 с `write(b": ping\n\n")`; прерывание по `request.transport`/CancelledError.

- [ ] **Step 4: Запусти тесты**
  Run: `pytest tests/test_web_api.py -v`
  Expected: PASS

- [ ] **Step 5: Коммит**
```bash
git add config.py .env.example web/ tests/test_web_api.py
git commit -m "feat: add florist orders panel backend (auth, API, SSE)"
```

---

## Task 3: Фронтенд (мобильная панель)

**Files:**
- Create: `web/static/index.html`, `web/static/panel.css`, `web/static/panel.js`

**Interfaces:**
- Consumes: API-контракт Task 2. Статусы и кнопки рисуются из `GET /api/statuses` (не хардкодить!). Заказы из `GET /api/orders`. Смена — `POST /api/orders/{id}/status`. Обновления — `GET /api/events` (SSE).

- [ ] **Step 1: Верстай `web/static/index.html` + `web/static/panel.css` по утверждённому макету** `/workspaces/tender-radar/.preview/florist-orders-mockup.html`

  Требования: mobile-first (одна колонка, крупные кнопки, боковой отступ ≥16px, без горизонтальной прокрутки), светлая «ботаническая» тема (токены: холодный зеленовато-белый фон, лиственный зелёный, розово-лепестковый акцент), шрифты Fraunces + Manrope с Google Fonts. Статус — «стебель», который распускается (как в макете). Точное соответствие стилю макета: цвета, отступы, карточки, шапка, фильтр-чипы, поиск, кнопки «Позвонить»/«Карты», тост нового заказа.

  Структура: экран входа (форма пароля) + панель (шапка со счётчиком новых, поиск, чипы фильтра по статусам, лента карточек). Чипы и кнопка смены статуса генерируются из `/api/statuses`.

- [ ] **Step 2: Напиши `web/static/panel.js`**

  Логика: при загрузке `GET /api/me` → если 401, показать экран входа; иначе загрузить статусы и заказы. Подключение `new EventSource("/api/events")` → на `refresh` перечитать заказы (с текущим фильтром/поиском). Новые заказы (id, которого не было) — звук (AudioContext/`<audio>`, запуск по клику «Включить звук») + подсветка + тост. Фильтр по статусу и поиск по имени/телефону — локально на клиенте или query-параметрами. Смена статуса — `fetch POST` и обновление карточки. Кнопки «Позвонить» = `tel:{phone}`, «Карты» = `https://www.google.com/maps/search/?api=1&query={address}`.

- [ ] **Step 3: Проверь вручную**
  Run: `python -m web` (порт из `PANEL_PORT`), открой `http://127.0.0.1:<port>/`. Убедись: вход по паролю, список заказов, фильтр, поиск, смена статуса, `tel:`/`maps:` ссылки. Ошибок в консоли нет.

- [ ] **Step 4: Коммит**
```bash
git add web/static/
git commit -m "feat: add mobile-first florist panel frontend"
```

---

## Task 4: README и финальная проверка

**Files:**
- Create/Modify: `README.md`

- [ ] **Step 1: Добавь в `README.md` раздел «Панель заказов»**

  Запуск: `python -m web` (нужны `PANEL_PASSWORD`, опционально `PANEL_HOST`, `PANEL_PORT` в `.env`). Как добавить/изменить статус — отредактировать `web/statuses.py`. Как работает SSE. Как запустить тесты.

- [ ] **Step 2: Прогони весь набор тестов**
  Run: `pytest -q`
  Expected: все PASS (включая старые тесты бота).

- [ ] **Step 3: Коммит**
```bash
git add README.md
git commit -m "docs: add florist panel run instructions"
```
