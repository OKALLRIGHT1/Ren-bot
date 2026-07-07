import json
from pathlib import Path

from modules.plugin_manager import PluginManager


def _write_plugin(
    root: Path,
    *,
    folder: str = "demo_plugin",
    trigger: str = "demo_plugin",
    config_type: str | None = None,
    class_type: str | None = None,
) -> Path:
    plugin_dir = root / folder
    plugin_dir.mkdir()
    config = {
        "name": "Demo Plugin",
        "trigger": trigger,
        "llm_command": trigger,
        "settings": {},
        "access_control": {},
    }
    if config_type is not None:
        config["type"] = config_type
    (plugin_dir / "config.json").write_text(
        json.dumps(config, ensure_ascii=False),
        encoding="utf-8",
    )
    type_line = f'    type = "{class_type}"\n' if class_type is not None else ""
    (plugin_dir / "plugin.py").write_text(
        "class Plugin:\n"
        f"{type_line}"
        "    async def run(self, args, ctx=None):\n"
        "        return 'ok'\n",
        encoding="utf-8",
    )
    return plugin_dir


def test_config_type_wins_over_class_type_and_records_warning(tmp_path: Path) -> None:
    _write_plugin(tmp_path, config_type="direct", class_type="delegate")
    manager = PluginManager(plugin_dir=str(tmp_path))

    manager.load_plugins()

    plugin = manager.plugins["demo_plugin"]
    assert plugin.type == "direct"
    assert any(
        error.get("code") == "plugin_type_mismatch"
        and error.get("plugin") == "demo_plugin"
        and error.get("config_type") == "direct"
        and error.get("class_type") == "delegate"
        for error in manager.load_errors
    )


def test_missing_config_type_uses_class_type(tmp_path: Path) -> None:
    _write_plugin(tmp_path, class_type="delegate")
    manager = PluginManager(plugin_dir=str(tmp_path))

    manager.load_plugins()

    plugin = manager.plugins["demo_plugin"]
    assert plugin.type == "delegate"
    assert manager.delegate_map["demo_plugin"] is plugin


def test_save_plugin_config_without_type_preserves_runtime_type(tmp_path: Path) -> None:
    _write_plugin(tmp_path, config_type="delegate", class_type="delegate")
    manager = PluginManager(plugin_dir=str(tmp_path))
    manager.load_plugins()

    assert manager.save_plugin_config(
        "demo_plugin",
        {
            "name": "Demo Plugin",
            "trigger": "demo_plugin",
            "llm_command": "demo_plugin",
            "settings": {},
            "access_control": {},
        },
    )

    assert manager.plugins["demo_plugin"].type == "delegate"


def test_reload_plugin_records_type_mismatch_warning(tmp_path: Path) -> None:
    _write_plugin(tmp_path, config_type="direct", class_type="delegate")
    manager = PluginManager(plugin_dir=str(tmp_path))
    manager.load_plugins()
    manager.load_errors = []

    assert manager.reload_plugin("demo_plugin")

    assert manager.plugins["demo_plugin"].type == "direct"
    assert any(
        error.get("code") == "plugin_type_mismatch"
        and error.get("source") == "reload_plugin"
        for error in manager.load_errors
    )
