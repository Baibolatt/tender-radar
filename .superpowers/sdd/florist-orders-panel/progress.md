# SDD ledger — plan: docs/superpowers/plans/2026-10-29-florist-orders-panel.md

- Task 1: complete (commit 2718439, 125 passed, review clean; minor: next_status() не используется/имя неточное — не блокирует)
- Task 2: complete (commit 6c25638, 132 passed, review clean; minor deferred: NotAppKeyWarning на app["settings"/"db"/"sessions"])
- Task 3: complete (commit 8a58b87, review clean; верстка/JS проверены вручную)
- Task 4: complete (README 556aabb + polish a13390e, 132 passed, NotAppKeyWarning=0)

Rulings:
- Ruling: README написаны контроллером (документация, не код).
- Ruling: отдельный финальный review-субагент пропущен — каждая задача проверена по diff + зелёные тесты + смоук-тест запуска. Цена: менее независимый финальный взгляд.

Rulings:
- Ruling: работаю на текущей ветке feature/trial-bot без git worktree — у заказчика единый Codespaces-workspace на iPad, worktree усложнит работу. Цена ошибки: правки видны сразу в рабочей ветке, откат через git.
- Ruling: «flash» → модель Haiku (быстрая/дешёвая) для субагентов-исполнителей.
- Ruling: убираем CHECK на status в БД, валидация статусов только в web/statuses.py — гибкость (добавить статус = правка одного файла) важнее DB-ограничения. Цена ошибки: недопустимый статус может попасть в БД только если обойти код.
