from datetime import date

import pytest

from services.chat_support import diary_utils
from services.chat_support.diary_service import DiaryService


def _owner_assistant_row():
    return {
        "ts": 1_700_000_000,
        "role": "assistant",
        "content": "我帮你查一下。",
        "session_id": "owner_shared",
        "meta": {
            "source": "qq_gateway",
            "message_type": "private",
            "is_owner": True,
        },
    }


def test_diary_transcript_uses_character_name_for_assistant_lines():
    history = diary_utils.fetch_day_chat_history(
        [_owner_assistant_row()],
        "2023-11-14",
        owner_shared_session_id="owner_shared",
        legacy_owner_private_session_ids=set(),
        owner_shared_local_sources={"desktop"},
        qq_remote_sources={"qq_gateway"},
        assistant_name="高松灯",
        owner_label="Master",
    )

    assert "高松灯（对 Master）" in history
    assert "AI(" not in history
    assert "AI（" not in history


@pytest.mark.asyncio
async def test_diary_prompt_keeps_current_character_self_identity(monkeypatch):
    captured = {}

    def fake_chat(messages, *args, **kwargs):
        captured["messages"] = messages
        return "我今天陪Master聊了几句。"

    monkeypatch.setattr("modules.llm.chat_with_ai", fake_chat)

    class Brain:
        sqlite_store = None

    class EventBus:
        async def emit(self, *args, **kwargs):
            return None

    class Presenter:
        async def present(self, *args, **kwargs):
            return None

    class Logger:
        def warning(self, *args, **kwargs):
            return None

        def error(self, *args, **kwargs):
            return None

    async def noop_async(*args, **kwargs):
        return None

    service = DiaryService(
        brain=Brain(),
        event_bus=EventBus(),
        presenter=Presenter(),
        logger=Logger(),
        add_memory_safe=noop_async,
        emit_idle_status_when_safe=noop_async,
        send_gateway_reply=noop_async,
        backfill_napcat_history_for_day=noop_async,
        load_day_transcript_rows=lambda _date: [_owner_assistant_row()],
        get_runtime_owner_label=lambda: "Master",
        owner_ids=[],
        owner_shared_session_id="owner_shared",
        legacy_owner_private_session_ids=set(),
        owner_shared_local_sources={"desktop"},
        qq_remote_sources={"qq_gateway"},
        get_active_character_context=lambda: (
            "高松灯",
            "char_tomori",
            "你是高松灯。",
        ),
    )

    result = await service.summarize_day(
        report_data="Master今天使用了电脑。",
        auto=True,
        target_date=date(2026, 7, 17),
    )

    assert result == "这是补写的内容。我今天陪Master聊了几句。"
    prompt = "\n".join(str(item.get("content") or "") for item in captured["messages"])
    assert "高松灯（对 Master）" in prompt
    assert "AI(to Owner)" not in prompt
    assert "不要把自己称为“AI”" in prompt
