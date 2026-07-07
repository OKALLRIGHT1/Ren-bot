import importlib.util
import json
import sys
from pathlib import Path

import pytest

from modules.plugin_manager import PluginManager


PLUGIN_DIR = Path("plugins/app_control")


def load_plugin_class():
    spec = importlib.util.spec_from_file_location(
        "test_app_control_plugin", PLUGIN_DIR / "plugin.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.Plugin


def _qq_ctx(*, owner=False):
    return {
        "source": "qq_gateway",
        "channel_meta": {
            "adapter": "napcat_qq",
            "is_owner": owner,
            "message_type": "private",
        },
    }


def test_app_control_config_allows_only_local_and_qq_owner():
    config = json.loads((PLUGIN_DIR / "config.json").read_text(encoding="utf-8-sig"))

    assert config["trigger"] == "app_control"
    assert config["type"] == "direct"
    assert "/重启" in config["aliases"]
    assert config["access_control"]["allow_local"] is True
    assert config["access_control"]["allow_remote_qq"] is True
    assert config["access_control"]["allow_qq_owner"] is True
    assert config["access_control"]["allow_qq_others"] is False


@pytest.mark.asyncio
async def test_app_control_restart_requires_command_prefix_and_owner():
    Plugin = load_plugin_class()
    plugin = Plugin()
    plugin.name = "主程序控制"
    plugin.access_control = {
        "allow_local": True,
        "allow_remote_qq": True,
        "allow_qq_owner": True,
        "allow_qq_others": False,
        "allow_group_without_at": False,
    }

    manager = PluginManager(plugin_dir="plugins")
    manager.plugins = {"app_control": plugin}
    manager.direct_map = {"/重启": plugin, "/远程重启": plugin, "/restart": plugin}

    handled, result = await manager.execute_direct_commands(
        "帮我重启一下", {"source": "text_input"}
    )
    assert handled is False
    assert result is None

    handled, result = await manager.execute_direct_commands("/重启", _qq_ctx(owner=False))
    assert handled is True
    assert "不允许" in result
    assert "其他 QQ 联系人" in result

    handled, result = await manager.execute_direct_commands("/重启", _qq_ctx(owner=True))
    assert handled is True
    assert result["__type__"] == "app_restart"
    assert "重启" in result["message"]
