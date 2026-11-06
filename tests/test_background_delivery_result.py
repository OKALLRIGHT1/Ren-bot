from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.chat_service import ChatService
from services.chat_support.gateway_sender import GatewaySender
from services.plugin_ports.gateway import ChatGatewayPort


@pytest.mark.asyncio
@pytest.mark.parametrize("ok", [False, True])
async def test_gateway_business_result_reaches_background_plugin(ok):
    gateway = SimpleNamespace(send_text=AsyncMock(return_value={"ok": ok}))
    logger = SimpleNamespace(info=lambda *a: None, warning=lambda *a: None, error=lambda *a: None)
    sender = GatewaySender(chat_gateway_getter=lambda: gateway, logger=logger,
                           prepare_reply_for_output=lambda text, *a, **kw: text,
                           strip_emo_tags=lambda text: text, strip_cmd=lambda text: text,
                           clean_text_for_tts=lambda text: text)
    service = ChatService.__new__(ChatService)
    service.gateway_sender = sender
    service.chat_gateway = gateway
    port = ChatGatewayPort.from_chat_service(service)
    origin = {"source": "qq_gateway", "channel_meta": {"adapter": "napcat_qq", "session_id": "private:42"}}
    assert await port.send_text("后台任务完成", origin) is ok
    assert gateway.send_text.await_args.args[:2] == ("napcat_qq", "private:42")


@pytest.mark.asyncio
async def test_no_target_is_failure_not_unknown_delivery():
    sender = GatewaySender(chat_gateway_getter=lambda: None, logger=SimpleNamespace(warning=lambda *a: None),
                           prepare_reply_for_output=lambda text, *a, **kw: text,
                           strip_emo_tags=lambda text: text, strip_cmd=lambda text: text,
                           clean_text_for_tts=lambda text: text)
    assert await sender.send_reply("后台任务完成", {}) is False
