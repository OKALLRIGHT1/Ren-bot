from types import SimpleNamespace

import pytest

from services.chat_support.active_alert_service import ActiveAlertService


def _service(presenter=None) -> ActiveAlertService:
    return ActiveAlertService(
        default_persona="用角色口吻说话。",
        event_bus=SimpleNamespace(),
        presenter=presenter,
        extract_emo_tag=lambda text: (None, text),
        apply_character_catchphrase=lambda text: text,
    )


def test_fallback_alert_text_uses_work_duration():
    text = _service()._fallback_alert_text(60)
    assert "工作了 60 分钟" in text
    assert "坐了" not in text


@pytest.mark.asyncio
async def test_send_active_alert_prompt_uses_work_duration(monkeypatch):
    captured = {}

    class Presenter:
        async def present(self, text, **kwargs):
            captured["reply"] = text
            captured["kwargs"] = kwargs

    service = _service(Presenter())

    def fake_chat(messages, **kwargs):
        captured["messages"] = messages
        return "起来走动一下"

    monkeypatch.setattr("modules.llm.chat_with_ai", fake_chat)
    await service.send_active_alert("电脑", 45)

    prompt = captured["messages"][1]["content"]
    assert "连续工作了 45 分钟" in prompt
    assert "坐了" not in prompt
    assert captured["reply"] == "起来走动一下"
