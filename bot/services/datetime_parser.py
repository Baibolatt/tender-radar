"""Разбор дат, времени, бюджета и повода без вызова модели.

Все вычисления детерминированные: один и тот же текст при одной и той же
сегодняшней дате всегда даёт один результат. Это дешевле и надёжнее модели.
"""

import re
from datetime import date, datetime, timedelta

MONTHS_GENITIVE = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)

WEEKDAYS_RU = (
    "понедельник",
    "вторник",
    "среда",
    "четверг",
    "пятница",
    "суббота",
    "воскресенье",
)

MONTH_WORDS = {
    "янв": 1,
    "январь": 1,
    "января": 1,
    "фев": 2,
    "февраль": 2,
    "февраля": 2,
    "мар": 3,
    "март": 3,
    "марта": 3,
    "апр": 4,
    "апрель": 4,
    "апреля": 4,
    "май": 5,
    "мая": 5,
    "июн": 6,
    "июнь": 6,
    "июня": 6,
    "июл": 7,
    "июль": 7,
    "июля": 7,
    "авг": 8,
    "август": 8,
    "августа": 8,
    "сен": 9,
    "сент": 9,
    "сентябрь": 9,
    "сентября": 9,
    "окт": 10,
    "октябрь": 10,
    "октября": 10,
    "ноя": 11,
    "нояб": 11,
    "ноябрь": 11,
    "ноября": 11,
    "дек": 12,
    "декабрь": 12,
    "декабря": 12,
}

WEEKDAY_WORDS = {
    "понедельник": 0,
    "понедельника": 0,
    "пн": 0,
    "вторник": 1,
    "вторника": 1,
    "вт": 1,
    "среда": 2,
    "среду": 2,
    "среды": 2,
    "ср": 2,
    "четверг": 3,
    "четверга": 3,
    "чт": 3,
    "пятница": 4,
    "пятницу": 4,
    "пятницы": 4,
    "пт": 4,
    "суббота": 5,
    "субботу": 5,
    "субботы": 5,
    "сб": 5,
    "воскресенье": 6,
    "воскресенья": 6,
    "вс": 6,
}

FLOWER_WORDS = (
    ("роз", "роз"),
    ("тюльпан", "тюльпан"),
    ("пион", "пион"),
    ("хризантем", "хризантем"),
    ("орхиде", "орхиде"),
    ("подсолнух", "подсолнух"),
    ("ромашк", "ромашк"),
    ("полев", "ромашк"),
    ("гербер", "гербер"),
    ("лили", "лили"),
    ("мимоз", "мимоз"),
    ("гвоздик", "гвоздик"),
    ("гортензи", "гортензи"),
)

OCCASION_WORDS = (
    ("рождение ребёнка", ("рождение ребёнка", "рождение ребенка", "новорожд", "малыш")),
    ("день рождения", ("день рождения", "днюх", "днюш", "рождени")),
    ("свадьба", ("свадьб", "свадеб", "невест")),
    ("8 марта", ("8 марта", "восьмое марта", "женский день")),
    ("юбилей", ("юбиле",)),
    ("годовщина", ("годовщин",)),
    ("выпускной", ("выпускн",)),
    ("извинение", ("извин", "прощения", "прости")),
    ("любовь", ("любов", "романт", "любим")),
)

ORDINALS = {
    "первый": 0,
    "первого": 0,
    "первую": 0,
    "второй": 1,
    "второго": 1,
    "вторую": 1,
    "третий": 2,
    "третьего": 2,
    "третью": 2,
    "четвёртый": 3,
    "четвертый": 3,
    "четвёртого": 3,
    "четвертого": 3,
    "пятый": 4,
    "пятого": 4,
}


def normalize_text(value: str) -> str:
    """Приводит текст к нижнему регистру и убирает лишние пробелы."""
    text = (value or "").lower().replace("ё", "е").replace(" ", " ")
    return re.sub(r"\s+", " ", text).strip()


def _make_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_delivery_date(text: str, today: date) -> date | None:
    """Понимает «сегодня», «завтра», «в пятницу», «27.10», «27 октября»."""
    raw = normalize_text(text)
    if not raw:
        return None

    iso = re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", raw)
    if iso:
        return _make_date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))

    # 27.10, 27/10/2026, 27-10
    numeric = re.search(
        r"(?<!\d)(\d{1,2})\s*[./-]\s*(\d{1,2})(?:\s*[./-]\s*(\d{2,4}))?(?!\d)",
        raw,
    )
    if numeric:
        day = int(numeric.group(1))
        month = int(numeric.group(2))
        year = today.year
        if numeric.group(3):
            year = int(numeric.group(3))
            if year < 100:
                year += 2000
        parsed = _make_date(year, month, day)
        if parsed is not None:
            return parsed

    # 27 октября [2026]
    day_month = re.search(
        r"(?<!\d)(\d{1,2})\s+([а-я]+)(?:\s+(\d{4}))?", raw
    )
    if day_month:
        month = MONTH_WORDS.get(day_month.group(2).strip("."))
        if month is not None:
            year = int(day_month.group(3)) if day_month.group(3) else today.year
            parsed = _make_date(year, month, int(day_month.group(1)))
            if parsed is not None:
                return parsed

    if "послезавтра" in raw:
        return today + timedelta(days=2)
    if "завтра" in raw:
        return today + timedelta(days=1)
    if "сегодня" in raw:
        return today

    through_days = re.search(r"через\s+(\d+)\s*(?:день|дня|дней)", raw)
    if through_days:
        return today + timedelta(days=int(through_days.group(1)))

    if re.search(r"через\s+(?:две|2)\s+недели", raw):
        return today + timedelta(days=14)
    if re.search(r"через\s+(?:неделю|1\s+неделю)", raw):
        return today + timedelta(days=7)

    next_week = "следующ" in raw or "на следующей неделе" in raw
    for word, target in sorted(WEEKDAY_WORDS.items(), key=lambda item: -len(item[0])):
        if not re.search(rf"\b{word}\b", raw):
            continue
        days = (target - today.weekday()) % 7
        if next_week:
            days += 7
        return today + timedelta(days=days)

    day_only = re.search(r"(\d{1,2})\s+числа", raw)
    if day_only:
        day = int(day_only.group(1))
        parsed = _make_date(today.year, today.month, day)
        if parsed is not None and parsed >= today:
            return parsed
        next_month = (today.replace(day=1) + timedelta(days=32)).replace(day=1)
        return _make_date(next_month.year, next_month.month, day)

    return None


def format_date_ru(value: date | str | None) -> str:
    """Показывает дату клиенту: «27 октября», без ГГГГ-ММ-ДД."""
    if value is None:
        return ""
    if isinstance(value, str):
        try:
            value = date.fromisoformat(value)
        except ValueError:
            return value
    return f"{value.day} {MONTHS_GENITIVE[value.month - 1]}"


def format_now_ru(now: datetime, tz_name: str) -> str:
    """Текущие дата, время и день недели для промпта."""
    return (
        f"{now.day} {MONTHS_GENITIVE[now.month - 1]} {now.year}, "
        f"{WEEKDAYS_RU[now.weekday()]}, {now:%H:%M} ({tz_name})"
    )


def _clock(hour: int, minute: int) -> str | None:
    if 0 <= hour <= 23 and 0 <= minute <= 59:
        return f"{hour:02d}:{minute:02d}"
    return None


def parse_delivery_time(text: str) -> str | None:
    """Приводит время к одному виду: «14:00», «14:00–16:00»."""
    raw = normalize_text(text)
    if not raw:
        return None

    if "как можно раньше" in raw or "пораньше" in raw:
        return "как можно раньше"
    if "утр" in raw or "до обеда" in raw:
        return "утром"
    if "обед" in raw:
        return "днём"
    if "вечер" in raw or "после работы" in raw:
        return "вечером"
    if "день" in raw and "рожден" not in raw:
        return "днём"

    raw = raw.replace("–", "-").replace("—", "-")
    interval = re.search(
        r"(?:с\s+)?(\d{1,2})(?::(\d{2}))?\s*(?:до|-)\s*(\d{1,2})(?::(\d{2}))?",
        raw,
    )
    if interval:
        start = _clock(int(interval.group(1)), int(interval.group(2) or 0))
        end = _clock(int(interval.group(3)), int(interval.group(4) or 0))
        if start and end:
            return f"{start}–{end}"

    single = re.search(r"(?:к|в|с|около)\s+(\d{1,2})(?::(\d{2}))?", raw)
    if single:
        clock = _clock(int(single.group(1)), int(single.group(2) or 0))
        if clock:
            return clock

    hour_word = re.search(r"(\d{1,2})\s*(?:час|ч)\b", raw)
    if hour_word:
        clock = _clock(int(hour_word.group(1)), 0)
        if clock:
            return clock

    bare = re.fullmatch(r"\s*(\d{1,2})(?::(\d{2}))?\s*", raw)
    if bare:
        clock = _clock(int(bare.group(1)), int(bare.group(2) or 0))
        if clock:
            return clock

    return None


def parse_budget(text: str) -> int | None:
    """Понимает «до 15 000», «20 тысяч», «15к», «15000 тенге»."""
    raw = normalize_text(text)
    if not raw:
        return None

    # Убираем пробелы между разрядами: «15 000» -> «15000».
    raw = re.sub(r"(?<=\d)\s+(?=\d)", "", raw)

    thousand = re.search(r"(\d{1,4})\s*(?:тыс[а-я]*|к)\b", raw)
    if thousand:
        value = int(thousand.group(1)) * 1000
        if 1000 <= value <= 1_000_000:
            return value

    currency = re.search(r"(\d{3,7})\s*(?:₸|тг|тнг|тенге|kzt)", raw)
    if currency:
        return _budget_value(int(currency.group(1)))

    limit = re.search(
        r"(?:до|бюджет[а-я]*|не дороже|максимум|в пределах|около|примерно)"
        r"\s*(\d{3,7})",
        raw,
    )
    if limit:
        return _budget_value(int(limit.group(1)))

    bare = re.search(r"(?<!\d)(\d{4,7})(?!\d)", raw)
    if bare:
        return _budget_value(int(bare.group(1)))

    return None


def _budget_value(value: int) -> int | None:
    return value if 1000 <= value <= 1_000_000 else None


def parse_occasion(text: str) -> str | None:
    """Определяет повод по ключевым словам."""
    raw = normalize_text(text)
    for name, keywords in OCCASION_WORDS:
        if any(keyword in raw for keyword in keywords):
            return name
    return None


def extract_flower(text: str) -> str | None:
    """Возвращает основу названия цветка для фильтра каталога."""
    raw = normalize_text(text)
    for keyword, stem in FLOWER_WORDS:
        if keyword in raw:
            return stem
    return None


def ordinal_index(text: str) -> int | None:
    """Понимает «первый», «второй», «третий» при выборе из списка."""
    raw = normalize_text(text)
    for word, index in ORDINALS.items():
        if re.search(rf"\b{word}\b", raw):
            return index
    return None
