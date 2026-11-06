import pytest

from services.chat_support.followup_service import FollowupService


def _service(store=None):
    return FollowupService(
        memory_store_getter=lambda: store,
        add_memory_safe=lambda *args, **kwargs: None,
    )


def test_question_recall_is_not_extracted_as_user_task():
    service = _service()
    assert service.looks_like_question_or_recall("你还记得我上次开会说了什么吗")
    assert service.extract_task_candidates("你还记得我上次开会说了什么吗") == []
    assert service.extract_task_candidates("记得帮我买咖啡") == ["帮我买咖啡"]


def test_task_followup_mentions_the_task():
    service = _service()
    assert "交开题" in service.build_task_followup_text("交开题")
    assert "交房租" in service.build_task_followup_text("交房租")


def test_care_followup_uses_topic_label():
    service = _service()
    assert "开题" in service.build_followup_text("work_study", "开题")
    assert service.build_followup_text("plan", "搬家")
    assert "身体" not in service.build_followup_text("work_study", "开题")


def test_injury_message_suppresses_followup_preface():
    service = _service()
    assert service.should_suppress_followup_preface("我摔倒了好疼")
    assert not service.should_suppress_followup_preface("今天天气不错")


@pytest.mark.asyncio
async def test_task_followup_skips_non_desktop_source():
    service = _service()
    result = await service.maybe_send_task_followup(
        "你好",
        ctx={"source": "napcat_qq"},
    )
    assert result is None


@pytest.mark.asyncio
async def test_proactive_followup_skips_codex_mode():
    service = _service()
    result = await service.maybe_send_proactive_followup(
        "你好",
        ctx={"source": "text_input", "codex_mode": True},
    )
    assert result is None
