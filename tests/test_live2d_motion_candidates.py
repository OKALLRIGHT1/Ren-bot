import random
import asyncio
import json

import modules.live2d as live2d


def test_pick_motion_candidate_accepts_model_default_pose_marker():
    cfg = {"mtn": live2d.MODEL_DEFAULT_MOTION, "type": 0}

    selected = live2d.pick_motion_candidate(cfg, rng=random.Random(0))

    assert selected == {"mtn": live2d.MODEL_DEFAULT_MOTION, "type": 0}


def test_pick_motion_candidate_accepts_stop_motion_marker():
    cfg = {"mtn": live2d.STOP_MOTION, "type": 0}

    selected = live2d.pick_motion_candidate(cfg, rng=random.Random(0))

    assert selected == {"mtn": live2d.STOP_MOTION, "type": 0}


def test_play_motion_sends_stop_motion_marker(monkeypatch):
    sent = []

    async def fake_send(msg, msg_id, data_builder, max_retries=2):
        sent.append((msg, msg_id, data_builder(0)))

    monkeypatch.setattr(live2d, "_send_to_models", fake_send)

    asyncio.run(live2d.play_motion(live2d.STOP_MOTION))

    assert sent == [
        (13200, 2, {"id": 0, "type": 0, "mtn": live2d.STOP_MOTION})
    ]


def test_play_motion_resolves_model_default_pose_from_current_legacy_model(
    tmp_path, monkeypatch
):
    model_path = tmp_path / "model.json"
    model_path.write_text(
        json.dumps(
            {
                "motions": {
                    "angry01": [{"file": "data/motions/angry01.mtn"}],
                    "idle01": [{"file": "data/motions/idle01.mtn"}],
                }
            }
        ),
        encoding="utf-8",
    )
    sent = []

    async def fake_send(msg, msg_id, data_builder, max_retries=2):
        sent.append((msg, msg_id, data_builder(0)))

    monkeypatch.setattr(live2d, "_send_to_models", fake_send)
    monkeypatch.setattr(live2d, "_CURRENT_COSTUME_MODEL_PATH", str(model_path))

    asyncio.run(live2d.play_motion(live2d.MODEL_DEFAULT_MOTION))

    assert sent == [
        (13200, 2, {"id": 0, "type": 0, "mtn": "angry01:angry01"})
    ]


def test_pick_motion_candidate_keeps_legacy_single_motion():
    cfg = {"mtn": "idle01", "type": 1, "exp": 2}

    selected = live2d.pick_motion_candidate(cfg, rng=random.Random(0))

    assert selected == {"mtn": "idle01", "type": 1}


def test_pick_motion_candidate_randomly_selects_from_motion_list():
    cfg = {
        "mtn": "idle01",
        "type": 0,
        "motions": [
            {"mtn": "angry01:angry01", "type": 0},
            {"mtn": "angry02:angry02", "type": 1},
        ],
    }

    selected = live2d.pick_motion_candidate(cfg, rng=random.Random(0))

    assert selected in cfg["motions"]
    assert selected["mtn"].startswith("angry")


def test_update_current_costume_config_refreshes_runtime_emotion_map(monkeypatch):
    monkeypatch.setattr(live2d, "_CURRENT_COSTUME_CONFIG", {})
    monkeypatch.setattr(live2d, "_CURRENT_COSTUME_EMOTION_MAP", {})
    monkeypatch.setattr(live2d, "_CURRENT_COSTUME_MODEL_PATH", "old")

    live2d.update_current_costume_config(
        {"emotion_map": {"happy": {"mtn": "new-happy", "type": 1}}},
        model_path="D:/models/tomori/model.json",
    )

    assert live2d._CURRENT_COSTUME_MODEL_PATH == "D:/models/tomori/model.json"
    assert live2d.resolve_emotion_config("happy") == {"mtn": "new-happy", "type": 1}
