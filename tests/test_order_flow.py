"""Тесты шагов автомата заказа без модели."""

from bot.services import order_flow
from bot.services.order_flow import (
    STEP_ADDRESS,
    STEP_BOUQUET,
    STEP_CARD,
    STEP_CARD_TEXT,
    STEP_CLIENT_NAME,
    STEP_CLIENT_PHONE,
    STEP_CONFIRM,
    STEP_DATE,
    STEP_GIFT,
    STEP_RECIPIENT_NAME,
    STEP_RECIPIENT_PHONE,
    STEP_TIME,
    STEP_WISHES,
)


def _full_draft():
    return {
        "bouquet_id": 1,
        "client_name": "Айгуль",
        "phone": "+77001234567",
        "is_gift": False,
        "address": "Тараз, ул. Абая, 10",
        "delivery_date": "2026-10-27",
        "delivery_time": "14:00",
        "card_asked": True,
        "wishes_asked": True,
    }


def test_next_step_starts_with_bouquet():
    assert order_flow.next_step({}) == STEP_BOUQUET


def test_next_step_walks_all_fields():
    draft = {}
    assert order_flow.next_step(draft) == STEP_BOUQUET
    draft["bouquet_id"] = 1
    assert order_flow.next_step(draft) == STEP_CLIENT_NAME
    draft["client_name"] = "Айгуль"
    assert order_flow.next_step(draft) == STEP_CLIENT_PHONE
    draft["phone"] = "+77001234567"
    assert order_flow.next_step(draft) == STEP_GIFT
    draft["is_gift"] = False
    assert order_flow.next_step(draft) == STEP_ADDRESS
    draft["address"] = "Тараз"
    assert order_flow.next_step(draft) == STEP_DATE
    draft["delivery_date"] = "2026-10-27"
    assert order_flow.next_step(draft) == STEP_TIME
    draft["delivery_time"] = "14:00"
    assert order_flow.next_step(draft) == STEP_CARD
    draft["card_asked"] = True
    assert order_flow.next_step(draft) == STEP_WISHES
    draft["wishes_asked"] = True
    assert order_flow.next_step(draft) == STEP_CONFIRM


def test_card_is_asked_before_text():
    draft = {
        "bouquet_id": 1,
        "client_name": "Айгуль",
        "phone": "+77001234567",
        "is_gift": False,
        "address": "Тараз",
        "delivery_date": "2026-10-27",
        "delivery_time": "14:00",
    }

    assert order_flow.next_step(draft) == STEP_CARD
    draft["card_needed"] = True
    assert order_flow.next_step(draft) == STEP_CARD_TEXT
    draft["card_text"] = "С днём рождения!"
    draft["card_asked"] = True
    assert order_flow.next_step(draft) == STEP_WISHES

    no_card = {**draft, "card_needed": False, "card_text": ""}
    assert order_flow.next_step(no_card) == STEP_WISHES


def test_gift_adds_recipient_steps():
    draft = _full_draft()
    draft["is_gift"] = True
    draft.pop("card_asked")
    draft.pop("wishes_asked")

    assert order_flow.next_step(draft) == STEP_RECIPIENT_NAME
    draft["recipient_name"] = "Алия"
    assert order_flow.next_step(draft) == STEP_RECIPIENT_PHONE
    draft["recipient_phone"] = "+77079876543"
    assert order_flow.next_step(draft) == STEP_CARD


def test_yes_no_and_skip():
    assert order_flow.is_yes("Да")
    assert order_flow.is_yes("всё верно")
    assert order_flow.is_no("нет")
    assert not order_flow.is_yes("не знаю")

    assert order_flow.is_skip("без открытки")
    assert order_flow.is_skip("нет")
    # Пожелание «без ленты» нельзя выбрасывать как пропуск.
    assert not order_flow.is_skip("без ленты")


def test_clean_name_and_gift_parsing():
    assert order_flow.clean_name("меня зовут Айгуль") == "Айгуль"
    assert order_flow.clean_name("123") is None
    assert order_flow.clean_name("А") is None

    assert order_flow.parse_gift("в подарок маме") is True
    assert order_flow.parse_gift("для себя") is False
    assert order_flow.parse_gift("не знаю") is None


def test_detect_field_change():
    assert order_flow.detect_field_change("дата 28 октября") == (
        "delivery_date",
        "28 октября",
    )
    assert order_flow.detect_field_change("телефон +7 707 123 45 67") == (
        "phone",
        "+7 707 123 45 67",
    )
    # Вопрос не должен считаться правкой заказа.
    assert order_flow.detect_field_change("какой телефон?") is None
    assert order_flow.detect_field_change("когда будет готово?") is None
