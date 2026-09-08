import pytest

from services.chat_support.direct_flow_service import (
    DirectFlowHooks,
    interpret_direct_result,
)
from services.chat_support.flow_result import STATUS_HANDLED_SILENT, STATUS_REPLIED


class _Presenter:
    def __init__(self):
        self.presented = []

    async def present(self, text, **kwargs):
        self.presented.append((text, kwargs))


class _Bus:
    def __init__(self):
        self.events = []

    async def emit(self, name, **kwargs):
        self.events.append((name, kwargs))


def _hooks(**overrides):
    async def send_text(text, ctx, emotion=None):
        overrides.setdefault("texts", []).append((text, emotion))

    async def send_image(path, ctx, caption=""):
        overrides.setdefault("images", []).append((path, caption))
        return overrides.get("image_ok", True)

    async def send_file(path, ctx, file_name=""):
        overrides.setdefault("files", []).append((path, file_name))
        return overrides.get("file_ok", True)

    async def send_voice(path, ctx):
        overrides.setdefault("voices", []).append(path)
        return overrides.get("voice_ok", True)

    async def cleanup(path):
        overrides.setdefault("cleaned", []).append(path)

    async def polish(*, user_text, direct_text, ctx):
        return "polished:" + direct_text

    async def add_memory(*args, **kwargs):
        return None

    async def idle(*args, **kwargs):
        return None

    values = dict(
        send_text=send_text,
        send_image=send_image,
        send_file=send_file,
        send_voice=send_voice,
        cleanup_image=cleanup,
        present_direct_tool_text=polish,
        presenter=_Presenter(),
        event_bus=_Bus(),
        add_memory_safe=add_memory,
        update_active_time=lambda: None,
        set_codex_task_state=lambda *a, **k: None,
        emit_idle_status_when_safe=idle,
        schedule_app_restart=lambda result: overrides.setdefault("restarts", []).append(
            result
        ),
    )
    values.update({k: v for k, v in overrides.items() if k in DirectFlowHooks.__dataclass_fields__})
    hooks = DirectFlowHooks(**values)
    hooks._captured = overrides
    return hooks


@pytest.mark.asyncio
async def test_image_success_is_silent_with_caption_memory():
    hooks = _hooks()
    ctx = {"source": "qq_gateway"}
    outcome = await interpret_direct_result(
        user_text="发图",
        direct_result={
            "__type__": "gateway_image",
            "image_path": "D:/tmp/a.png",
            "caption": "图片说明",
            "cleanup": False,
        },
        ctx=ctx,
        hooks=hooks,
    )
    assert outcome.flow.status == STATUS_HANDLED_SILENT
    assert outcome.flow.reply_text == ""
    assert outcome.flow.text_for_memory() == "图片说明"
    assert outcome.skip_gateway_text is True
    assert hooks._captured["images"] == [("D:/tmp/a.png", "")]


@pytest.mark.asyncio
async def test_image_success_sends_qq_post_text():
    hooks = _hooks()
    outcome = await interpret_direct_result(
        user_text="查邮件",
        direct_result={
            "__type__": "gateway_image",
            "image_path": "D:/tmp/mail.png",
            "caption": "卡片",
            "post_send_text": "共 2 封",
            "cleanup": False,
        },
        ctx={"source": "qq_gateway"},
        hooks=hooks,
    )
    assert outcome.flow.status == STATUS_HANDLED_SILENT
    assert hooks._captured["texts"] == [("共 2 封", "neutral")]
    assert outcome.flow.text_for_memory() == "卡片"


@pytest.mark.asyncio
async def test_restart_keeps_message_and_pending_payload():
    hooks = _hooks()
    payload = {"__type__": "app_restart", "message": "收到，正在重启主程序。", "delay_sec": 0}
    outcome = await interpret_direct_result(
        user_text="/重启",
        direct_result=payload,
        ctx={"source": "qq_gateway"},
        hooks=hooks,
    )
    assert outcome.flow.status == STATUS_REPLIED
    assert outcome.flow.reply_text == "收到，正在重启主程序。"
    assert outcome.pending_restart["__type__"] == "app_restart"
    assert outcome.skip_gateway_text is False


@pytest.mark.asyncio
async def test_plain_text_is_polished_and_dict_is_not():
    hooks = _hooks()
    plain = await interpret_direct_result(
        user_text="列表",
        direct_result="工具处理完成",
        ctx={"source": "text_input"},
        hooks=hooks,
    )
    assert plain.flow.reply_text == "polished:工具处理完成"

    typed = await interpret_direct_result(
        user_text="/重启",
        direct_result={"__type__": "app_restart", "message": "重启"},
        ctx={"source": "text_input"},
        hooks=hooks,
    )
    assert typed.flow.reply_text == "重启"
