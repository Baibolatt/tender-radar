# WhatsApp Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add WhatsApp Cloud API as a second channel alongside the existing Telegram bot, sharing all business logic (dialog handling, order creation, notifications) via a common service layer.

**Architecture:** Extract the core dialog processing logic from `DialogService` into a channel-agnostic `ConversationService` that both Telegram and WhatsApp handlers call. Add a Flask-based webhook endpoint for WhatsApp that validates the verify token, parses incoming messages, calls the shared service, and sends replies via WhatsApp Graph API.

**Tech Stack:** Python 3.12, aiogram 3 (Telegram), Flask (WhatsApp webhook), aiohttp/httpx (WhatsApp Graph API calls), SQLite (shared DB), Google Gemini API (AI responses).

**Spec:** User's pasted requirements in the conversation.

## Global Constraints

- Всегда отвечай на русском: объяснения, планы, вопросы. Код, имена переменных и коммиты — на английском, комментарии в коде — по-русски.
- Не клади API-ключи и токены в файлы, только в переменные окружения.
- Следи за расходом токенов API, не делай лишних вызовов модели.
- Telegram-бот на aiogram 3 + SQLite — архитектуру не меняем.
- Реальную модель вызывать только по команде «проверим вживую». В тестах — только фейковая модель.
- Код должен быть готов к продакшену: обработка ошибок, логирование, не ломаться при пустом payload.

## Review Focus

1. **Empty/malformed WhatsApp payload** — must not crash, log and return 200
2. **Non-text messages (delivery receipts, reactions, etc.)** — must be ignored silently
3. **WhatsApp API errors** — must be logged, not crash the webhook; message retried via outbox if needed
4. **Concurrent messages from same WhatsApp user** — must be serialized per chat (like Telegram's `_lock_for`)
5. **Verify token mismatch** — must return 403, not leak token in logs

---

### Task 1: Add WhatsApp config to Settings and .env.example

**Files:**
- Modify: `config.py` (add WhatsApp fields)
- Modify: `.env.example` (add WhatsApp variables)

**Interfaces:**
- Produces: `settings.whatsapp_token`, `settings.whatsapp_phone_number_id`, `settings.webhook_verify_token`

- [ ] **Step 1: Add WhatsApp fields to Settings class in config.py**

```python
# В класс Settings добавить:
whatsapp_token: SecretStr = Field(default=SecretStr(""), alias="WHATSAPP_TOKEN")
whatsapp_phone_number_id: str = Field(default="", alias="WHATSAPP_PHONE_NUMBER_ID")
webhook_verify_token: SecretStr = Field(default=SecretStr(""), alias="WEBHOOK_VERIFY_TOKEN")
```

- [ ] **Step 2: Add variables to .env.example**

```bash
# WhatsApp Cloud API
WHATSAPP_TOKEN=
WHATSAPP_PHONE_NUMBER_ID=
WEBHOOK_VERIFY_TOKEN=
```

- [ ] **Step 3: Verify config loads correctly**

Run: `.venv/bin/python -c "from config import get_settings; s=get_settings(); print(s.whatsapp_phone_number_id)"`
Expected: Empty string (or value from .env if set)

- [ ] **Step 4: Commit**

```bash
git add config.py .env.example
git commit -m "config: add WhatsApp Cloud API settings"
```

---

### Task 2: Create shared ConversationService (extract from DialogService)

**Files:**
- Create: `bot/services/conversation_service.py`
- Modify: `bot/services/dialog_service.py` (delegate to ConversationService)
- Modify: `bot/services/__init__.py` (export ConversationService)

**Interfaces:**
- Consumes: `Database`, `Settings`, `AIClient`, `store` dict, `OrderService`
- Produces: `ConversationService.process_message(chat_id: int, text: str, channel: str, source_id: str) -> ConversationResult`
- Produces: `ConversationService.process_callback(chat_id: int, data: str, channel: str, source_id: str) -> ConversationResult`
- `ConversationResult`: reply text, handoff flag, order_draft, order object, keyboard, photo_file_id

- [ ] **Step 1: Create ConversationService class in conversation_service.py**

Move all channel-agnostic logic from DialogService:
- `_reply` logic (state machine)
- `_handle_chatting`, `_handle_order_text`, `_handle_confirmation_text`
- `_ask_next`, `_ask_step`, `_step_question`
- `_summary_outcome`, `_create_order`
- `_handoff`, `_notify_handoff`
- `_model_reply`, `_flow_question`, `_confirmation_question`
- `_merge_model_fields`, `_draft_for_prompt`, `_relevant_catalog`
- Order validation, summary, creation via OrderService
- Session management (draft, history, state)
- AI call counting, logging

Replace `chat_id: int` with `user_id: str` (channel-agnostic identifier)
Add `channel: str` parameter ("telegram" | "whatsapp")
Add `source_id: str` for idempotency (Telegram update_id, WhatsApp message_id)

- [ ] **Step 2: Refactor DialogService to use ConversationService**

DialogService.reply() → ConversationService.process_message(chat_id, text, "telegram", str(update_id))
DialogService.handle_callback() → ConversationService.process_callback(chat_id, data, "telegram", str(update_id))

- [ ] **Step 3: Run existing tests**

Run: `.venv/bin/python -m pytest tests/test_dialog_service.py -v`
Expected: All 141 tests pass

- [ ] **Step 4: Commit**

```bash
git add bot/services/conversation_service.py bot/services/dialog_service.py bot/services/__init__.py
git commit -m "refactor: extract ConversationService for multi-channel support"
```

---

### Task 3: Create WhatsApp client for Graph API

**Files:**
- Create: `bot/services/whatsapp_client.py`
- Modify: `bot/services/__init__.py` (export WhatsAppClient)

**Interfaces:**
- Consumes: `Settings` (token, phone_number_id)
- Produces: `WhatsAppClient.send_text(to: str, text: str) -> dict`
- Produces: `WhatsAppClient.send_interactive(to: str, text: str, buttons: list[dict]) -> dict`

- [ ] **Step 1: Write failing test for WhatsAppClient**

```python
# tests/test_whatsapp_client.py
async def test_send_text_makes_correct_request():
    # mock httpx post, verify URL, headers, JSON body
```

Run: `.venv/bin/python -m pytest tests/test_whatsapp_client.py::test_send_text_makes_correct_request -v`
Expected: FAIL (module not found)

- [ ] **Step 2: Implement WhatsAppClient**

```python
class WhatsAppClient:
    def __init__(self, settings: Settings):
        self.token = settings.whatsapp_token.get_secret_value()
        self.phone_number_id = settings.whatsapp_phone_number_id
        self.base_url = f"https://graph.facebook.com/v21.0/{self.phone_number_id}/messages"
    
    async def send_text(self, to: str, text: str) -> dict:
        # POST to Graph API with Bearer token
        # Return response JSON
    
    async def send_interactive(self, to: str, text: str, buttons: list[dict]) -> dict:
        # For button replies (catalog, yes/no, etc.)
```

Handle: rate limits (429), auth errors (401), network errors, truncate text to 4096 chars (WhatsApp limit)

- [ ] **Step 3: Run test**

Run: `.venv/bin/python -m pytest tests/test_whatsapp_client.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add bot/services/whatsapp_client.py bot/services/__init__.py tests/test_whatsapp_client.py
git commit -m "feat: add WhatsApp Graph API client"
```

---

### Task 4: Create Flask webhook endpoint for WhatsApp

**Files:**
- Create: `bot/whatsapp_webhook.py`
- Create: `tests/test_whatsapp_webhook.py`
- Modify: `requirements.txt` (add flask)

**Interfaces:**
- Consumes: `ConversationService`, `WhatsAppClient`, `Settings`
- Produces: Flask app with routes:
  - `GET /webhook` — verify token, return challenge
  - `POST /webhook` — process incoming message, send reply

- [ ] **Step 1: Add flask to requirements.txt**

```bash
flask==3.1.0
```

Run: `.venv/bin/pip install flask`

- [ ] **Step 2: Write failing test for webhook verification**

```python
# tests/test_whatsapp_webhook.py
def test_get_webhook_valid_token_returns_challenge():
    client = app.test_client()
    response = client.get('/webhook?hub.mode=subscribe&hub.verify_token=TEST_TOKEN&hub.challenge=12345')
    assert response.status_code == 200
    assert response.data == b'12345'
```

Run: `.venv/bin/python -m pytest tests/test_whatsapp_webhook.py::test_get_webhook_valid_token_returns_challenge -v`
Expected: FAIL

- [ ] **Step 3: Implement GET /webhook**

```python
@app.route('/webhook', methods=['GET'])
def verify_webhook():
    verify_token = request.args.get('hub.verify_token')
    challenge = request.args.get('hub.challenge')
    if verify_token == settings.webhook_verify_token.get_secret_value():
        return challenge, 200
    return 'Forbidden', 403
```

- [ ] **Step 4: Write failing test for POST /webhook with text message**

```python
async def test_post_webhook_text_message_calls_service_and_sends_reply():
    # Mock ConversationService.process_message and WhatsAppClient.send_text
    # Verify they're called with correct args
```

- [ ] **Step 5: Implement POST /webhook**

Parse payload:
```json
{
  "object": "whatsapp_business_account",
  "entry": [{
    "changes": [{
      "value": {
        "messages": [{
          "from": "7XXXXXXXXXX",
          "id": "wamid.xxx",
          "text": {"body": "Привет"},
          "timestamp": "1234567890"
        }]
      }
    }]
  }]
}
```

Extract: `from` (sender phone), `id` (message_id), `text.body`
Ignore: non-text messages (delivery, read, reaction), empty payload
Call: `conversation_service.process_message(user_id=phone, text=body, channel="whatsapp", source_id=message_id)`
Send reply via WhatsAppClient

- [ ] **Step 6: Run tests**

Run: `.venv/bin/python -m pytest tests/test_whatsapp_webhook.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add bot/whatsapp_webhook.py tests/test_whatsapp_webhook.py requirements.txt
git commit -m "feat: add WhatsApp webhook endpoint with verification"
```

---

### Task 5: Add WhatsApp entry point (run_web.py)

**Files:**
- Create: `bot/run_web.py` (or add to web app)
- Modify: `README.md` (add instructions)

**Interfaces:**
- Consumes: Flask app from whatsapp_webhook.py, Settings, Database
- Produces: Runnable script to start webhook server

- [ ] **Step 1: Create run_web.py**

```python
"""Запуск WhatsApp webhook сервера: python -m bot.run_web"""
import asyncio
from aiohttp import web
from config import get_settings
from db.database import Database
from bot.whatsapp_webhook import create_whatsapp_app

async def main():
    settings = get_settings()
    db = Database(settings.database_path)
    await db.connect()
    try:
        await db.migrate()
        app = create_whatsapp_app(settings, db)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, host="0.0.0.0", port=8080)
        await site.start()
        await asyncio.Event().wait()
    finally:
        await db.close()

if __name__ == "__main__":
    asyncio.run(main())
```

Note: Use aiohttp instead of Flask for consistency, or keep Flask with gunicorn. Let's use aiohttp for async consistency.

Actually, re-reading requirements — they asked for Flask. Let's use Flask with a simple run script.

- [ ] **Step 2: Update README with Codespaces instructions**

```
## WhatsApp Webhook Setup

### В Codespaces:
1. Запустите вебхук: `.venv/bin/python -m bot.run_web`
2. В портах Codespaces найдите порт 8080, сделайте его Public
3. Скопируйте URL (например: https://xxx-8080.app.github.dev)
4. В Meta Developer Console → WhatsApp → Configuration → Webhook:
   - Callback URL: `https://xxx-8080.app.github.dev/webhook`
   - Verify Token: значение из `WEBHOOK_VERIFY_TOKEN` в .env
   - Subscribe to: `messages`
```

- [ ] **Step 3: Verify run_web.py works**

Run: `.venv/bin/python -m bot.run_web` (Ctrl+C to stop)
Expected: Server starts on port 8080

- [ ] **Step 4: Commit**

```bash
git add bot/run_web.py README.md
git commit -m "feat: add WhatsApp webhook runner and docs"
```

---

### Task 6: Integration test — full WhatsApp flow

**Files:**
- Create: `tests/test_whatsapp_integration.py`

**Interfaces:**
- Consumes: All above components
- Tests: Full message flow from webhook → ConversationService → WhatsAppClient

- [ ] **Step 1: Write integration test**

Test scenarios:
1. Incoming text → greeting reply
2. Incoming "заказать" → catalog → bouquet selection → full order flow
3. Handoff request → handoff logged, notification sent
4. Non-text message (delivery receipt) → ignored
5. Invalid verify token → 403
6. Empty payload → 200 OK

- [ ] **Step 2: Run integration tests**

Run: `.venv/bin/python -m pytest tests/test_whatsapp_integration.py -v`
Expected: PASS

- [ ] **Step 3: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: All 141+ new tests pass

- [ ] **Step 4: Commit**

```bash
git add tests/test_whatsapp_integration.py
git commit -m "test: add WhatsApp integration tests"
```

---

### Task 7: Add phone number normalization for WhatsApp

**Files:**
- Modify: `bot/services/order_service.py` (normalize_phone already handles +7 format)
- Modify: `bot/services/conversation_service.py` (store phone in canonical format)

**Note:** WhatsApp sends numbers in international format (e.g., "7XXXXXXXXXX" or "+7XXXXXXXXXX"). The existing `normalize_phone` should handle this. Just ensure the WhatsApp sender ID is normalized before storing.

- [ ] **Step 1: Verify normalize_phone handles WhatsApp format**

Test: `normalize_phone("77001234567")` → `+77001234567`
Test: `normalize_phone("+77001234567")` → `+77001234567`

- [ ] **Step 2: Apply normalization in ConversationService when receiving WhatsApp message**

- [ ] **Step 3: Run tests**

Run: `.venv/bin/python -m pytest tests/test_order_service.py::test_normalize_phone -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git commit -am "fix: normalize WhatsApp phone numbers to canonical format"
```

---

### Task 8: Final verification and cleanup

- [ ] Run all tests: `.venv/bin/python -m pytest tests/ -v`
- [ ] Test manual flow in Codespaces (if tokens available)
- [ ] Update memory files if needed
- [ ] Final commit

```bash
git add -A
git commit -m "feat: WhatsApp Cloud API integration complete"
```