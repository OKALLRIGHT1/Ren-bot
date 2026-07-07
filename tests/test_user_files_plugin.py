import importlib.util
import json
import sys
from pathlib import Path

import pytest


PLUGIN_DIR = Path("plugins/user_files")


def load_plugin_class():
    spec = importlib.util.spec_from_file_location(
        "test_user_files_plugin", PLUGIN_DIR / "plugin.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.Plugin


def test_user_files_config_is_direct_and_owner_only_for_remote():
    config = json.loads((PLUGIN_DIR / "config.json").read_text(encoding="utf-8-sig"))

    assert config["trigger"] == "user_files"
    assert config["type"] == "direct"
    assert config["access_control"]["allow_local"] is True
    assert config["access_control"]["allow_remote_qq"] is True
    assert config["access_control"]["allow_qq_owner"] is True
    assert config["access_control"]["allow_qq_others"] is False
    assert "用户文件" in config["aliases"]


def _plugin_with_root(tmp_path):
    Plugin = load_plugin_class()
    plugin = Plugin()
    plugin.settings = {
        "custom_roots": {
            "default": [
                {"name": "tmp", "path": str(tmp_path)},
                {"name": "downloads", "path": str(tmp_path)},
            ]
        },
        "max_read_chars": {"default": 200},
        "max_list_items": {"default": 20},
    }
    return plugin


@pytest.mark.asyncio
async def test_user_files_lists_and_reads_custom_root(tmp_path):
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    plugin = _plugin_with_root(tmp_path)

    listing = await plugin.run("list ||| tmp ||| .", {"allow_read": True})
    content = await plugin.run("read ||| tmp ||| a.txt", {"allow_read": True})

    assert "a.txt" in listing
    assert "hello" in content


@pytest.mark.asyncio
async def test_user_files_natural_language_read_downloads_file(tmp_path):
    (tmp_path / "eur-yp6y2hha8y.txt").write_text("download text", encoding="utf-8")
    plugin = _plugin_with_root(tmp_path)

    content = await plugin.run(
        "帮我看看下载目录里的eur-yp6y2hha8y.txt文件里是什么",
        {"allow_read": True},
    )

    assert "download text" in content


@pytest.mark.asyncio
async def test_user_files_rejects_path_escape(tmp_path):
    plugin = _plugin_with_root(tmp_path)

    result = await plugin.run("read ||| tmp ||| ..\\secret.txt", {"allow_read": True})

    assert "路径越界" in result


@pytest.mark.asyncio
async def test_user_files_write_requires_confirmation(tmp_path):
    plugin = _plugin_with_root(tmp_path)

    result = await plugin.run(
        "write ||| tmp ||| note.txt ||| hello",
        {"allow_read": True, "allow_write": True},
    )

    assert result["__agent_result__"] == "confirmation_required"
    assert result["trigger"] == "user_files"
    assert not (tmp_path / "note.txt").exists()

    confirmed = await plugin.confirm_agent_action(result["payload"], {"allow_write": True})

    assert "已写入" in confirmed
    assert (tmp_path / "note.txt").read_text(encoding="utf-8") == "hello"


@pytest.mark.asyncio
async def test_user_files_move_requires_confirmation(tmp_path):
    (tmp_path / "from.txt").write_text("hello", encoding="utf-8")
    plugin = _plugin_with_root(tmp_path)

    result = await plugin.run(
        "move ||| tmp ||| from.txt ||| to.txt",
        {"allow_read": True, "allow_write": True},
    )

    assert result["__agent_result__"] == "confirmation_required"
    assert (tmp_path / "from.txt").exists()

    confirmed = await plugin.confirm_agent_action(result["payload"], {"allow_write": True})

    assert "已移动" in confirmed
    assert not (tmp_path / "from.txt").exists()
    assert (tmp_path / "to.txt").read_text(encoding="utf-8") == "hello"


def test_user_files_natural_language_requires_clear_file_intent():
    Plugin = load_plugin_class()
    plugin = Plugin()

    assert plugin.should_handle_direct("帮我看看下载目录里的 a.txt", {}, "用户文件")
    assert plugin.should_handle_direct("读取 Documents 里的 notes.txt", {}, "文件助手")
    assert not plugin.should_handle_direct("我今天下载了一个游戏", {}, "下载")


def test_user_files_declares_command_and_natural_capabilities():
    Plugin = load_plugin_class()
    plugin = Plugin()

    capabilities = plugin.get_capabilities()

    assert [cap.id for cap in capabilities] == [
        "user_files.command",
        "user_files.read",
        "user_files.list",
    ]


def test_user_files_capability_matches_clear_file_intent_only():
    Plugin = load_plugin_class()
    plugin = Plugin()
    capabilities = {cap.id: cap for cap in plugin.get_capabilities()}

    command_match = capabilities["user_files.command"].match(
        "user_files read ||| downloads ||| a.txt", {}
    )
    read_match = capabilities["user_files.read"].match("帮我看看下载目录里的 a.txt", {})
    list_match = capabilities["user_files.list"].match("列出 downloads 目录里的文件", {})
    casual_match = capabilities["user_files.read"].match("我今天下载了一个游戏", {})

    assert command_match is not None
    assert command_match.score == 1.0
    assert read_match is not None
    assert read_match.args == {"action": "read", "root": "downloads", "path": "a.txt"}
    assert list_match is not None
    assert list_match.args == {"action": "list", "root": "downloads", "path": "."}
    assert casual_match is None


def test_user_files_downloads_root_falls_back_to_existing_drive_downloads(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    drive_downloads = tmp_path / "drive" / "Downloads"
    drive_downloads.mkdir(parents=True)
    Plugin = load_plugin_class()
    plugin = Plugin()

    monkeypatch.setattr(plugin, "_home_dir", lambda: fake_home, raising=False)
    monkeypatch.setattr(plugin, "_drive_download_candidates", lambda: [drive_downloads], raising=False)

    roots = plugin._roots()

    assert roots["downloads"] == drive_downloads.resolve()
