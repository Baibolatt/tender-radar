"""Разовый скрипт: показывает сырой ответ DeepSeek без разбора JSON."""

import json
import sqlite3
import sys

import httpx

from ai.prompts import build_system_prompt
from config import Settings

CHAT_ID = 8787687661
FAILED_TEXT = "Может красиво напишешь отдельно по строкам"


def catalog_text(conn: sqlite3.Connection, store_id: int) -> str:
    """Собирает каталог так же, как DialogService."""
    rows = conn.execute(
        """
        SELECT id, name, price_kzt, description
        FROM bouquets
        WHERE store_id = ? AND is_available = 1
        ORDER BY sort_order, id
        """,
        (store_id,),
    ).fetchall()
    lines = []
    for row in rows:
        price = f"{int(row['price_kzt']):,}".replace(",", " ")
        line = f"{row['id']}. «{row['name']}» — {price} тг"
        if row["description"]:
            line += f" — {row['description']}"
        lines.append(line)
    return "\n".join(lines)


def history_before_failure(conn: sqlite3.Connection, store_id: int, chat_id: int):
    """Берёт историю диалога до проблемного сообщения."""
    row = conn.execute(
        "SELECT history_json FROM dialog_sessions WHERE store_id=? AND chat_id=?",
        (store_id, chat_id),
    ).fetchone()
    if row is None:
        raise SystemExit("Сессия не найдена")

    history = json.loads(row["history_json"])
    # Убираем последний ответ «передаю менеджеру» и последнее сообщение клиента.
    if history and history[-1]["content"].startswith("Передаю вас менеджеру"):
        history.pop()
    if history and history[-1]["role"] == "user":
        history.pop()
    return history


def main() -> None:
    settings = Settings()
    key = settings.ai_api_key.get_secret_value()
    print("ключ: длина", len(key), "| пробелы по краям:", key != key.strip())
    print("модель:", settings.ai_model)
    print("base_url:", settings.ai_base_url)

    conn = sqlite3.connect("data/bot.db")
    conn.row_factory = sqlite3.Row
    store = conn.execute(
        "SELECT id, name FROM stores WHERE code = ?",
        (settings.store_code,),
    ).fetchone()
    if store is None:
        raise SystemExit("Магазин не найден")

    system_prompt = build_system_prompt(
        store["name"],
        catalog_text(conn, store["id"]),
        {},
    )
    messages = [
        {"role": "system", "content": system_prompt},
        *history_before_failure(conn, store["id"], CHAT_ID),
        {"role": "user", "content": FAILED_TEXT},
    ]

    payload = {
        "model": settings.ai_model,
        "messages": messages,
        "max_tokens": settings.ai_max_tokens,
        "temperature": 0.3,
        "response_format": {"type": "json_object"},
    }

    response = httpx.post(
        f"{settings.ai_base_url.rstrip('/')}/chat/completions",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=settings.ai_timeout_seconds,
    )
    print("\nHTTP:", response.status_code)
    data = response.json()
    print("usage:", data.get("usage"))
    choice = (data.get("choices") or [{}])[0]
    print("finish_reason:", choice.get("finish_reason"))
    content = (choice.get("message") or {}).get("content")
    print("content type:", type(content).__name__)
    print("RAW CONTENT:")
    print(repr(content))

    if content:
        try:
            json.loads(content)
            print("\njson.loads: OK")
        except json.JSONDecodeError as exc:
            print("\njson.loads: ОШИБКА:", exc.msg,
                  f"(строка {exc.lineno}, колонка {exc.colno})")
            print("позиция ошибки:", repr(content[max(0, exc.pos - 80):exc.pos + 80]))


if __name__ == "__main__":
    sys.exit(main())
