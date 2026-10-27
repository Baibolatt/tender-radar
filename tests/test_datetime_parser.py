"""Тесты разбора дат, времени, бюджета и повода без модели."""

from datetime import date

from bot.services.datetime_parser import (
    extract_flower,
    format_date_ru,
    ordinal_index,
    parse_budget,
    parse_delivery_date,
    parse_delivery_time,
    parse_occasion,
)

TODAY = date(2026, 10, 26)  # понедельник


def test_date_today_tomorrow_after_tomorrow():
    assert parse_delivery_date("сегодня", TODAY) == TODAY
    assert parse_delivery_date("завтра", TODAY) == date(2026, 10, 27)
    assert parse_delivery_date("послезавтра", TODAY) == date(2026, 10, 28)
    assert parse_delivery_date("через 3 дня", TODAY) == date(2026, 10, 29)
    assert parse_delivery_date("через неделю", TODAY) == date(2026, 11, 2)


def test_date_numeric_forms():
    assert parse_delivery_date("27.10", TODAY) == date(2026, 10, 27)
    assert parse_delivery_date("27/10/2026", TODAY) == date(2026, 10, 27)
    assert parse_delivery_date("27-10", TODAY) == date(2026, 10, 27)
    assert parse_delivery_date("2026-10-27", TODAY) == date(2026, 10, 27)


def test_date_in_words():
    assert parse_delivery_date("27 октября", TODAY) == date(2026, 10, 27)
    assert parse_delivery_date("27 октября 2026", TODAY) == date(2026, 10, 27)
    assert parse_delivery_date("на 27 октября", TODAY) == date(2026, 10, 27)


def test_date_weekdays():
    assert parse_delivery_date("в пятницу", TODAY) == date(2026, 10, 30)
    assert parse_delivery_date(
        "в следующую пятницу", TODAY
    ) == date(2026, 11, 6)


def test_date_day_of_month():
    assert parse_delivery_date("27 числа", TODAY) == date(2026, 10, 27)
    assert parse_delivery_date("28 числа", TODAY) == date(2026, 10, 28)
    assert parse_delivery_date("5 числа", TODAY) == date(2026, 11, 5)


def test_date_past_is_returned_for_caller_to_check():
    assert parse_delivery_date("20.10", TODAY) == date(2026, 10, 20)


def test_date_unknown_returns_none():
    assert parse_delivery_date("когда-нибудь", TODAY) is None
    assert parse_delivery_date("", TODAY) is None


def test_date_is_shown_in_russian_not_iso():
    assert format_date_ru(date(2026, 10, 27)) == "27 октября"
    assert format_date_ru("2026-10-27") == "27 октября"
    assert "2026-10-27" != format_date_ru("2026-10-27")


def test_time_forms():
    assert parse_delivery_time("к 15:00") == "15:00"
    assert parse_delivery_time("в 9") == "09:00"
    assert parse_delivery_time("с 14:00 до 16:00") == "14:00–16:00"
    assert parse_delivery_time("14-16") == "14:00–16:00"
    assert parse_delivery_time("утром") == "утром"
    assert parse_delivery_time("вечером") == "вечером"
    assert parse_delivery_time("как можно раньше") == "как можно раньше"
    assert parse_delivery_time("не знаю") is None


def test_budget_forms():
    assert parse_budget("до 15 000") == 15000
    assert parse_budget("20 тысяч") == 20000
    assert parse_budget("15к") == 15000
    assert parse_budget("15000 тенге") == 15000
    assert parse_budget("бюджет 30000") == 30000
    assert parse_budget("500") is None


def test_occasion_and_flower():
    assert parse_occasion("на день рождения") == "день рождения"
    assert parse_occasion("свадьба") == "свадьба"
    assert parse_occasion("на 8 марта") == "8 марта"
    assert parse_occasion("просто так") is None

    assert extract_flower("хочу 15 роз") == "роз"
    assert extract_flower("тюльпаны") == "тюльпан"
    assert extract_flower("мимоза") == "мимоз"


def test_ordinals():
    assert ordinal_index("первый") == 0
    assert ordinal_index("второй вариант") == 1
    assert ordinal_index("третий") == 2
    assert ordinal_index("любой") is None
