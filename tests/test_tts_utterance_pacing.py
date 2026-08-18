import asyncio
import time

import pytest

from modules.tts.router import AudioItem, TTSRouter


def _router(**kwargs):
    edge_cfg = {
        "voice": "zh-CN-XiaoxiaoNeural",
        "rate": "+0%",
        "volume": "+0%",
        "enabled": True,
        "max_chars": 500,
        "use_live2d_player": True,
        "live2d_channel": 0,
        "live2d_volume": 1.0,
        "enable_lip_sync": False,
        "rhubarb_path": "",
        "lip_sync_smooth_window": 3,
    }
    return TTSRouter(edge_cfg=edge_cfg, verbose=False, **kwargs)


def test_utterance_display_lingers_past_audio():
    router = _router()
    item = AudioItem(None, 2.0, "你好。", "neutral", None)
    assert router._utterance_display_ms(item, 2.0) == 2350


@pytest.mark.asyncio
async def test_emit_utterance_sends_matching_line_and_bubble():
    lines = []
    bubbles = []

    async def line_sender(text):
        lines.append(text)

    async def bubble_sender(text, emotion, duration_ms):
        bubbles.append((text, emotion, duration_ms))

    router = _router(line_sender=line_sender, bubble_sender=bubble_sender)
    item = AudioItem(
        None,
        1.2,
        "台风的话，上海这边……秋天偶尔会有它的尾巴扫过。",
        "think",
        None,
        show_bubble=True,
        append_ui=True,
    )
    await router._emit_utterance(item, 1550)
    assert lines == ["台风的话，上海这边……秋天偶尔会有它的尾巴扫过。"]
    assert bubbles == [
        ("台风的话，上海这边……秋天偶尔会有它的尾巴扫过。", "think", 1550)
    ]


@pytest.mark.asyncio
async def test_player_sends_next_line_after_previous_finishes():
    events = []

    class FakeBackend:
        async def play_audio_file(self, *args, **kwargs):
            return None

    async def line_sender(text):
        events.append((text, time.monotonic()))

    router = _router(line_sender=line_sender)
    router.text_linger_sec = 0.08
    router.segment_pause_sec = 0.0
    router.final_pause_sec = 0.0
    router._ensure_worker()
    backend = FakeBackend()
    await router._audio_q.put(
        AudioItem(
            "dummy-a",
            0.12,
            "第一句。",
            "neutral",
            backend,
            tail_padding=0.0,
            show_bubble=False,
            append_ui=True,
        )
    )
    await router._audio_q.put(
        AudioItem(
            "dummy-b",
            0.12,
            "第二句。",
            "neutral",
            backend,
            tail_padding=0.0,
            show_bubble=False,
            append_ui=True,
        )
    )
    deadline = time.monotonic() + 2.0
    while len(events) < 2 and time.monotonic() < deadline:
        await asyncio.sleep(0.02)

    assert [text for text, _ in events] == ["第一句。", "第二句。"]
    assert events[1][1] - events[0][1] >= 0.16
    for task in (router._worker_task, router._player_task):
        if task:
            task.cancel()
