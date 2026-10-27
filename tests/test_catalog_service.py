"""Тесты каталога: текст и кнопки собирает код из базы."""

from bot.services.catalog_service import (
    catalog_for_model,
    filter_bouquets,
    find_by_name,
    format_price,
    render_card,
    render_list,
    select_for_model,
)
from bot.texts import AVAILABILITY_NOTE


def _bouquet(
    bouquet_id,
    name,
    price,
    description="",
    photo_file_id=None,
):
    return {
        "id": bouquet_id,
        "name": name,
        "price_kzt": price,
        "description": description,
        "photo_file_id": photo_file_id,
        "is_available": 1,
    }


BOUQUETS = [
    _bouquet(1, "Классические розы", 15000, "15 красных роз"),
    _bouquet(2, "Тюльпаны", 8500, "15 тюльпанов"),
    _bouquet(3, "Пионы", 22000, "нежные пионы"),
    _bouquet(4, "Хризантемы", 9500, "осенний букет"),
    _bouquet(5, "Орхидеи", 30000, "орхидеи в кашпо"),
    _bouquet(6, "Ромашки", 7000, "полевые ромашки"),
    _bouquet(7, "Мимоза", 6000, "весенняя мимоза"),
]


def test_price_format_uses_space_and_tenge():
    assert format_price(15000) == "15 000 ₸"
    assert format_price(8500) == "8 500 ₸"
    assert format_price(1000000) == "1 000 000 ₸"


def test_list_has_one_line_per_bouquet_and_max_five():
    view = render_list(BOUQUETS, page_size=5)

    assert view.text.startswith("Вот что есть в каталоге:\n\n")
    assert view.text.count("—") == 5
    assert "🌹 <b>Классические розы</b> — 15 000 ₸" in view.text
    assert "🌷 <b>Тюльпаны</b> — 8 500 ₸" in view.text
    assert view.text.endswith(AVAILABILITY_NOTE)
    assert len(view.bouquet_ids) == 5


def test_list_uses_flower_emojis_and_bold_names():
    view = render_list(
        [
            _bouquet(3, "Пионы", 22000, "нежные пионы"),
            _bouquet(2, "Тюльпаны", 8500, "15 тюльпанов"),
            _bouquet(4, "Хризантемы", 9500, "осенний букет"),
        ]
    )

    assert "🌸 <b>Пионы</b> — 22 000 ₸" in view.text
    assert "🌷 <b>Тюльпаны</b> — 8 500 ₸" in view.text
    assert "🌼 <b>Хризантемы</b> — 9 500 ₸" in view.text


def test_list_buttons_use_bouquet_names():
    view = render_list(BOUQUETS, page_size=5)

    assert (("Классические розы", "bouquet:1"),) in view.keyboard
    assert (("Тюльпаны", "bouquet:2"),) in view.keyboard
    assert (("Показать ещё", "catalog:1"),) in view.keyboard


def test_second_page_shows_rest_and_back_button():
    view = render_list(BOUQUETS, page=1, page_size=5)

    assert view.bouquet_ids == (6, 7)
    assert (("Назад", "catalog:0"),) in view.keyboard
    assert (("Показать ещё", "catalog:2"),) not in view.keyboard


def test_budget_filter_and_over_budget_fallback():
    filtered, reason = filter_bouquets(BOUQUETS, budget=10000)

    assert reason is None
    assert {item["id"] for item in filtered} == {2, 4, 6, 7}

    filtered, reason = filter_bouquets(BOUQUETS, budget=5000)
    assert reason == "over_budget"
    assert [item["id"] for item in filtered] == [7]

    view = render_list(BOUQUETS, budget=5000)
    assert "В бюджете до 5 000 ₸ подходящих нет" in view.text
    assert "Мимоза" in view.text


def test_flover_filter_uses_description():
    filtered, reason = filter_bouquets(BOUQUETS, flower="роз")

    assert reason is None
    assert [item["id"] for item in filtered] == [1]

    filtered, reason = filter_bouquets(BOUQUETS, flower="кактус")
    assert reason == "flower_missing"
    assert len(filtered) == len(BOUQUETS)


def test_card_has_photo_description_and_buttons():
    bouquet = _bouquet(
        1,
        "Классические розы",
        15000,
        "15 красных роз",
        photo_file_id="photo-1",
    )
    view = render_card(bouquet)

    assert view.photo_file_id == "photo-1"
    assert "15 красных роз" in view.text
    assert "15 000 ₸" in view.text
    assert (
        ("Заказать", "order:1"),
        ("Назад к списку", "catalog:0"),
    ) in view.keyboard


def test_find_by_name_exact_and_tokens():
    assert find_by_name(BOUQUETS, "Тюльпаны")["id"] == 2
    assert find_by_name(BOUQUETS, "хочу тюльпаны")["id"] == 2
    assert find_by_name(BOUQUETS, "хочу 15 роз") is None
    assert find_by_name(BOUQUETS, "неизвестный букет") is None


def test_select_for_model_limits_and_filters():
    selected = select_for_model(BOUQUETS, "покажите всё", limit=5)
    assert len(selected) == 5

    selected = select_for_model(BOUQUETS, "до 10000", limit=5)
    assert {item["id"] for item in selected} == {2, 4, 6, 7}


def test_catalog_for_model_is_short_and_has_prices():
    text = catalog_for_model(BOUQUETS[:2])

    assert "1. «Классические розы» — 15 000 ₸" in text
    assert "2. «Тюльпаны» — 8 500 ₸" in text
    assert "15 красных роз" in text
