import asyncio

from modules.emotion_controller import EmotionController
from modules.state_machine import AgentState


def test_idle_random_motion_runs_only_when_idle(monkeypatch):
    calls = []

    async def fake_play_motion(mtn, motion_type=0):
        calls.append(("motion", mtn, motion_type))

    monkeypatch.setattr("modules.live2d.play_motion", fake_play_motion)
    monkeypatch.setattr(
        "modules.live2d.pick_motion_candidate",
        lambda cfg: {"mtn": cfg["mtn"], "type": int(cfg.get("type", 0))},
    )

    controller = EmotionController(
        mapping={
            "idle_random": {"mtn": "idle-extra", "type": 0},
            "idle": {"mtn": "idle-base", "type": 1},
        }
    )

    async def run():
        controller.agent_state = AgentState.SPEAKING
        assert await controller.play_idle_random_once(return_idle_delay=0) is False
        controller.agent_state = AgentState.IDLE
        assert await controller.play_idle_random_once(return_idle_delay=0) is True

    asyncio.run(run())

    assert calls == [
        ("motion", "idle-extra", 0),
        ("motion", "idle-base", 1),
    ]
