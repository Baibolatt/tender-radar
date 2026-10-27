"""Красивый каталог: текст и кнопки собирает код, а не модель."""

import html
import re
from dataclasses import dataclass
from typing import Any

from bot.services.datetime_parser import (
    extract_flower,
    normalize_text,
    parse_budget,
    parse_occasion,
)
from bot.texts import AVAILABILITY_NOTE

Button = tuple[str, str]
Keyboard = tuple[tuple[Button, ...], ...]

FLOWER_EMOJI = {
    "роз": "🌹",
    "тюльпан": "🌷",
    "пион": "🌸",
    "хризантем": "🌼",
    "орхиде": "🌺",
    "подсолнух": "🌻",
    "ромашк": "🌼",
    "гербер": "🌼",
    "лили": "🌷",
    "мимоз": "🌿",
    "гвоздик": "🌺",
    "гортензи": "🌸",
}

OCCASION_HINTS = {
    "день рождения": ("роз", "пион", "хризантем", "подсолнух", "гербер", "микс", "ярк"),
    "свадьба": ("бел", "пион", "орхиде", "роз", "пастел"),
    "любовь": ("роз", "пион", "красн", "пастел"),
    "8 марта": ("тюльпан", "мимоз", "весен", "роз"),
    "юбилей": ("премиум", "роз", "пион", "больш"),
    "годовщина": ("роз", "пион", "пастел"),
    "выпускной": ("роз", "тюльпан", "ярк", "солнеч"),
    "извинение": ("роз", "бел", "пастел"),
    "рождение ребёнка": ("мини", "полев", "ромашк", "бел"),
}


@dataclass(frozen=True)
class CatalogView:
    """Готовый текст, кнопки и фото для одного экрана каталога."""

    text: str
    keyboard: Keyboard
    bouquet_ids: tuple[int, ...] = ()
    photo_file_id: str | None = None


def format_price(price_kzt: int) -> str:
    """Цена с пробелом в тысячах и знаком ₸."""
    return f"{int(price_kzt):,}".replace(",", " ") + " ₸"


def emoji_for(bouquet: dict[str, Any]) -> str:
    """Эмодзи по виду цветов из названия или описания."""
    haystack = normalize_text(
        f"{bouquet.get('name', '')} {bouquet.get('description', '')}"
    )
    for stem, emoji in FLOWER_EMOJI.items():
        if stem in haystack:
            return emoji
    return "💐"


def _haystack(bouquet: dict[str, Any]) -> str:
    return normalize_text(
        f"{bouquet.get('name', '')} {bouquet.get('description', '')}"
    )


def filter_bouquets(
    bouquets: list[dict[str, Any]],
    *,
    budget: int | None = None,
    occasion: str | None = None,
    flower: str | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    """Фильтрует каталог по бюджету, поводу и виду цветов.

    Возвращает список и причину, если подходящих не нашлось. Если фильтр
    по поводу или цветку ничего не дал, честно показываем остальной каталог.
    """
    result = list(bouquets)
    reason: str | None = None

    if flower:
        matched = [item for item in result if flower in _haystack(item)]
        if matched:
            result = matched
        else:
            reason = "flower_missing"

    if occasion:
        hints = OCCASION_HINTS.get(occasion, ())
        matched = [
            item
            for item in result
            if any(hint in _haystack(item) for hint in hints)
        ]
        if matched:
            result = matched
        elif reason is None:
            reason = "occasion_missing"

    if budget is not None:
        within = [
            item for item in result if int(item["price_kzt"]) <= int(budget)
        ]
        if within:
            result = within
        else:
            nearest = min(
                result or bouquets, key=lambda item: int(item["price_kzt"])
            )
            return [nearest], "over_budget"

    return result, reason


def _heading(
    *,
    budget: int | None,
    occasion: str | None,
    reason: str | None,
) -> str:
    if reason == "over_budget" and budget is not None:
        return (
            f"В бюджете до {format_price(budget)} подходящих нет. "
            "Ближайший вариант:"
        )
    if reason == "flower_missing":
        return "Такого у нас сейчас нет. Вот что есть:"
    if reason == "occasion_missing":
        return "На этот повод подходящих не нашлось. Вот что есть:"
    if occasion and budget is not None:
        return f"Варианты на {occasion} до {format_price(budget)}:"
    if occasion:
        return f"Варианты на {occasion}:"
    if budget is not None:
        return f"Варианты до {format_price(budget)}:"
    return "Вот что есть в каталоге:"


def render_list(
    bouquets: list[dict[str, Any]],
    *,
    budget: int | None = None,
    occasion: str | None = None,
    flower: str | None = None,
    page: int = 0,
    page_size: int = 5,
) -> CatalogView:
    """Список максимум из 5 букетов и inline-кнопки с названиями."""
    filtered, reason = filter_bouquets(
        bouquets, budget=budget, occasion=occasion, flower=flower
    )
    if not filtered:
        filtered = list(bouquets)

    page_size = max(1, int(page_size))
    pages = max(1, (len(filtered) + page_size - 1) // page_size)
    page = max(0, min(int(page), pages - 1))
    start = page * page_size
    items = filtered[start : start + page_size]

    lines = [_heading(budget=budget, occasion=occasion, reason=reason), ""]
    for bouquet in items:
        lines.append(
            f"{emoji_for(bouquet)} <b>{html.escape(bouquet['name'])}</b> — "
            f"{format_price(bouquet['price_kzt'])}"
        )
    lines.append(AVAILABILITY_NOTE)

    rows: list[tuple[Button, ...]] = []
    for bouquet in items:
        rows.append(((bouquet["name"], f"bouquet:{bouquet['id']}"),))

    navigation: list[Button] = []
    if page > 0:
        navigation.append(("Назад", f"catalog:{page - 1}"))
    if page < pages - 1:
        navigation.append(("Показать ещё", f"catalog:{page + 1}"))
    if navigation:
        rows.append(tuple(navigation))

    return CatalogView(
        text="\n".join(lines),
        keyboard=tuple(rows),
        bouquet_ids=tuple(int(item["id"]) for item in items),
    )


def render_card(bouquet: dict[str, Any]) -> CatalogView:
    """Карточка букета: фото (если есть), описание, цена и кнопки."""
    lines = [
        f"{emoji_for(bouquet)} <b>{html.escape(bouquet['name'])}</b> — "
        f"{format_price(bouquet['price_kzt'])}"
    ]
    description = (bouquet.get("description") or "").strip()
    if description:
        lines.append(html.escape(description))
    lines.append(AVAILABILITY_NOTE)

    keyboard: Keyboard = (
        (
            ("Заказать", f"order:{bouquet['id']}"),
            ("Назад к списку", "catalog:0"),
        ),
    )
    return CatalogView(
        text="\n".join(lines),
        keyboard=keyboard,
        bouquet_ids=(int(bouquet["id"]),),
        photo_file_id=bouquet.get("photo_file_id"),
    )


def find_by_name(
    bouquets: list[dict[str, Any]], text: str
) -> dict[str, Any] | None:
    """Ищет букет по полному названию или по всем словам названия."""
    raw = normalize_text(text)
    if not raw:
        return None
    tokens = set(re.findall(r"[a-zа-я0-9]+", raw))

    for bouquet in bouquets:
        name = normalize_text(bouquet["name"])
        if name == raw:
            return bouquet
        name_tokens = set(re.findall(r"[a-zа-я0-9]+", name))
        if name_tokens and name_tokens <= tokens:
            return bouquet
    return None


def select_for_model(
    bouquets: list[dict[str, Any]],
    text: str,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Отбирает для промпта только позиции, подходящие под запрос клиента."""
    filtered, _ = filter_bouquets(
        bouquets,
        budget=parse_budget(text),
        occasion=parse_occasion(text),
        flower=extract_flower(text),
    )
    if not filtered:
        filtered = list(bouquets)
    return filtered[: max(1, int(limit))]


def catalog_for_model(bouquets: list[dict[str, Any]]) -> str:
    """Короткий список для промпта: id, название, цена, описание."""
    if not bouquets:
        return "Подходящих позиций сейчас нет."
    lines = []
    for bouquet in bouquets:
        description = (bouquet.get("description") or "").strip()
        line = (
            f"{bouquet['id']}. «{bouquet['name']}» — "
            f"{format_price(bouquet['price_kzt'])}"
        )
        if description:
            line += f" — {description[:120]}"
        lines.append(line)
    return "\n".join(lines)
