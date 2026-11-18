from types import SimpleNamespace

import pytest

from services.chat_service import ChatService


def _service(*, has_vision: bool):
    service = object.__new__(ChatService)
    service.logger = SimpleNamespace(warning=lambda *_args, **_kwargs: None)
    service._chat_route_has_vision = lambda: has_vision
    service._build_live2d_self_awareness_hint = lambda *_args, **_kwargs: ""
    return service


@pytest.mark.asyncio
async def test_describe_external_images_attaches_when_chat_has_vision(monkeypatch):
    service = _service(has_vision=True)
    monkeypatch.setattr(
        service,
        "_load_external_image_payloads",
        lambda ctx: _async_value(["img-a", "img-b"]),
    )
    ctx = {"source": "qq_gateway", "channel_meta": {"images": [{}]}}
    text = await service._describe_external_images(ctx)
    assert text == ""
    assert ctx["_pending_vision_images"] == ["img-a", "img-b"]
    assert ctx["has_screenshot"] is True


@pytest.mark.asyncio
async def test_describe_external_images_uses_vision_task_without_chat_vision(
    monkeypatch,
):
    service = _service(has_vision=False)
    calls = []

    async def fake_analyze(image_base64, prompt, caller=""):
        calls.append((image_base64, caller))
        return f"desc-{image_base64}"

    monkeypatch.setattr(
        service,
        "_load_external_image_payloads",
        lambda ctx: _async_value(["img-a"]),
    )
    monkeypatch.setattr("modules.llm.analyze_image", fake_analyze)
    ctx = {
        "source": "qq_gateway",
        "channel_meta": {"images": [{}], "image_vision_enabled": True},
    }
    text = await service._describe_external_images(ctx)
    assert "【QQ图片识别】" in text
    assert "desc-img-a" in text
    assert "_pending_vision_images" not in ctx
    assert calls == [("img-a", "qq_image_describe")]


async def _async_value(value):
    return value
