# TREE.md — карта репозитория

Автогенерировано: `scripts/gen_tree.sh` (не редактируй вручную).
Папки до 3 уровней; у каждой папки — одна строка описания.

```
.
  .env.example
  .gitignore
  .preview/ — мокапы интерфейса
    florist-orders-mockup.html
    index.html
  .superpowers/ — служебные артефакты superpowers (можно игнорировать)
    sdd/
      2026-10-30-whatsapp-integration/
        progress.md
      florist-orders-panel/
        progress.md
  CLAUDE.md
  README.md
  ai/ — клиент модели и промпты
    __init__.py
    client.py
    prompts.py
    schemas.py
  bot/ — Telegram-бот и WhatsApp: обработчики, сервисы, запуск
    __init__.py
    handlers/ — обработчики сообщений и кнопок Telegram
      __init__.py
      client.py
    logging_setup.py
    main.py
    middlewares/ — middleware aiogram: сервисы, ошибки, контекст
      __init__.py
      errors.py
      services.py
      update_context.py
    run_web.py
    services/ — бизнес-логика: диалог, заказы, каталог, WhatsApp
      __init__.py
      catalog_service.py
      conversation_service.py
      datetime_parser.py
      dialog_service.py
      notifier.py
      order_flow.py
      order_service.py
      whatsapp_client.py
    single_instance.py
    texts.py
    whatsapp_webhook.py
  config.py
  db/ — SQLite: подключение, миграции, репозитории
    __init__.py
    catalog_seed.json
    database.py
    migrations/ — SQL-миграции схемы БД
      001_init.sql
      002_order_recipient.sql
      003_order_statuses.sql
      004_handoffs.sql
      005_multi_channel.sql
    repositories.py
    seed_catalog.py
  docs/ — документация и планы
    superpowers/ — планы разработки
      plans/ — планы по датам
        2026-10-29-florist-orders-panel.md
        2026-10-30-whatsapp-integration.md
  pytest.ini
  requirements.txt
  scripts/ — вспомогательные скрипты
    debug_ai.py
  tests/ — тесты
    test_ai_client.py
    test_catalog.py
    test_catalog_service.py
    test_datetime_parser.py
    test_db.py
    test_dialog_service.py
    test_handoffs.py
    test_handoffs_api.py
    test_notifier.py
    test_order_flow.py
    test_order_repository.py
    test_order_service.py
    test_prompts.py
    test_single_instance.py
    test_smoke.py
    test_update_context.py
    test_web_api.py
    test_whatsapp_client.py
    test_whatsapp_integration.py
    test_whatsapp_webhook.py
  web/ — панель заказов флориста (aiohttp)
    __init__.py
    __main__.py
    app.py
    auth.py
    handlers.py
    keys.py
    static/ — статика панели (HTML/CSS/JS)
      index.html
      panel.css
      panel.js
    statuses.py
```
