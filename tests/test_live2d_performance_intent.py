import asyncio

from modules import live2d


def test_performance_intent_is_retired(monkeypatch):
    sent = []

    async def fake_send(msg, msg_id, data_builder, max_retries=2):
        sent.append({"msg": msg, "msg_id": msg_id, "data": data_builder(0)})

    monkeypatch.setattr(live2d, "_send_to_models", fake_send)
    monkeypatch.setattr(live2d, "is_performance_enabled", lambda: True)
    live2d.set_performance_client_check(lambda: True)

    assert live2d.has_performance_client() is True
    assert asyncio.run(live2d.send_performance_intent("happy", 0.8)) is False
    assert sent == []

    monkeypatch.setattr(
        live2d,
        "EMO_TO_LIVE2D",
        {"angry": {"type": 0, "mtn": "angry01", "exp": 1}},
        raising=False,
    )
    monkeypatch.setattr(live2d, "_CURRENT_COSTUME_EMOTION_MAP", {})
    asyncio.run(live2d.trigger_emotion("angry"))
    msgs = [item["msg"] for item in sent]
    assert 13700 not in msgs
    motion = next(item["data"] for item in sent if item["msg"] == 13200)
    expression = next(item["data"] for item in sent if item["msg"] == 13300)
    assert motion["mtn"] == "angry01"
    assert motion["emotion"] == "angry"
    assert motion["intensity"] == 0.5
    assert expression["expId"] == 1
    assert expression["emotion"] == "angry"

    live2d.set_performance_client_check(None)


def test_send_bubble_does_not_retrigger_when_performance_enabled(monkeypatch):
    sent = []

    async def fake_send(msg, msg_id, data_builder, max_retries=2):
        sent.append(msg)

    monkeypatch.setattr(live2d, "_send_to_models", fake_send)
    monkeypatch.setattr(live2d, "is_performance_enabled", lambda: True)
    live2d.set_performance_client_check(lambda: True)

    asyncio.run(live2d.send_bubble("hello", emotion="happy", duration_ms=1200))
    assert sent == [11000]

    live2d.set_performance_client_check(None)


def test_performance_disabled_keeps_legacy_emotion(monkeypatch):
    sent = []

    async def fake_send(msg, msg_id, data_builder, max_retries=2):
        sent.append(msg)

    monkeypatch.setattr(live2d, "_send_to_models", fake_send)
    monkeypatch.setattr(live2d, "is_performance_enabled", lambda: False)
    live2d.set_performance_client_check(lambda: True)
    monkeypatch.setattr(live2d, "_CURRENT_COSTUME_EMOTION_MAP", {})
    monkeypatch.setattr(
        live2d,
        "EMO_TO_LIVE2D",
        {"happy": {"type": 0, "mtn": "Motion:motion_100", "exp": 1}},
        raising=False,
    )

    asyncio.run(live2d.trigger_emotion("happy"))
    assert 13300 in sent
    assert 13200 in sent
    assert 13700 not in sent

    live2d.set_performance_client_check(None)


def test_emotion_controller_sends_intensity_on_motion_and_expression(monkeypatch):
    from modules.emotion_controller import EmotionController

    played = []

    async def fake_set_expression(exp_id, **kwargs):
        played.append(("exp", exp_id, kwargs))

    async def fake_play_motion(mtn, motion_type=0, **kwargs):
        played.append(("mtn", mtn, motion_type, kwargs))

    monkeypatch.setattr(live2d, "has_performance_client", lambda: True)
    monkeypatch.setattr(live2d, "set_expression", fake_set_expression)
    monkeypatch.setattr(live2d, "play_motion", fake_play_motion)

    controller = EmotionController(mapping={"happy": {"exp": 1, "mtn": "Motion:motion_100"}})
    asyncio.run(controller.request_emotion("happy", 0.7, prefer_motion=True, reason="model_reply"))
    assert played == [
        ("exp", 1, {"emotion": "happy", "intensity": 0.7}),
        ("mtn", "Motion:motion_100", 0, {"emotion": "happy", "intensity": 0.7}),
    ]
