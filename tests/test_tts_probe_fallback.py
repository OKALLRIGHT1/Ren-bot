from __future__ import annotations

import importlib
import sys


def test_unready_gptsovits_falls_back_to_edge_and_reprobes(monkeypatch):
    sys.modules.pop("modules.tts.router", None)
    router_module = importlib.import_module("modules.tts.router")
    warnings = []
    clock = {"now": 1000.0}

    class FakeGPTSoVITS:
        def __init__(self, **kwargs):
            self.ready = False
            self.last_error = "无法连接 GPT-SoVITS 服务"

        def apply_runtime_config(self, cfg):
            del cfg
            self.last_error = "无法连接 GPT-SoVITS 服务"
            self.ready = False

    monkeypatch.setattr(
        router_module,
        "_load_gptsovits_class",
        lambda verbose=False: FakeGPTSoVITS,
    )
    monkeypatch.setattr(router_module.time, "time", lambda: clock["now"])
    monkeypatch.setattr(router_module, "print", warnings.append, raising=False)
    import builtins

    monkeypatch.setattr(builtins, "print", warnings.append)
    router = router_module.TTSRouter(
        edge_cfg={"voice": "zh-CN-XiaoxiaoNeural"},
        verbose=True,
    )
    role_cfg = {
        "enabled": True,
        "gpt_w": "G:/voice/model.ckpt",
        "sov_w": "G:/voice/model.pth",
        "ref_wav": "G:/voice/ref.wav",
    }

    router.apply_role_tts_config(role_cfg)
    first = router.tts_status()
    assert first["backend"] == "edge"
    assert first["display"] == "edge(fallback)"
    assert first["gpt_ready"] is False
    router.apply_role_tts_config(role_cfg)
    assert sum("回退到 Edge-TTS" in str(item) for item in warnings) == 1

    def recover(cfg):
        del cfg
        router.gpt.ready = True
        router.gpt.last_error = ""

    router.gpt.apply_runtime_config = recover
    clock["now"] = 1000.0 + 301.0
    status = router.probe_role_tts()
    assert status["backend"] == "gpt"
    assert status["display"] == "gpt"
    assert status["gpt_ready"] is True
