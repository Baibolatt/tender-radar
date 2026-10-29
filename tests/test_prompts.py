"""Проверка системного промпта: факты не выдумываются, handoff узкий."""

from ai.prompts import build_system_prompt


def test_prompt_keeps_grounding_rules():
    prompt = build_system_prompt(
        "Магазин",
        "«Розы» — 5000 ₸",
        draft={"client_name": "Айгуль"},
        now_text="29 октября",
        ready_minutes=60,
    )

    assert "Магазин" in prompt
    assert "«Розы» — 5000 ₸" in prompt
    assert "не придумывай" in prompt
    assert "подтвердит флорист" in prompt
    assert "передай флористу" in prompt.lower()
    assert "2–4 предложения" in prompt


def test_prompt_narrows_handoff():
    prompt = build_system_prompt("Магазин", "")
    assert "ТОЛЬКО если" in prompt
    assert "продолжай помогать сам" in prompt
