"""Статусы заказа — единый источник правды для панели флориста."""

STATUSES: list[dict] = [
    {"key": "new", "label": "Новый", "next_action": "Взять в работу"},
    {"key": "assembling", "label": "Собирается", "next_action": "Готово"},
    {"key": "ready", "label": "Готов", "next_action": "В доставку"},
    {"key": "delivering", "label": "Доставляется", "next_action": "Доставлен"},
    {"key": "delivered", "label": "Доставлен", "next_action": None},
    {"key": "cancelled", "label": "Отменён", "next_action": None},
]


def keys() -> tuple[str, ...]:
    return tuple(item["key"] for item in STATUSES)


def is_valid(key: str) -> bool:
    return key in keys()


def next_status(key: str) -> str | None:
    """Следующий статус по порядку; None у terminal-статусов (delivered, cancelled)."""
    for item in STATUSES:
        if item["key"] == key:
            return item["next_action"]
    return None
