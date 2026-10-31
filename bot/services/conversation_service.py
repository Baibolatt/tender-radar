"""Общая логика диалога для всех каналов: автомат заказа, каталог и модель.

Сервис не знает про Telegram или WhatsApp: клиента идентифицирует строка
user_id, канал приходит отдельным параметром. Модель вызывается только для
свободных вопросов и понимания свободного текста. Даты, телефоны, цены,
кнопки, номера заказов и сводку собирает код.
"""

import contextvars
import html
import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from ai.client import AIClient, AIError
from ai.prompts import build_step_prompt, build_system_prompt
from bot.services import order_flow
from bot.services.catalog_service import (
    Keyboard,
    catalog_for_model,
    find_by_name,
    render_card,
    render_list,
    select_for_model,
)
from bot.services.datetime_parser import (
    extract_flower,
    format_date_ru,
    format_now_ru,
    normalize_text,
    ordinal_index,
    parse_budget,
    parse_delivery_date,
    parse_delivery_time,
    parse_occasion,
)
from bot.services.order_service import (
    MAX_DELIVERY_DAYS,
    OrderService,
    OrderValidationError,
)
from bot.texts import (
    BOT_HONEST_TEXT,
    CAPABILITIES_TEXT,
    EDIT_MENU_TEXT,
    HANDOFF_TEXT,
    INPUT_TOO_LONG_TEXT,
    STALE_CONFIRM_TEXT,
    START_TEXT,
    THANKS_TEXT,
)
from config import Settings
from db.database import Database
from db.repositories import (
    BouquetRepository,
    DialogSessionRepository,
    HandoffRepository,
    Order,
    OutboxRepository,
    UsageRepository,
)

logger = logging.getLogger(__name__)

# Канал текущего сообщения. ContextVar не смешивает параллельные диалоги
# и избавляет от протаскивания channel через каждый приватный метод.
_CHANNEL: contextvars.ContextVar[str] = contextvars.ContextVar(
    "conversation_channel", default="telegram"
)

MAX_HISTORY_MESSAGES = 8
CATALOG_HISTORY_REPLY = "Показал каталог."

SUMMARY_KEYBOARD: Keyboard = (
    (("Всё верно", "confirm:yes"), ("Изменить", "confirm:edit")),
)

CARD_KEYBOARD: Keyboard = ((("Да", "card:yes"), ("Нет", "card:no")),)

GREETINGS = {
    "привет",
    "здравствуйте",
    "здравствуй",
    "добрый день",
    "добрый вечер",
    "доброе утро",
    "салам",
    "ассалам алейкум",
    "assalamu alaikum",
    "приветствую",
}

THANKS = {
    "спасибо",
    "благодарю",
    "рахмет",
    "спасибо большое",
    "большое спасибо",
}

BOT_QUESTIONS = (
    "ты бот",
    "вы бот",
    "ты робот",
    "вы робот",
    "ты человек",
    "вы человек",
    "искусственный интеллект",
    "ты живой",
)

CAPABILITY_QUESTIONS = (
    "что ты умеешь",
    "что вы умеете",
    "чем помочь",
    "что можешь",
)

HANDOFF_WORDS = (
    "человек",
    "менеджер",
    "оператор",
    "живой",
    "позовите",
    "соедините",
)

ANGRY_WORDS = (
    "обман",
    "жалоб",
    "верните",
    "ужас",
    "отвратит",
    "недовол",
    "мошенник",
    "надоел",
    "сколько можно",
    "позор",
)

ORDER_INTENT_WORDS = (
    "заказать",
    "оформить",
    "купить",
    "подобрать букет",
    "подбери букет",
    "хочу букет",
    "нужен букет",
    "можно заказать",
    "сделать заказ",
    "новый заказ",
)

CATALOG_INTENT_WORDS = (
    "каталог",
    "что есть",
    "какие есть",
    "покажи",
    "показать",
    "варианты",
    "букет",
    "цветы",
    "сколько стоят",
    "цены",
)

ORDER_DRAFT_KEYS = (
    "client_name",
    "phone",
    "is_gift",
    "recipient_name",
    "recipient_phone",
    "address",
    "delivery_date",
    "delivery_time",
    "bouquet_id",
    "card_text",
    "comment",
    "occasion",
    "budget_kzt",
)


@dataclass(frozen=True)
class ConversationResult:
    """Результат обработки одного сообщения или нажатия кнопки."""

    reply: str
    handoff: bool = False
    blocked: bool = False
    failed: bool = False
    order_draft: dict[str, Any] = field(default_factory=dict)
    ai_calls: int = 0
    order: Order | None = None
    keyboard: Keyboard = ()
    photo_file_id: str | None = None


class ConversationService:
    """Ведёт диалог и автомат заказа для любого канала. Заказ создаёт код."""

    def __init__(
        self,
        db: Database,
        settings: Settings,
        ai_client: AIClient,
        store: dict[str, Any],
        order_service: OrderService | None = None,
    ) -> None:
        self.db = db
        self.settings = settings
        self.ai = ai_client
        self.store = store

        self.bouquets = BouquetRepository(db)
        self.sessions = DialogSessionRepository(db)
        self.usage = UsageRepository(db)
        self.outbox = OutboxRepository(db)
        self.handoffs = HandoffRepository(db)
        self.orders = order_service or OrderService(db, settings, store)

    async def process_message(
        self, user_id: str, text: str, channel: str, source_id: str
    ) -> ConversationResult:
        """Обрабатывает сообщение клиента в указанном канале."""
        token = _CHANNEL.set(channel)
        try:
            return await self._reply(str(user_id), text, str(source_id))
        finally:
            _CHANNEL.reset(token)

    async def process_callback(
        self, user_id: str, data: str, channel: str, source_id: str
    ) -> ConversationResult:
        """Обрабатывает нажатие кнопки клиента в указанном канале."""
        token = _CHANNEL.set(channel)
        try:
            return await self._handle_callback(
                str(user_id), data, str(source_id)
            )
        finally:
            _CHANNEL.reset(token)

    @staticmethod
    def _channel() -> str:
        """Канал текущего сообщения."""
        return _CHANNEL.get()

    def _client_id_for_payload(self, user_id: str) -> str | int:
        """Telegram id клиента числовой; WhatsApp всегда строкой.

        Отрицательные Telegram id групп тоже остаются числом.
        """
        if self._channel() != "telegram":
            return user_id
        digits = user_id.lstrip("-")
        return int(user_id) if digits.isdigit() else user_id

    async def _reply(
        self, user_id: str, text: str, source_id: str
    ) -> ConversationResult:
        store_id = int(self.store["id"])
        today = self._today().isoformat()
        session = await self.sessions.ensure(
            store_id, user_id, channel=self._channel()
        )

        # Счётчики ведём только для статистики, клиентов не блокируем.
        if session["counted_date"] != today:
            await self.sessions.mark_counted(
                store_id, user_id, today, channel=self._channel()
            )
            session = await self.sessions.ensure(
                store_id, user_id, channel=self._channel()
            )

        if len(text) > self.settings.max_input_chars:
            return ConversationResult(
                reply=INPUT_TOO_LONG_TEXT,
                blocked=True,
                order_draft=self._load_draft(session),
                ai_calls=int(session["ai_calls"]),
            )

        draft = self._load_draft(session)
        history = self._load_history(session)
        state = session["state"]

        if state == order_flow.STEP_CONFIRM:
            return await self._handle_confirmation_text(
                user_id, text, source_id, session, draft, history
            )

        if state in order_flow.ORDER_STEPS:
            return await self._handle_order_text(
                user_id, text, session, state, draft, history
            )

        return await self._handle_chatting(
            user_id, text, today, session, draft, history
        )

    async def _handle_chatting(
        self,
        user_id: str,
        text: str,
        today: str,
        session: dict[str, Any],
        draft: dict[str, Any],
        history: list[dict[str, str]],
    ) -> ConversationResult:
        store_id = int(self.store["id"])

        # Сначала честный ответ на «ты бот?», иначе слово «человек»
        # ошибочно уводит диалог к флористу.
        small_talk = self._small_talk(text)
        if small_talk is not None:
            reply, keyboard = small_talk
            await self._save_chat(
                store_id, user_id, "chatting", draft, history, text, reply
            )
            return ConversationResult(
                reply=reply,
                order_draft=draft,
                keyboard=keyboard,
            )

        if self._is_handoff_request(text) or self._is_angry(text):
            return await self._handoff(
                store_id=store_id,
                user_id=user_id,
                text=text,
                draft=draft,
                history=history,
                reason="Клиент просит живого флориста",
            )

        budget = parse_budget(text)
        occasion = parse_occasion(text)
        flower = extract_flower(text)
        order_intent = self._is_order_intent(text)
        catalog_intent = self._is_catalog_intent(text)

        if not (
            order_intent
            or catalog_intent
            or budget
            or occasion
            or flower
        ):
            return await self._model_reply(
                user_id, text, today, session, draft, history
            )

        bouquets = await self.bouquets.list_available(store_id)
        bouquet = find_by_name(bouquets, text)

        if order_intent:
            draft["flow_id"] = draft.get("flow_id") or uuid.uuid4().hex
            if budget is not None:
                draft["budget_kzt"] = budget
            if occasion:
                draft["occasion"] = occasion
            if budget or occasion or flower:
                self._set_catalog_filters(draft, budget, occasion, flower)
            else:
                draft.setdefault("catalog_page", 0)

            if bouquet is not None:
                draft["bouquet_id"] = int(bouquet["id"])
                return await self._ask_next(
                    user_id, draft, history, user_text=text
                )

            if budget or occasion or flower:
                return await self._ask_next(
                    user_id, draft, history, user_text=text
                )

            return await self._ask_step(
                user_id,
                order_flow.STEP_OCCASION,
                draft,
                history,
                user_text=text,
            )

        # Просто смотрит каталог — оформление не начинаем.
        if bouquet is not None:
            view = render_card(bouquet)
            await self._save_chat(
                store_id,
                user_id,
                "chatting",
                draft,
                history,
                text,
                f"Показал карточку «{bouquet['name']}».",
            )
            return ConversationResult(
                reply=view.text,
                order_draft=draft,
                keyboard=view.keyboard,
                photo_file_id=view.photo_file_id,
            )

        if budget or occasion or flower:
            self._set_catalog_filters(draft, budget, occasion, flower)
        else:
            draft.setdefault("catalog_page", 0)
        view = await self._catalog_view(store_id, draft)
        await self._save_chat(
            store_id,
            user_id,
            "chatting",
            draft,
            history,
            text,
            CATALOG_HISTORY_REPLY,
        )
        return ConversationResult(
            reply=view.text,
            order_draft=draft,
            keyboard=view.keyboard,
        )

    async def _handle_order_text(
        self,
        user_id: str,
        text: str,
        session: dict[str, Any],
        state: str,
        draft: dict[str, Any],
        history: list[dict[str, str]],
    ) -> ConversationResult:
        store_id = int(self.store["id"])
        today = self._today()

        if self._is_handoff_request(text) or self._is_angry(text):
            return await self._handoff(
                store_id=store_id,
                user_id=user_id,
                text=text,
                draft=draft,
                history=history,
                reason="Клиент просит живого флориста",
            )

        small_talk = self._small_talk(text)
        if small_talk is not None:
            answer = small_talk[0]
            if state == order_flow.STEP_BOUQUET:
                view = await self._catalog_view(store_id, draft)
                reply = f"{answer}\n\n{view.text}"
                await self._save_chat(
                    store_id,
                    user_id,
                    state,
                    draft,
                    history,
                    text,
                    answer,
                )
                return ConversationResult(
                    reply=reply,
                    order_draft=draft,
                    keyboard=view.keyboard,
                )
            reply = f"{answer}\n\n{order_flow.question(state)}"
            await self._save_chat(
                store_id, user_id, state, draft, history, text, reply
            )
            return ConversationResult(reply=reply, order_draft=draft)

        # Свободный вопрос внутри оформления: сначала отвечаем, потом
        # повторяем текущий шаг. Текст открытки и пожелания не трогаем.
        if (
            state
            not in (
                order_flow.STEP_CARD,
                order_flow.STEP_CARD_TEXT,
                order_flow.STEP_WISHES,
            )
            and self._is_free_question(state, text)
        ):
            return await self._flow_question(
                user_id, text, session, state, draft, history
            )

        if state == order_flow.STEP_OCCASION:
            budget = parse_budget(text)
            occasion = parse_occasion(text)
            flower = extract_flower(text)
            if budget is not None:
                draft["budget_kzt"] = budget
            if occasion:
                draft["occasion"] = occasion
            self._set_catalog_filters(draft, budget, occasion, flower)
            return await self._ask_next(user_id, draft, history, user_text=text)

        if state == order_flow.STEP_BOUQUET:
            bouquets = await self.bouquets.list_available(store_id)
            bouquet = find_by_name(bouquets, text)
            if bouquet is not None:
                draft["bouquet_id"] = int(bouquet["id"])
                return await self._ask_next(
                    user_id, draft, history, user_text=text
                )

            index = ordinal_index(text)
            catalog_ids = draft.get("catalog_ids") or []
            if index is not None and 0 <= index < len(catalog_ids):
                chosen = next(
                    (
                        item
                        for item in bouquets
                        if int(item["id"]) == int(catalog_ids[index])
                    ),
                    None,
                )
                if chosen is not None:
                    draft["bouquet_id"] = int(chosen["id"])
                    return await self._ask_next(
                        user_id, draft, history, user_text=text
                    )

            budget = parse_budget(text)
            occasion = parse_occasion(text)
            flower = extract_flower(text)
            if budget or occasion or flower:
                if budget is not None:
                    draft["budget_kzt"] = budget
                if occasion:
                    draft["occasion"] = occasion
                self._set_catalog_filters(draft, budget, occasion, flower)
                return await self._ask_next(
                    user_id, draft, history, user_text=text
                )

            view = await self._catalog_view(store_id, draft)
            reply = (
                "Не понял, какой букет. Выберите кнопкой ниже "
                "или напишите название.\n\n" + view.text
            )
            await self._save_chat(
                store_id,
                user_id,
                order_flow.STEP_BOUQUET,
                draft,
                history,
                text,
                CATALOG_HISTORY_REPLY,
            )
            return ConversationResult(
                reply=reply, order_draft=draft, keyboard=view.keyboard
            )

        if state == order_flow.STEP_CLIENT_NAME:
            name = order_flow.clean_name(text)
            if name:
                draft["client_name"] = name
                return await self._ask_next(
                    user_id, draft, history, user_text=text
                )
            return await self._ask_step(
                user_id,
                state,
                draft,
                history,
                text,
                prefix="Подскажите, пожалуйста, имя — как к вам обращаться?",
            )

        if state == order_flow.STEP_CLIENT_PHONE:
            phone = self.orders.normalize_phone(text)
            if phone:
                draft["phone"] = phone
                return await self._ask_next(
                    user_id, draft, history, user_text=text
                )
            return await self._ask_step(
                user_id,
                state,
                draft,
                history,
                text,
                prefix=(
                    "Кажется, в номере ошибка. Напишите, пожалуйста, "
                    "в формате +7 707 123 45 67 — после +7 должно быть "
                    "10 цифр."
                ),
            )

        if state == order_flow.STEP_GIFT:
            is_gift = order_flow.parse_gift(text)
            if is_gift is not None:
                draft["is_gift"] = is_gift
                if not is_gift:
                    draft.pop("recipient_name", None)
                    draft.pop("recipient_phone", None)
                return await self._ask_next(
                    user_id, draft, history, user_text=text
                )
            return await self._ask_step(
                user_id,
                state,
                draft,
                history,
                text,
                prefix="Подскажите, букет для вас или в подарок?",
            )

        if state == order_flow.STEP_RECIPIENT_NAME:
            name = self._recipient_name(draft, text)
            if name:
                draft["recipient_name"] = name
                return await self._ask_next(
                    user_id, draft, history, user_text=text
                )
            return await self._ask_step(
                user_id,
                state,
                draft,
                history,
                text,
                prefix="Как зовут получателя?",
            )

        if state == order_flow.STEP_RECIPIENT_PHONE:
            phone = self._recipient_phone(draft, text)
            if phone:
                draft["recipient_phone"] = phone
                return await self._ask_next(
                    user_id, draft, history, user_text=text
                )
            return await self._ask_step(
                user_id,
                state,
                draft,
                history,
                text,
                prefix=(
                    "Проверьте, пожалуйста, номер получателя: "
                    "например, +7 707 123 45 67 — после +7 должно быть "
                    "10 цифр."
                ),
            )

        if state == order_flow.STEP_ADDRESS:
            address = text.strip()
            if len(address) >= 3:
                draft["address"] = address[:500]
                return await self._ask_next(
                    user_id, draft, history, user_text=text
                )
            return await self._ask_step(
                user_id,
                state,
                draft,
                history,
                text,
                prefix="Куда привезти? Напишите адрес в Таразе, пожалуйста.",
            )

        if state == order_flow.STEP_DATE:
            parsed = parse_delivery_date(text, today)
            if parsed is None:
                return await self._ask_step(
                    user_id,
                    state,
                    draft,
                    history,
                    text,
                    prefix=(
                        "Не понял дату. Напишите, например, «завтра» "
                        "или «27 октября»."
                    ),
                )
            if parsed < today:
                return await self._ask_step(
                    user_id,
                    state,
                    draft,
                    history,
                    text,
                    prefix="Эта дата уже прошла. На какую дату оформить?",
                )
            if parsed > today + timedelta(days=MAX_DELIVERY_DAYS):
                return await self._ask_step(
                    user_id,
                    state,
                    draft,
                    history,
                    text,
                    prefix=(
                        f"Пока принимаем заказы на {MAX_DELIVERY_DAYS} дней "
                        "вперёд. Выберите дату поближе, пожалуйста."
                    ),
                )
            draft["delivery_date"] = parsed.isoformat()
            return await self._ask_next(user_id, draft, history, user_text=text)

        if state == order_flow.STEP_TIME:
            normalized = parse_delivery_time(text)
            if normalized:
                draft["delivery_time"] = normalized
                return await self._ask_next(
                    user_id, draft, history, user_text=text
                )
            if parse_delivery_date(text, today) is not None:
                return await self._ask_step(
                    user_id,
                    state,
                    draft,
                    history,
                    text,
                    prefix=(
                        "Похоже, это дата. Уточните, пожалуйста, время: "
                        "«к 15:00» или «с 14:00 до 16:00»."
                    ),
                )
            raw = normalize_text(text)
            if raw in {
                "не знаю",
                "любое",
                "как получится",
                "на ваше усмотрение",
                "когда сможете",
                "как удобно",
            }:
                draft["delivery_time"] = "согласовать с флористом"
            else:
                draft["delivery_time"] = text.strip()[:100]
            return await self._ask_next(user_id, draft, history, user_text=text)

        if state == order_flow.STEP_CARD:
            if order_flow.is_yes(text):
                return await self._save_card_answer(
                    user_id, True, draft, history, text
                )
            if order_flow.is_no(text) or order_flow.is_skip(text):
                return await self._save_card_answer(
                    user_id, False, draft, history, text
                )
            return await self._ask_step(
                user_id,
                state,
                draft,
                history,
                text,
                prefix="Нужна открытка? Можно ответить «Да» или «Нет».",
            )

        if state == order_flow.STEP_CARD_TEXT:
            card_text = (text or "").strip()
            if not card_text:
                return await self._ask_step(
                    user_id,
                    state,
                    draft,
                    history,
                    text,
                    prefix="Что написать в открытке?",
                )
            # Текст открытки сохраняем дословно, без переистолкования.
            draft["card_needed"] = True
            draft["card_text"] = card_text[:1000]
            draft["card_asked"] = True
            return await self._ask_next(user_id, draft, history, user_text=text)

        if state == order_flow.STEP_WISHES:
            if order_flow.is_skip(text):
                draft["comment"] = ""
            else:
                draft["comment"] = text.strip()[:1000]
            draft["wishes_asked"] = True
            return await self._ask_next(user_id, draft, history, user_text=text)

        # Неизвестное состояние — начинаем с ближайшего шага.
        return await self._ask_next(user_id, draft, history, user_text=text)

    async def _flow_question(
        self,
        user_id: str,
        text: str,
        session: dict[str, Any],
        state: str,
        draft: dict[str, Any],
        history: list[dict[str, str]],
    ) -> ConversationResult:
        """Отвечает на свободный вопрос и возвращает клиента к шагу."""
        store_id = int(self.store["id"])
        ai_calls = await self._reserve_ai_call(store_id, user_id, session)

        bouquets = await self.bouquets.list_available(store_id)
        system_prompt = build_system_prompt(
            self.store["name"],
            self._relevant_catalog(bouquets, text, draft),
            self._draft_for_prompt(draft),
            now_text=self._now_text(),
            ready_minutes=self.settings.ready_minutes,
        )
        messages = [
            *history[-(MAX_HISTORY_MESSAGES - 1) :],
            {"role": "user", "content": text},
        ]

        try:
            completion = await self.ai.complete(system_prompt, messages)
        except AIError as exc:
            logger.warning("AI не ответил на вопрос в оформлении: %s", exc)
            return await self._ask_step(
                user_id,
                state,
                draft,
                history,
                text,
                prefix=(
                    "Сейчас не могу ответить — флорист уточнит.\n\n"
                    + order_flow.question(state)
                ),
            )

        await self.usage.add_tokens(
            store_id,
            self._today().isoformat(),
            completion.prompt_tokens,
            completion.completion_tokens,
        )
        self._log_ai_usage(store_id, user_id, ai_calls, completion)
        self._merge_model_fields(draft, completion, bouquets)

        if completion.response.handoff:
            await self._notify_handoff(
                store_id=store_id,
                user_id=user_id,
                text=text,
                reason="Вопрос во время оформления заказа",
                draft=draft,
                session=session,
            )

        answer = self._truncate(
            self._escape(completion.response.reply),
            self._store_limit(
                "max_response_chars", self.settings.max_response_chars
            ),
        )

        if state == order_flow.STEP_BOUQUET:
            view = await self._catalog_view(store_id, draft)
            reply = f"{answer}\n\n{view.text}"
            await self._save_chat(
                store_id,
                user_id,
                state,
                draft,
                history,
                text,
                answer,
            )
            return ConversationResult(
                reply=reply,
                order_draft=draft,
                keyboard=view.keyboard,
                ai_calls=ai_calls,
                handoff=completion.response.handoff,
            )

        if completion.response.handoff:
            reply = f"{answer}\n\n{HANDOFF_TEXT}\n\n{order_flow.question(state)}"
        else:
            reply = f"{answer}\n\n{order_flow.question(state)}"
        await self._save_chat(
            store_id, user_id, state, draft, history, text, answer
        )
        return ConversationResult(
            reply=reply,
            order_draft=draft,
            ai_calls=ai_calls,
            handoff=completion.response.handoff,
        )

    async def _handle_confirmation_text(
        self,
        user_id: str,
        text: str,
        source_id: str,
        session: dict[str, Any],
        draft: dict[str, Any],
        history: list[dict[str, str]],
    ) -> ConversationResult:
        store_id = int(self.store["id"])

        if self._is_handoff_request(text) or self._is_angry(text):
            return await self._handoff(
                store_id=store_id,
                user_id=user_id,
                text=text,
                draft=draft,
                history=history,
                reason="Клиент просит живого флориста",
            )

        small_talk = self._small_talk(text)
        if small_talk is not None:
            summary = await self.orders.summary(draft)
            reply = f"{small_talk[0]}\n\n{summary}"
            await self._save_chat(
                store_id,
                user_id,
                order_flow.STEP_CONFIRM,
                draft,
                history,
                text,
                reply,
            )
            return ConversationResult(
                reply=reply,
                order_draft=draft,
                keyboard=SUMMARY_KEYBOARD,
            )

        if order_flow.is_yes(text):
            return await self._create_order(
                user_id, source_id, draft, history, user_text=text
            )

        change = order_flow.detect_field_change(text)
        if change is not None:
            return await self._apply_field_change(
                user_id, change, draft, history, user_text=text
            )

        if order_flow.is_no(text):
            return await self._show_edit_menu(
                store_id, user_id, draft, history
            )

        if self._is_ready_question(text):
            summary = await self.orders.summary(draft)
            reply = self.orders.ready_text() + "\n\n" + summary
            await self._save_chat(
                store_id,
                user_id,
                order_flow.STEP_CONFIRM,
                draft,
                history,
                text,
                reply,
            )
            return ConversationResult(
                reply=reply, order_draft=draft, keyboard=SUMMARY_KEYBOARD
            )

        if "?" in text or self._looks_like_question(text):
            return await self._confirmation_question(
                user_id, text, session, draft, history
            )

        summary = await self.orders.summary(draft)
        reply = (
            "Пожалуйста, выберите «Всё верно» или «Изменить».\n\n"
            + summary
        )
        await self._save_chat(
            store_id,
            user_id,
            order_flow.STEP_CONFIRM,
            draft,
            history,
            text,
            reply,
        )
        return ConversationResult(
            reply=reply, order_draft=draft, keyboard=SUMMARY_KEYBOARD
        )

    async def _handle_callback(
        self, user_id: str, data: str, source_id: str
    ) -> ConversationResult:
        store_id = int(self.store["id"])
        session = await self.sessions.ensure(
            store_id, user_id, channel=self._channel()
        )
        draft = self._load_draft(session)
        history = self._load_history(session)
        state = session["state"]
        data = data or ""

        if data.startswith("catalog:"):
            draft["catalog_page"] = self._as_int(
                data.split(":", 1)[1], default=0
            )
            view = await self._catalog_view(store_id, draft)
            next_state = (
                order_flow.STEP_BOUQUET
                if state in order_flow.ORDER_STEPS
                else "chatting"
            )
            await self._save(
                store_id, user_id, next_state, draft, history
            )
            return ConversationResult(
                reply=view.text,
                order_draft=draft,
                keyboard=view.keyboard,
            )

        if data.startswith("bouquet:"):
            bouquet = await self._available_bouquet(
                store_id, data.split(":", 1)[1]
            )
            if bouquet is None:
                view = await self._catalog_view(store_id, draft)
                reply = (
                    "Этот букет больше недоступен. "
                    "Выберите, пожалуйста, другой.\n\n" + view.text
                )
                await self._save(
                    store_id, user_id, order_flow.STEP_BOUQUET, draft, history
                )
                return ConversationResult(
                    reply=reply, order_draft=draft, keyboard=view.keyboard
                )
            view = render_card(bouquet)
            await self._save(store_id, user_id, state, draft, history)
            return ConversationResult(
                reply=view.text,
                order_draft=draft,
                keyboard=view.keyboard,
                photo_file_id=view.photo_file_id,
            )

        if data.startswith("order:"):
            bouquet = await self._available_bouquet(
                store_id, data.split(":", 1)[1]
            )
            if bouquet is None:
                view = await self._catalog_view(store_id, draft)
                reply = (
                    "Этот букет больше недоступен. "
                    "Выберите, пожалуйста, другой.\n\n" + view.text
                )
                await self._save(
                    store_id, user_id, order_flow.STEP_BOUQUET, draft, history
                )
                return ConversationResult(
                    reply=reply, order_draft=draft, keyboard=view.keyboard
                )
            draft["bouquet_id"] = int(bouquet["id"])
            draft["flow_id"] = draft.get("flow_id") or uuid.uuid4().hex
            return await self._ask_next(user_id, draft, history)

        if data in {"card:yes", "card:no"}:
            if state != order_flow.STEP_CARD:
                return ConversationResult(
                    reply="Кнопка уже неактуальна.", order_draft=draft
                )
            needed = data == "card:yes"
            return await self._save_card_answer(
                user_id,
                needed,
                draft,
                history,
                "Да" if needed else "Нет",
            )

        if data == "confirm:yes":
            if state != order_flow.STEP_CONFIRM:
                return ConversationResult(
                    reply=STALE_CONFIRM_TEXT, order_draft=draft
                )
            return await self._create_order(
                user_id,
                source_id,
                draft,
                history,
                user_text="Всё верно",
            )

        if data == "confirm:edit":
            return await self._show_edit_menu(
                store_id, user_id, draft, history
            )

        if data.startswith("edit:"):
            field = data.split(":", 1)[1]
            return await self._apply_edit(user_id, field, draft, history)

        if data == "back:confirm":
            return await self._summary_outcome(
                user_id, draft, history, user_text=None
            )

        return ConversationResult(reply="Кнопка уже неактуальна.", order_draft=draft)

    async def _save_card_answer(
        self,
        user_id: str,
        needed: bool,
        draft: dict[str, Any],
        history: list[dict[str, str]],
        user_text: str,
    ) -> ConversationResult:
        """Запоминает «да/нет» и, если нужно, спрашивает текст открытки."""
        if needed:
            draft["card_needed"] = True
            draft.pop("card_asked", None)
            draft.pop("card_text", None)
        else:
            draft["card_needed"] = False
            draft["card_text"] = ""
            draft["card_asked"] = True
        return await self._ask_next(
            user_id, draft, history, user_text=user_text
        )

    async def _ask_next(
        self,
        user_id: str,
        draft: dict[str, Any],
        history: list[dict[str, str]],
        *,
        user_text: str | None = None,
        prefix: str = "",
        history_reply: str | None = None,
    ) -> ConversationResult:
        """Переходит к первому незаполненному шагу или к сводке."""
        store_id = int(self.store["id"])
        step = order_flow.next_step(draft)

        if step == order_flow.STEP_CONFIRM:
            return await self._summary_outcome(
                user_id, draft, history, user_text=user_text
            )

        if step == order_flow.STEP_BOUQUET:
            view = await self._catalog_view(store_id, draft)
            reply = f"{prefix}\n\n{view.text}".strip() if prefix else view.text
            await self._save_chat(
                store_id,
                user_id,
                step,
                draft,
                history,
                user_text,
                history_reply or CATALOG_HISTORY_REPLY,
            )
            return ConversationResult(
                reply=reply, order_draft=draft, keyboard=view.keyboard
            )

        reply = order_flow.question(step)
        # Без префикса-уточнения формулируем вопрос через ИИ.
        if not prefix:
            reply = await self._step_question(store_id, step)
        if prefix:
            reply = f"{prefix}\n\n{reply}"
        await self._save_chat(
            store_id,
            user_id,
            step,
            draft,
            history,
            user_text,
            history_reply or reply,
        )
        keyboard = CARD_KEYBOARD if step == order_flow.STEP_CARD else None
        return ConversationResult(reply=reply, order_draft=draft, keyboard=keyboard)

    async def _ask_step(
        self,
        user_id: str,
        step: str,
        draft: dict[str, Any],
        history: list[dict[str, str]],
        user_text: str | None,
        prefix: str = "",
    ) -> ConversationResult:
        """Повторяет вопрос текущего шага, не меняя состояние."""
        store_id = int(self.store["id"])
        reply = prefix or order_flow.question(step)
        await self._save_chat(
            store_id,
            user_id,
            step,
            draft,
            history,
            user_text,
            reply,
        )
        keyboard = CARD_KEYBOARD if step == order_flow.STEP_CARD else None
        return ConversationResult(reply=reply, order_draft=draft, keyboard=keyboard)

    async def _step_question(self, store_id: int, step: str) -> str:
        """Формулирует вопрос шага через ИИ; при ошибке — стандартный текст."""
        if not self.settings.ai_step_questions:
            return order_flow.question(step)

        hint = order_flow.STEP_HINTS.get(step, order_flow.question(step))
        prompt = build_step_prompt(self.store["name"], hint)
        try:
            completion = await self.ai.complete(
                prompt, [{"role": "user", "content": "Сформулируй вопрос клиенту."}]
            )
        except AIError:
            return order_flow.question(step)

        await self.usage.add_tokens(
            store_id,
            self._today().isoformat(),
            completion.prompt_tokens,
            completion.completion_tokens,
        )
        reply = (completion.response.reply or "").strip()
        if not reply:
            return order_flow.question(step)
        return self._truncate(
            self._escape(reply),
            self._store_limit("max_response_chars", self.settings.max_response_chars),
        )

    async def _summary_outcome(
        self,
        user_id: str,
        draft: dict[str, Any],
        history: list[dict[str, str]],
        *,
        user_text: str | None,
    ) -> ConversationResult:
        store_id = int(self.store["id"])
        # Один ключ на весь заказ: повторное подтверждение не создаёт дубль.
        draft["flow_id"] = draft.get("flow_id") or uuid.uuid4().hex
        result = await self.orders.validate(draft)

        if not result.ok:
            if any("букет" in error for error in result.errors):
                draft.pop("bouquet_id", None)
                return await self._ask_next(
                    user_id,
                    draft,
                    history,
                    user_text=user_text,
                    prefix=(
                        "Этот букет больше недоступен. "
                        "Выберите, пожалуйста, другой."
                    ),
                )
            if any("дата" in error for error in result.errors):
                draft.pop("delivery_date", None)
                return await self._ask_next(
                    user_id,
                    draft,
                    history,
                    user_text=user_text,
                    prefix=(
                        "Дата не подходит. Напишите, пожалуйста, другую — "
                        "например, «завтра» или «27 октября»."
                    ),
                )
            if result.errors:
                step = self._recovery_step_for_errors(
                    draft, " ".join(result.errors)
                )
            else:
                step = order_flow.next_step(draft)
                if step == order_flow.STEP_CONFIRM:
                    step = order_flow.STEP_OCCASION
            reply = (
                self.orders.validation_text(result)
                + "\n\n"
                + order_flow.question(step)
            )
            await self._save_chat(
                store_id,
                user_id,
                step,
                draft,
                history,
                user_text,
                reply,
            )
            return ConversationResult(reply=reply, order_draft=draft)

        summary = await self.orders.summary(draft)
        await self._save_chat(
            store_id,
            user_id,
            order_flow.STEP_CONFIRM,
            draft,
            history,
            user_text,
            summary,
        )
        return ConversationResult(
            reply=summary, order_draft=draft, keyboard=SUMMARY_KEYBOARD
        )

    async def _create_order(
        self,
        user_id: str,
        source_id: str,
        draft: dict[str, Any],
        history: list[dict[str, str]],
        *,
        user_text: str,
    ) -> ConversationResult:
        store_id = int(self.store["id"])
        try:
            order = await self.orders.create(
                draft, user_id, source_id, channel=self._channel()
            )
        except OrderValidationError as exc:
            step = self._recovery_step(draft, exc)
            reply = self.orders.validation_text(exc.result)
            if step == order_flow.STEP_BOUQUET:
                view = await self._catalog_view(store_id, draft)
                reply += "\n\n" + view.text
                await self._save_chat(
                    store_id,
                    user_id,
                    step,
                    draft,
                    history,
                    user_text,
                    CATALOG_HISTORY_REPLY,
                )
                return ConversationResult(
                    reply=reply, order_draft=draft, keyboard=view.keyboard
                )
            reply += "\n\n" + order_flow.question(step)
            await self._save_chat(
                store_id, user_id, step, draft, history, user_text, reply
            )
            return ConversationResult(reply=reply, order_draft=draft)

        reply = self.orders.accepted_text(order)
        await self._save_chat(
            store_id, user_id, "chatting", {}, history, user_text, reply
        )
        return ConversationResult(reply=reply, order=order)

    def _recovery_step(
        self,
        draft: dict[str, Any],
        exc: OrderValidationError,
    ) -> str:
        """Куда вернуть клиента, если заказ не прошёл проверку."""
        return self._recovery_step_for_errors(
            draft, " ".join(exc.result.errors)
        )

    def _recovery_step_for_errors(
        self,
        draft: dict[str, Any],
        errors: str,
    ) -> str:
        if "букет" in errors:
            draft.pop("bouquet_id", None)
            return order_flow.STEP_BOUQUET
        if "дата" in errors:
            draft.pop("delivery_date", None)
            return order_flow.STEP_DATE
        if "телефон получателя" in errors:
            draft.pop("recipient_phone", None)
            return order_flow.STEP_RECIPIENT_PHONE
        if "телефон" in errors:
            draft.pop("phone", None)
            return order_flow.STEP_CLIENT_PHONE
        if "имя получателя" in errors:
            draft.pop("recipient_name", None)
            return order_flow.STEP_RECIPIENT_NAME
        if "имя" in errors:
            draft.pop("client_name", None)
            return order_flow.STEP_CLIENT_NAME
        if "адрес" in errors:
            draft.pop("address", None)
            return order_flow.STEP_ADDRESS
        if "время" in errors:
            draft.pop("delivery_time", None)
            return order_flow.STEP_TIME
        step = order_flow.next_step(draft)
        if step == order_flow.STEP_CONFIRM:
            return order_flow.STEP_OCCASION
        return step

    async def _show_edit_menu(
        self,
        store_id: int,
        user_id: str,
        draft: dict[str, Any],
        history: list[dict[str, str]],
    ) -> ConversationResult:
        await self._save_chat(
            store_id,
            user_id,
            order_flow.STEP_CONFIRM,
            draft,
            history,
            None,
            EDIT_MENU_TEXT,
        )
        return ConversationResult(
            reply=EDIT_MENU_TEXT,
            order_draft=draft,
            keyboard=self._edit_keyboard(draft),
        )

    async def _apply_edit(
        self,
        user_id: str,
        field: str,
        draft: dict[str, Any],
        history: list[dict[str, str]],
    ) -> ConversationResult:
        if field == "is_gift":
            draft["is_gift"] = True
            draft.pop("recipient_name", None)
            draft.pop("recipient_phone", None)
            return await self._ask_next(user_id, draft, history)

        if field == "self":
            draft["is_gift"] = False
            draft.pop("recipient_name", None)
            draft.pop("recipient_phone", None)
            return await self._summary_outcome(
                user_id, draft, history, user_text=None
            )

        if field not in order_flow.EDIT_FIELD_STEPS:
            return ConversationResult(
                reply="Кнопка уже неактуальна.", order_draft=draft
            )

        if field == "card_text":
            draft.pop("card_asked", None)
            draft.pop("card_needed", None)
        if field == "comment":
            draft.pop("wishes_asked", None)
        if field in {"recipient_name", "recipient_phone"}:
            draft["is_gift"] = True
        draft.pop(field, None)
        return await self._ask_next(user_id, draft, history)

    async def _apply_field_change(
        self,
        user_id: str,
        change: tuple[str, str],
        draft: dict[str, Any],
        history: list[dict[str, str]],
        *,
        user_text: str,
    ) -> ConversationResult:
        field, value = change
        today = self._today()
        store_id = int(self.store["id"])

        if field == "bouquet_id":
            if not value:
                draft.pop("bouquet_id", None)
                return await self._ask_next(
                    user_id, draft, history, user_text=user_text
                )
            bouquets = await self.bouquets.list_available(store_id)
            bouquet = find_by_name(bouquets, value)
            if bouquet is None:
                draft.pop("bouquet_id", None)
                return await self._ask_next(
                    user_id,
                    draft,
                    history,
                    user_text=user_text,
                    prefix="Не понял, какой букет. Выберите кнопкой ниже.",
                )
            draft["bouquet_id"] = int(bouquet["id"])
            return await self._summary_outcome(
                user_id, draft, history, user_text=user_text
            )

        if field == "is_gift":
            if not value:
                draft.pop("is_gift", None)
                return await self._ask_next(
                    user_id, draft, history, user_text=user_text
                )
            is_gift = order_flow.parse_gift(value)
            if is_gift is None:
                return await self._ask_step(
                    user_id,
                    order_flow.STEP_GIFT,
                    draft,
                    history,
                    user_text,
                    prefix="Подскажите, букет для вас или в подарок?",
                )
            draft["is_gift"] = is_gift
            if not is_gift:
                draft.pop("recipient_name", None)
                draft.pop("recipient_phone", None)
            return await self._ask_next(
                user_id, draft, history, user_text=user_text
            )

        if field == "delivery_date":
            if not value:
                draft.pop("delivery_date", None)
                return await self._ask_next(
                    user_id, draft, history, user_text=user_text
                )
            parsed = parse_delivery_date(value, today)
            if parsed is None or parsed < today:
                draft.pop("delivery_date", None)
                return await self._ask_step(
                    user_id,
                    order_flow.STEP_DATE,
                    draft,
                    history,
                    user_text,
                    prefix=(
                        "Не понял дату. Напишите, пожалуйста, например, "
                        "«завтра» или «27 октября»."
                    ),
                )
            if parsed > today + timedelta(days=MAX_DELIVERY_DAYS):
                return await self._ask_step(
                    user_id,
                    order_flow.STEP_DATE,
                    draft,
                    history,
                    user_text,
                    prefix=(
                        f"Пока принимаем заказы на {MAX_DELIVERY_DAYS} дней "
                        "вперёд. Выберите дату поближе."
                    ),
                )
            draft["delivery_date"] = parsed.isoformat()
            return await self._summary_outcome(
                user_id, draft, history, user_text=user_text
            )

        if field == "delivery_time":
            if not value:
                draft.pop("delivery_time", None)
                return await self._ask_next(
                    user_id, draft, history, user_text=user_text
                )
            draft["delivery_time"] = (
                parse_delivery_time(value) or value[:100]
            )
            return await self._summary_outcome(
                user_id, draft, history, user_text=user_text
            )

        if field in {"phone", "recipient_phone"}:
            if not value:
                draft.pop(field, None)
                if field == "recipient_phone":
                    draft["is_gift"] = True
                return await self._ask_next(
                    user_id, draft, history, user_text=user_text
                )
            phone = self.orders.normalize_phone(value)
            if phone is None:
                return await self._ask_step(
                    user_id,
                    order_flow.EDIT_FIELD_STEPS[field],
                    draft,
                    history,
                    user_text,
                    prefix=(
                        "Проверьте, пожалуйста, номер: после +7 должно "
                        "быть 10 цифр. Например: +7 707 123 45 67."
                    ),
                )
            draft[field] = phone
            if field == "recipient_phone":
                draft["is_gift"] = True
            return await self._summary_outcome(
                user_id, draft, history, user_text=user_text
            )

        if field in {"client_name", "recipient_name"}:
            if not value:
                draft.pop(field, None)
                if field == "recipient_name":
                    draft["is_gift"] = True
                return await self._ask_next(
                    user_id, draft, history, user_text=user_text
                )
            name = order_flow.clean_name(value)
            if name is None:
                return await self._ask_step(
                    user_id,
                    order_flow.EDIT_FIELD_STEPS[field],
                    draft,
                    history,
                    user_text,
                    prefix="Подскажите, пожалуйста, имя.",
                )
            draft[field] = name
            if field == "recipient_name":
                draft["is_gift"] = True
            return await self._summary_outcome(
                user_id, draft, history, user_text=user_text
            )

        if field == "address":
            if not value:
                draft.pop("address", None)
                return await self._ask_next(
                    user_id, draft, history, user_text=user_text
                )
            draft["address"] = value[:500]
            return await self._summary_outcome(
                user_id, draft, history, user_text=user_text
            )

        if field == "card_text":
            if not value:
                draft.pop("card_text", None)
                draft.pop("card_asked", None)
                draft.pop("card_needed", None)
                return await self._ask_next(
                    user_id, draft, history, user_text=user_text
                )
            skip = order_flow.is_skip(value)
            draft["card_needed"] = not skip
            draft["card_text"] = "" if skip else value[:1000]
            draft["card_asked"] = True
            return await self._summary_outcome(
                user_id, draft, history, user_text=user_text
            )

        if field == "comment":
            if not value:
                draft.pop("comment", None)
                draft.pop("wishes_asked", None)
                return await self._ask_next(
                    user_id, draft, history, user_text=user_text
                )
            draft["comment"] = "" if order_flow.is_skip(value) else value[:1000]
            draft["wishes_asked"] = True
            return await self._summary_outcome(
                user_id, draft, history, user_text=user_text
            )

        return await self._ask_next(
            user_id, draft, history, user_text=user_text
        )

    async def _confirmation_question(
        self,
        user_id: str,
        text: str,
        session: dict[str, Any],
        draft: dict[str, Any],
        history: list[dict[str, str]],
    ) -> ConversationResult:
        """Отвечает на вопрос внутри подтверждения и снова показывает сводку."""
        store_id = int(self.store["id"])
        summary = await self.orders.summary(draft)

        ai_calls = await self._reserve_ai_call(
            store_id, user_id, session
        )

        bouquets = await self.bouquets.list_available(store_id)
        system_prompt = build_system_prompt(
            self.store["name"],
            self._relevant_catalog(bouquets, text, draft),
            self._draft_for_prompt(draft),
            now_text=self._now_text(),
            ready_minutes=self.settings.ready_minutes,
        )
        messages = [
            *history[-(MAX_HISTORY_MESSAGES - 1) :],
            {"role": "user", "content": text},
        ]

        try:
            completion = await self.ai.complete(system_prompt, messages)
        except AIError as exc:
            logger.warning("AI не ответил в подтверждении: %s", exc)
            reply = (
                "Сейчас не могу ответить — флорист уточнит точнее.\n\n"
                + summary
            )
            await self._save_chat(
                store_id,
                user_id,
                order_flow.STEP_CONFIRM,
                draft,
                history,
                text,
                reply,
            )
            return ConversationResult(
                reply=reply,
                order_draft=draft,
                keyboard=SUMMARY_KEYBOARD,
                ai_calls=ai_calls,
                failed=True,
            )

        await self.usage.add_tokens(
            store_id,
            self._today().isoformat(),
            completion.prompt_tokens,
            completion.completion_tokens,
        )
        self._log_ai_usage(store_id, user_id, ai_calls, completion)

        if completion.response.handoff:
            await self._notify_handoff(
                store_id=store_id,
                user_id=user_id,
                text=text,
                reason="Вопрос при подтверждении заказа",
                draft=draft,
                session=session,
            )

        answer = self._truncate(
            self._escape(completion.response.reply),
            self._store_limit(
                "max_response_chars", self.settings.max_response_chars
            ),
        )
        reply = f"{answer}\n\n{summary}"
        await self._save_chat(
            store_id,
            user_id,
            order_flow.STEP_CONFIRM,
            draft,
            history,
            text,
            reply,
        )
        return ConversationResult(
            reply=reply,
            order_draft=draft,
            keyboard=SUMMARY_KEYBOARD,
            ai_calls=ai_calls,
            handoff=completion.response.handoff,
        )

    async def _model_reply(
        self,
        user_id: str,
        text: str,
        today: str,
        session: dict[str, Any],
        draft: dict[str, Any],
        history: list[dict[str, str]],
    ) -> ConversationResult:
        store_id = int(self.store["id"])
        ai_calls = await self._reserve_ai_call(store_id, user_id, session)

        bouquets = await self.bouquets.list_available(store_id)
        system_prompt = build_system_prompt(
            self.store["name"],
            self._relevant_catalog(bouquets, text, draft),
            self._draft_for_prompt(draft),
            now_text=self._now_text(),
            ready_minutes=self.settings.ready_minutes,
        )
        messages = [
            *history[-(MAX_HISTORY_MESSAGES - 1) :],
            {"role": "user", "content": text},
        ]

        try:
            completion = await self.ai.complete(system_prompt, messages)
        except AIError as exc:
            logger.warning("AI недоступен: %s", exc)
            saved_history = self._trim_history(
                [*messages, {"role": "assistant", "content": HANDOFF_TEXT}]
            )
            await self.sessions.save_conversation(
                store_id,
                user_id,
                "handoff",
                draft,
                saved_history,
                channel=self._channel(),
            )
            await self._notify_handoff(
                store_id=store_id,
                user_id=user_id,
                text=text,
                reason="Модель недоступна",
                draft=draft,
                session=session,
            )
            return ConversationResult(
                reply=HANDOFF_TEXT,
                handoff=True,
                failed=True,
                order_draft=draft,
                ai_calls=ai_calls,
            )

        await self.usage.add_tokens(
            store_id,
            today,
            completion.prompt_tokens,
            completion.completion_tokens,
        )
        self._log_ai_usage(store_id, user_id, ai_calls, completion)

        self._merge_model_fields(draft, completion, bouquets)

        if completion.response.handoff:
            await self._notify_handoff(
                store_id=store_id,
                user_id=user_id,
                text=text,
                reason="Модель передала диалог менеджеру",
                draft=draft,
                session=session,
            )

        if completion.response.ready and await self.orders.is_ready(draft):
            return await self._summary_outcome(
                user_id, draft, history, user_text=text
            )

        reply = self._truncate(
            self._escape(completion.response.reply),
            self._store_limit(
                "max_response_chars", self.settings.max_response_chars
            ),
        )
        state = (
            "handoff" if completion.response.handoff else "chatting"
        )
        saved_history = self._trim_history(
            [
                *messages,
                {"role": "assistant", "content": completion.response.reply},
            ]
        )
        await self.sessions.save_conversation(
            store_id,
            user_id,
            state,
            draft,
            saved_history,
            channel=self._channel(),
        )
        return ConversationResult(
            reply=reply,
            handoff=completion.response.handoff,
            order_draft=draft,
            ai_calls=ai_calls,
        )

    async def _handoff(
        self,
        store_id: int,
        user_id: str,
        text: str,
        draft: dict[str, Any],
        history: list[dict[str, str]],
        reason: str,
    ) -> ConversationResult:
        saved_history = self._trim_history(
            [
                *history,
                {"role": "user", "content": text},
                {"role": "assistant", "content": HANDOFF_TEXT},
            ]
        )
        await self.sessions.save_conversation(
            store_id,
            user_id,
            "handoff",
            draft,
            saved_history,
            channel=self._channel(),
        )
        session = await self.sessions.ensure(
            store_id, user_id, channel=self._channel()
        )
        await self._notify_handoff(
            store_id=store_id,
            user_id=user_id,
            text=text,
            reason=reason,
            draft=draft,
            session=session,
        )
        return ConversationResult(
            reply=HANDOFF_TEXT, handoff=True, order_draft=draft
        )

    async def _notify_handoff(
        self,
        *,
        store_id: int,
        user_id: str,
        text: str,
        reason: str,
        draft: dict[str, Any],
        session: dict[str, Any],
    ) -> bool:
        """Фиксирует обращение в панели и шлёт уведомление флористу.

        Запись в handoffs создаётся всегда, чтобы флорист видел обращение
        в панели. Кулдаун ограничивает только Telegram-уведомление.
        """
        await self.handoffs.create(
            store_id=store_id,
            client_chat_id=user_id,
            client_name=draft.get("client_name") or "",
            reason=reason,
            last_message=self._truncate(text, 500),
            channel=self._channel(),
        )

        if self._handoff_active(session):
            return True

        florist_chat_id = (
            self.store.get("florist_chat_id") or self.settings.florist_chat_id
        )
        if florist_chat_id is None:
            logger.error("Не задан chat_id флориста для магазина %s", store_id)
        else:
            try:
                await self.outbox.enqueue_handoff(
                    store_id,
                    int(florist_chat_id),
                    {
                        "client_chat_id": self._client_id_for_payload(user_id),
                        "client_name": draft.get("client_name") or "",
                        "reason": reason,
                        "last_message": self._truncate(text, 500),
                    },
                )
            except Exception:
                logger.exception("Не удалось передать заявку флористу")
                return False

            logger.info(
                "Передача человеку: магазин=%s, chat=%s, причина=%s",
                store_id,
                user_id,
                reason,
            )

        until = (
            datetime.now(timezone.utc)
            + timedelta(minutes=self.settings.handoff_cooldown_minutes)
        ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        await self.sessions.set_handoff_until(
            store_id, user_id, until, channel=self._channel()
        )
        return True

    async def _catalog_view(self, store_id: int, draft: dict[str, Any]):
        bouquets = await self.bouquets.list_available(store_id)
        view = render_list(
            bouquets,
            budget=self._as_int(draft.get("catalog_budget")),
            occasion=draft.get("catalog_occasion") or None,
            flower=draft.get("catalog_flower") or None,
            page=self._as_int(draft.get("catalog_page"), default=0),
            page_size=self.settings.catalog_page_size,
        )
        draft["catalog_ids"] = list(view.bouquet_ids)
        return view

    @staticmethod
    def _set_catalog_filters(
        draft: dict[str, Any],
        budget: int | None,
        occasion: str | None,
        flower: str | None,
    ) -> None:
        draft["catalog_budget"] = budget
        draft["catalog_occasion"] = occasion
        draft["catalog_flower"] = flower
        draft["catalog_page"] = 0

    async def _available_bouquet(
        self, store_id: int, raw_id: str
    ) -> dict[str, Any] | None:
        bouquet_id = self._as_int(raw_id)
        if bouquet_id is None:
            return None
        return await self.bouquets.get_available(store_id, bouquet_id)

    def _merge_model_fields(
        self,
        draft: dict[str, Any],
        completion: Any,
        bouquets: list[dict[str, Any]],
    ) -> None:
        """Переносит в черновик только поля, проверенные кодом."""
        order = completion.response.order
        if order is None:
            return
        fields = order.cleaned()

        name = order_flow.clean_name(fields.get("client_name", ""))
        if name:
            draft["client_name"] = name

        phone = self.orders.normalize_phone(fields.get("phone"))
        if phone:
            draft["phone"] = phone

        address = (fields.get("address") or "").strip()
        if address:
            draft["address"] = address[:500]

        if "is_gift" in fields:
            draft["is_gift"] = bool(fields["is_gift"])
            if not draft["is_gift"]:
                draft.pop("recipient_name", None)
                draft.pop("recipient_phone", None)

        recipient_name = order_flow.clean_name(
            fields.get("recipient_name", "")
        )
        if recipient_name:
            draft["recipient_name"] = recipient_name
            draft.setdefault("is_gift", True)

        recipient_phone = self.orders.normalize_phone(
            fields.get("recipient_phone")
        )
        if recipient_phone:
            draft["recipient_phone"] = recipient_phone
            draft.setdefault("is_gift", True)

        delivery_raw = (fields.get("delivery_date") or "").strip()
        if delivery_raw:
            parsed = parse_delivery_date(delivery_raw, self._today())
            if (
                parsed is not None
                and self._today() <= parsed
                <= self._today() + timedelta(days=MAX_DELIVERY_DAYS)
            ):
                draft["delivery_date"] = parsed.isoformat()

        delivery_time = (fields.get("delivery_time") or "").strip()
        if delivery_time:
            draft["delivery_time"] = (
                parse_delivery_time(delivery_time) or delivery_time[:100]
            )

        bouquet_id = fields.get("bouquet_id")
        if bouquet_id is not None:
            available = {int(item["id"]) for item in bouquets}
            if int(bouquet_id) in available:
                draft["bouquet_id"] = int(bouquet_id)
            else:
                logger.warning("Модель предложила недоступный букет: %s", bouquet_id)

        card_text = (fields.get("card_text") or "").strip()
        if card_text:
            draft["card_needed"] = True
            draft["card_text"] = card_text[:1000]
            draft["card_asked"] = True

        comment = (fields.get("comment") or "").strip()
        if comment:
            draft["comment"] = comment[:1000]
            draft["wishes_asked"] = True

        occasion = (fields.get("occasion") or "").strip()
        if occasion:
            draft["occasion"] = occasion[:100]

        budget = fields.get("budget_kzt")
        if budget is not None:
            draft["budget_kzt"] = int(budget)
            draft["catalog_budget"] = int(budget)

    @staticmethod
    def _draft_for_prompt(draft: dict[str, Any]) -> dict[str, Any]:
        result = {}
        for key in ORDER_DRAFT_KEYS:
            value = draft.get(key)
            if value in (None, ""):
                continue
            # Модель не должна увидеть ISO-дату и повторить её клиенту.
            if key == "delivery_date":
                value = format_date_ru(value)
            result[key] = value
        return result

    @staticmethod
    def _relevant_catalog(
        bouquets: list[dict[str, Any]],
        text: str,
        draft: dict[str, Any],
    ) -> str:
        """Подборка для промпта: запрос клиента и выбранный букет."""
        relevant = select_for_model(bouquets, text)
        chosen_id = draft.get("bouquet_id")
        if chosen_id is not None:
            chosen = next(
                (
                    item
                    for item in bouquets
                    if int(item["id"]) == int(chosen_id)
                ),
                None,
            )
            if chosen is not None and all(
                int(item["id"]) != int(chosen_id) for item in relevant
            ):
                relevant = [chosen, *relevant][:5]
        return catalog_for_model(relevant)

    @staticmethod
    def _edit_keyboard(draft: dict[str, Any]) -> Keyboard:
        rows: list[tuple[tuple[str, str], ...]] = [
            (
                ("Имя заказчика", "edit:client_name"),
                ("Телефон заказчика", "edit:phone"),
            ),
            (("Букет", "edit:bouquet_id"), ("Адрес", "edit:address")),
            (
                ("Дата", "edit:delivery_date"),
                ("Время", "edit:delivery_time"),
            ),
            (("Открытка", "edit:card_text"), ("Пожелания", "edit:comment")),
        ]
        if draft.get("is_gift"):
            rows.insert(
                1,
                (
                    ("Имя получателя", "edit:recipient_name"),
                    ("Телефон получателя", "edit:recipient_phone"),
                ),
            )
            rows.append((("Это для меня", "edit:self"),))
        else:
            rows.append((("Сделать подарком", "edit:is_gift"),))
        rows.append((("Назад к заказу", "back:confirm"),))
        return tuple(rows)

    async def _save_chat(
        self,
        store_id: int,
        user_id: str,
        state: str,
        draft: dict[str, Any],
        history: list[dict[str, str]],
        user_text: str | None,
        history_reply: str | None,
    ) -> None:
        if user_text is not None and history_reply is not None:
            history = self._trim_history(
                [
                    *history,
                    {"role": "user", "content": user_text},
                    {"role": "assistant", "content": history_reply},
                ]
            )
        await self._save(store_id, user_id, state, draft, history)

    async def _save(
        self,
        store_id: int,
        user_id: str,
        state: str,
        draft: dict[str, Any],
        history: list[dict[str, str]],
    ) -> None:
        await self.sessions.save_conversation(
            store_id, user_id, state, draft, history, channel=self._channel()
        )

    async def _reserve_ai_call(
        self,
        store_id: int,
        user_id: str,
        session: dict[str, Any],
    ) -> int:
        """Считает вызовы модели для статистики, без ограничения."""
        await self.sessions.add_ai_call(
            store_id,
            user_id,
            self._today().isoformat(),
            channel=self._channel(),
        )
        return int(session["ai_calls"]) + 1

    def _log_ai_usage(
        self, store_id: int, user_id: str, ai_calls: int, completion: Any
    ) -> None:
        logger.info(
            "AI вызов: магазин=%s, chat=%s, вызов=%d, токены=%d/%d, попыток=%d",
            store_id,
            user_id,
            ai_calls,
            completion.prompt_tokens,
            completion.completion_tokens,
            completion.attempts,
        )

    def _store_limit(self, name: str, default: int) -> int:
        value = self.store.get(name)
        return int(value) if value else int(default)

    def _now_text(self) -> str:
        now = datetime.now(ZoneInfo(self.settings.timezone))
        return format_now_ru(now, self.settings.timezone)

    def _today(self):
        from datetime import date

        return datetime.now(ZoneInfo(self.settings.timezone)).date()

    @staticmethod
    def _is_order_intent(text: str) -> bool:
        raw = normalize_text(text)
        return any(word in raw for word in ORDER_INTENT_WORDS)

    @staticmethod
    def _is_catalog_intent(text: str) -> bool:
        raw = normalize_text(text)
        if "сколько стоит" in raw and not (
            "букет" in raw or extract_flower(raw)
        ):
            return False
        return any(word in raw for word in CATALOG_INTENT_WORDS)

    @staticmethod
    def _is_handoff_request(text: str) -> bool:
        raw = normalize_text(text)
        if any(question in raw for question in BOT_QUESTIONS):
            return False
        return any(word in raw for word in HANDOFF_WORDS)

    @staticmethod
    def _is_angry(text: str) -> bool:
        raw = normalize_text(text)
        return any(word in raw for word in ANGRY_WORDS)

    def _is_free_question(self, state: str, text: str) -> bool:
        """Вопрос, который не является ответом текущего шага."""
        if not self._looks_like_question(text):
            return False

        today = self._today()
        if state == order_flow.STEP_OCCASION:
            return not (
                parse_budget(text)
                or parse_occasion(text)
                or extract_flower(text)
            )
        if state == order_flow.STEP_BOUQUET:
            return ordinal_index(text) is None
        if state == order_flow.STEP_DATE:
            return parse_delivery_date(text, today) is None
        if state == order_flow.STEP_TIME:
            return (
                parse_delivery_time(text) is None
                and parse_delivery_date(text, today) is None
            )
        if state in (
            order_flow.STEP_CLIENT_PHONE,
            order_flow.STEP_RECIPIENT_PHONE,
        ):
            return self.orders.normalize_phone(text) is None
        if state == order_flow.STEP_GIFT:
            return order_flow.parse_gift(text) is None
        if state in (
            order_flow.STEP_CLIENT_NAME,
            order_flow.STEP_RECIPIENT_NAME,
            order_flow.STEP_ADDRESS,
        ):
            return True
        return False

    @staticmethod
    def _looks_like_question(text: str) -> bool:
        raw = normalize_text(text)
        raw = re.sub(r"^а\s+", "", raw)
        return raw.startswith(
            (
                "сколько",
                "как",
                "когда",
                "на когда",
                "можно",
                "что",
                "где",
                "почему",
                "зачем",
                "какой",
                "какая",
                "какие",
                "каких",
                "есть ли",
                "подскажи",
                "расскажи",
            )
        )

    @staticmethod
    def _is_ready_question(text: str) -> bool:
        raw = normalize_text(text)
        return (
            "когда" in raw or "через сколько" in raw
        ) and any(
            word in raw
            for word in ("готов", "будет", "забрать", "достав", "срок")
        )

    @staticmethod
    def _small_talk(text: str) -> tuple[str, Keyboard] | None:
        raw = order_flow.normalize_reply(text)
        catalog_keyboard: Keyboard = (
            (("Подобрать букет", "catalog:0"),),
        )
        if raw in GREETINGS:
            return START_TEXT, catalog_keyboard
        if raw in THANKS:
            return THANKS_TEXT, ()
        if any(question in raw for question in BOT_QUESTIONS):
            return BOT_HONEST_TEXT, ()
        if any(question in raw for question in CAPABILITY_QUESTIONS):
            return CAPABILITIES_TEXT, catalog_keyboard
        return None

    @staticmethod
    def _recipient_name(
        draft: dict[str, Any], text: str
    ) -> str | None:
        raw = order_flow.normalize_reply(text)
        if raw in {"как меня", "меня", "то же", "тот же", "как заказчик"}:
            return draft.get("client_name")
        return order_flow.clean_name(text)

    def _recipient_phone(
        self, draft: dict[str, Any], text: str
    ) -> str | None:
        raw = order_flow.normalize_reply(text)
        if raw in {"тот же", "мой", "как у меня", "как меня", "такой же"}:
            return draft.get("phone")
        return self.orders.normalize_phone(text)

    @staticmethod
    def _load_draft(session: dict[str, Any]) -> dict[str, Any]:
        try:
            value = json.loads(session["draft_json"])
        except (TypeError, ValueError, KeyError):
            return {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _load_history(session: dict[str, Any]) -> list[dict[str, str]]:
        try:
            value = json.loads(session["history_json"])
        except (TypeError, ValueError, KeyError):
            return []
        if not isinstance(value, list):
            return []

        history = []
        for message in value:
            if (
                isinstance(message, dict)
                and message.get("role") in {"user", "assistant"}
                and isinstance(message.get("content"), str)
            ):
                history.append(
                    {"role": message["role"], "content": message["content"]}
                )
        return history[-MAX_HISTORY_MESSAGES:]

    @staticmethod
    def _trim_history(
        history: list[dict[str, str]]
    ) -> list[dict[str, str]]:
        return history[-MAX_HISTORY_MESSAGES:]

    @staticmethod
    def _handoff_active(session: dict[str, Any]) -> bool:
        value = session.get("handoff_until")
        if not value:
            return False
        try:
            until = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return False
        if until.tzinfo is None:
            until = until.replace(tzinfo=timezone.utc)
        return until > datetime.now(timezone.utc)

    @staticmethod
    def _escape(text: str) -> str:
        return html.escape(text or "")

    @staticmethod
    def _as_int(value: Any, default: int | None = None) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _truncate(text: str, limit: int) -> str:
        limit = max(1, int(limit))
        text = text.strip()
        if len(text) <= limit:
            return text

        cut = text[:limit]
        best = -1
        for separator in (". ", "! ", "? ", "\n"):
            position = cut.rfind(separator)
            if position > best:
                best = position

        if best >= limit // 2:
            return cut[: best + 1].strip()
        return cut.rstrip() + "…"
