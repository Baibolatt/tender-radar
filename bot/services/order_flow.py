"""Конченый автомат оформления заказа.

Модель здесь не нужна: шаги, вопросы и переходы заданы кодом. Состояние
хранится в базе, поэтому диалог не теряется при перезапуске бота.
"""

import re
from typing import Any

STEP_OCCASION = "order_occasion"
STEP_BOUQUET = "order_bouquet"
STEP_CLIENT_NAME = "order_client_name"
STEP_CLIENT_PHONE = "order_client_phone"
STEP_GIFT = "order_gift"
STEP_RECIPIENT_NAME = "order_recipient_name"
STEP_RECIPIENT_PHONE = "order_recipient_phone"
STEP_ADDRESS = "order_address"
STEP_DATE = "order_date"
STEP_TIME = "order_time"
STEP_CARD = "order_card"
STEP_CARD_TEXT = "order_card_text"
STEP_WISHES = "order_wishes"
STEP_CONFIRM = "confirming"

ORDER_STEPS = frozenset(
    {
        STEP_OCCASION,
        STEP_BOUQUET,
        STEP_CLIENT_NAME,
        STEP_CLIENT_PHONE,
        STEP_GIFT,
        STEP_RECIPIENT_NAME,
        STEP_RECIPIENT_PHONE,
        STEP_ADDRESS,
        STEP_DATE,
        STEP_TIME,
        STEP_CARD,
        STEP_CARD_TEXT,
        STEP_WISHES,
    }
)

STEP_QUESTIONS = {
    STEP_OCCASION: "Подскажите, по какому поводу букет и какой примерно бюджет?",
    STEP_BOUQUET: "Выберите букет кнопкой ниже — или напишите, какие цветы любите.",
    STEP_CLIENT_NAME: "Как вас зовут?",
    STEP_CLIENT_PHONE: "Ваш телефон для связи?",
    STEP_GIFT: "Букет для вас или в подарок?",
    STEP_RECIPIENT_NAME: "Как зовут получателя?",
    STEP_RECIPIENT_PHONE: "Телефон получателя? Флорист позвонит, чтобы согласовать доставку.",
    STEP_ADDRESS: "Куда привезти? Напишите адрес в Таразе.",
    STEP_DATE: "На когда нужен букет?",
    STEP_TIME: "На какое время или в какой промежуток привезти?",
    STEP_CARD: "Нужна открытка?",
    STEP_CARD_TEXT: "Что написать в открытке?",
    STEP_WISHES: (
        "Есть пожелания по оформлению — цветы, упаковка? "
        "Если нет, напишите «нет»."
    ),
}

STEP_HINTS = {
    STEP_OCCASION: "повод и примерный бюджет",
    STEP_BOUQUET: "какой букет выбрать",
    STEP_CLIENT_NAME: "имя клиента, как к нему обращаться",
    STEP_CLIENT_PHONE: "телефон клиента в формате +7 XXX XXX XX XX",
    STEP_GIFT: "букет для себя или в подарок",
    STEP_RECIPIENT_NAME: "имя получателя подарка",
    STEP_RECIPIENT_PHONE: "телефон получателя в формате +7 XXX XXX XX XX",
    STEP_ADDRESS: "адрес доставки в Таразе",
    STEP_DATE: "дату доставки (например «завтра» или «27 октября»)",
    STEP_TIME: "время или промежуток доставки",
    STEP_CARD: "нужна ли открытка (ответ «да» или «нет»)",
    STEP_CARD_TEXT: "текст открытки",
    STEP_WISHES: "пожелания по оформлению (можно ответить «нет»)",
}

EDIT_FIELD_STEPS = {
    "bouquet_id": STEP_BOUQUET,
    "client_name": STEP_CLIENT_NAME,
    "phone": STEP_CLIENT_PHONE,
    "is_gift": STEP_RECIPIENT_NAME,
    "recipient_name": STEP_RECIPIENT_NAME,
    "recipient_phone": STEP_RECIPIENT_PHONE,
    "address": STEP_ADDRESS,
    "delivery_date": STEP_DATE,
    "delivery_time": STEP_TIME,
    "card_text": STEP_CARD,
    "comment": STEP_WISHES,
}


def question(step: str) -> str:
    """Вопрос для текущего шага."""
    return STEP_QUESTIONS.get(step, STEP_QUESTIONS[STEP_OCCASION])


def next_step(draft: dict[str, Any]) -> str:
    """Первый незаполненный шаг. Когда всё собрано — подтверждение."""
    if not draft.get("bouquet_id"):
        return STEP_BOUQUET
    if not draft.get("client_name"):
        return STEP_CLIENT_NAME
    if not draft.get("phone"):
        return STEP_CLIENT_PHONE
    if "is_gift" not in draft:
        return STEP_GIFT
    if draft.get("is_gift"):
        if not draft.get("recipient_name"):
            return STEP_RECIPIENT_NAME
        if not draft.get("recipient_phone"):
            return STEP_RECIPIENT_PHONE
    if not draft.get("address"):
        return STEP_ADDRESS
    if not draft.get("delivery_date"):
        return STEP_DATE
    if not draft.get("delivery_time"):
        return STEP_TIME
    # Открытка: сначала вопрос «Нужна?», потом отдельно текст.
    if "card_needed" not in draft:
        if not draft.get("card_asked"):
            return STEP_CARD
    elif draft.get("card_needed") and not draft.get("card_asked"):
        return STEP_CARD_TEXT
    if not draft.get("wishes_asked"):
        return STEP_WISHES
    return STEP_CONFIRM


def normalize_reply(text: str) -> str:
    """Нижний регистр без знаков препинания — для «да/нет»."""
    value = (text or "").lower().replace("ё", "е")
    return re.sub(r"[^\w\s]", "", value).strip()


def is_yes(text: str) -> bool:
    normalized = normalize_reply(text)
    return normalized in {
        "да",
        "ага",
        "конечно",
        "подтверждаю",
        "верно",
        "все верно",
        "yes",
        "ок",
        "хорошо",
    } or normalized.startswith(("да ", "конечно "))


def is_no(text: str) -> bool:
    normalized = normalize_reply(text)
    return normalized in {
        "нет",
        "не",
        "неверно",
        "изменить",
        "поменять",
        "no",
    } or normalized.startswith(("нет ", "неверно ", "изменить ", "поменять "))


def clean_name(text: str) -> str | None:
    """Убирает «меня зовут» и проверяет, что это похоже на имя."""
    value = (text or "").strip()
    value = re.sub(
        r"^(?:меня зовут|мое имя|моё имя|имя)\s+", "", value, flags=re.IGNORECASE
    )
    value = value.strip(" .,!?;:")
    if len(value) < 2 or len(value) > 200:
        return None
    if re.search(r"\d", value):
        return None
    return value


def parse_gift(text: str) -> bool | None:
    """Понимает, букет для себя или в подарок."""
    raw = normalize_reply(text)
    if not raw:
        return None
    gift_words = (
        "подарок",
        "подарить",
        "в подарок",
        "маме",
        "папе",
        "жене",
        "девушке",
        "другу",
        "подруге",
        "коллеге",
        "сестре",
        "брату",
        "бабушке",
        "дедушке",
        "ребенку",
        "сыну",
        "дочке",
        "любимой",
        "любимому",
    )
    self_words = ("себе", "для себя", "мне", "себя", "не подарок", "без подарка")
    if any(word in raw for word in gift_words):
        return True
    if any(word in raw for word in self_words):
        return False
    return None


def is_skip(text: str) -> bool:
    """Понимает «без открытки», «нет», «пропустить»."""
    raw = normalize_reply(text)
    return raw in {
        "нет",
        "без открытки",
        "нет открытки",
        "не нужно",
        "не надо",
        "пропустить",
        "без пожеланий",
        "ничего",
        "пусто",
        "без открытки пожалуйста",
        "открытка не нужна",
    }


def detect_field_change(text: str) -> tuple[str, str] | None:
    """Понимает «дата 28 октября», «телефон +7...» внутри подтверждения."""
    raw = (text or "").strip()
    lowered = raw.lower().replace("ё", "е")
    rules = (
        ("recipient_phone", ("телефон получателя", "номер получателя")),
        ("recipient_name", ("имя получателя", "получателя зовут")),
        ("delivery_date", ("дата", "дату", "на дату")),
        ("delivery_time", ("время", "ко времени")),
        ("address", ("адрес", "адресу")),
        ("card_text", ("открытка", "открытку", "текст открытки")),
        ("comment", ("пожелания", "пожелание", "оформление")),
        ("bouquet_id", ("букет", "букету")),
        ("is_gift", ("подарок", "сделать подарком")),
        ("phone", ("телефон", "номер")),
        ("client_name", ("имя", "заказчика зовут")),
    )
    edit_words = ("измен", "помен", "исправ", "не тот", "не та", "неверн")
    for field, keywords in rules:
        for keyword in keywords:
            position = lowered.find(keyword)
            if position < 0:
                continue
            value = raw[position + len(keyword) :].strip(" .,!?:;-—")
            has_edit_word = any(word in lowered for word in edit_words)
            question_word = value.lower().startswith(
                ("как", "когд", "како", "что", "где", "сколь")
            )
            # «Какой телефон?» — это вопрос, а не правка заказа.
            if (not value and not has_edit_word) or (
                question_word and not has_edit_word
            ):
                return None
            return field, value
    return None
