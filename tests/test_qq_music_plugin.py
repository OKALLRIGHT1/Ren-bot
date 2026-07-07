import importlib.util
import os
import sys
import asyncio
import time
from pathlib import Path

from integrations.chat_gateway.napcat import NapCatOneBotAdapter


PLUGIN_DIR = Path("plugins/qq_music")
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_plugin_class():
    spec = importlib.util.spec_from_file_location(
        "test_qq_music_plugin", PLUGIN_DIR / "plugin.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.Plugin


def test_qq_music_default_cache_dir_is_gateway_allowed():
    Plugin = load_plugin_class()
    plugin = Plugin()

    cache_dir = Path(plugin._cache_dir({})).resolve()

    assert cache_dir == (PROJECT_ROOT / "audio_cache" / "qq_music").resolve()
    assert NapCatOneBotAdapter()._file_allowed(cache_dir / "song.mp3") is True


def test_qq_music_legacy_plugin_cache_setting_migrates_to_gateway_allowed_dir():
    Plugin = load_plugin_class()
    plugin = Plugin()

    cache_dir = Path(
        plugin._cache_dir({"cache_subdir": {"default": "data/cache"}})
    ).resolve()

    assert cache_dir == (PROJECT_ROOT / "audio_cache" / "qq_music").resolve()
    assert os.path.commonpath([str(PROJECT_ROOT), str(cache_dir)]) == str(PROJECT_ROOT)


def test_qq_music_request_timeout_keeps_margin_under_tool_timeout():
    Plugin = load_plugin_class()
    plugin = Plugin()
    plugin.timeout_sec = 30

    timeout = plugin._request_timeout_sec(
        {"request_timeout_sec": {"default": 30}}
    )

    assert timeout == 10


def test_qq_music_search_attempts_run_concurrently():
    Plugin = load_plugin_class()
    plugin = Plugin()
    plugin.settings = {"api_key": {"default": "test-key"}}

    calls = []

    async def fake_get_json(url, api_key, session_cookie, timeout_sec):
        calls.append(url)
        await asyncio.sleep(0.05)
        return {"code": 200, "data": []}

    plugin._get_json_async = fake_get_json

    started = time.perf_counter()
    result = asyncio.run(
        plugin.run(
            "点歌 春日影",
            {"source": "desktop"},
        )
    )
    elapsed = time.perf_counter() - started

    assert len(calls) == 6
    assert elapsed < 0.2
    assert "没有找到" in result
