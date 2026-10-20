import io
import json
import zipfile
from pathlib import Path

import pytest

from modules.plugin_manager import PluginManager
from modules.plugin_market import (
    PluginMarketError,
    PluginMarketFetchError,
    install_from_github,
    install_listing,
    is_safe_plugin_id,
    market_status,
    merge_plugin_config,
    parse_catalog,
    uninstall_plugin,
)


def _listing(**overrides):
    listing = {
        "id": "dice_plugin",
        "name": "骰子",
        "trigger": "dice_roll",
        "version": "1.0.0",
        "type": "direct",
        "summary": "掷骰",
        "path": "dice_plugin",
    }
    listing.update(overrides)
    return listing


def _plugin_files(folder: Path, trigger: str, version: str, default_name: str = "原名") -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "plugin.py").write_text("class Plugin:\n    pass\n", encoding="utf-8")
    (folder / "config.json").write_text(
        json.dumps(
            {
                "name": default_name,
                "trigger": trigger,
                "version": version,
                "settings": {"nickname": {"type": "text", "default": default_name}},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_parse_catalog_keeps_safe_entries_only():
    listings = parse_catalog(
        {
            "schema": 1,
            "plugins": [
                _listing(),
                {"id": "../evil", "path": "../evil", "trigger": "evil"},
                {"id": "dice_plugin", "path": "other", "trigger": "dice_roll"},
                {"id": "timer", "path": "timer", "trigger": ""},
            ],
        }
    )

    assert [item["id"] for item in listings] == ["dice_plugin"]


def test_parse_catalog_rejects_unknown_schema():
    with pytest.raises(PluginMarketError):
        parse_catalog({"schema": 2, "plugins": []})


def test_install_downloads_one_plugin_and_update_keeps_settings(tmp_path: Path):
    installed = tmp_path / "installed"
    _plugin_files(installed / "dice_plugin", "dice_roll", "1.0.0", "我的骰子")
    tree = [
        {"path": "dice_plugin", "type": "tree"},
        {"path": "dice_plugin/plugin.py", "type": "blob", "size": 20},
        {"path": "dice_plugin/config.json", "type": "blob", "size": 80},
        {"path": "dice_plugin/../outside.txt", "type": "blob", "size": 4},
        {"path": "other/plugin.py", "type": "blob", "size": 4},
    ]
    files = {
        "dice_plugin/plugin.py": b"class Plugin:\n    pass\n",
        "dice_plugin/config.json": json.dumps(
            {
                "name": "骰子",
                "trigger": "dice_roll",
                "version": "1.1.0",
                "settings": {"nickname": {"type": "text", "default": "新名字"}},
            },
            ensure_ascii=False,
        ).encode("utf-8"),
    }

    target = install_listing(
        _listing(version="1.1.0"),
        installed_root=str(installed),
        bundled_ids=set(),
        bundled_triggers=set(),
        fetch_tree=lambda: tree,
        fetch_bytes=lambda path: files[path],
    )

    payload = json.loads((Path(target) / "config.json").read_text(encoding="utf-8"))
    assert payload["version"] == "1.1.0"
    assert payload["settings"]["nickname"]["default"] == "我的骰子"
    assert not (installed / "outside.txt").exists()
    assert market_status(
        _listing(version="1.1.0"),
        bundled_ids=set(),
        bundled_triggers=set(),
        installed_root=str(installed),
    ) == "installed"


def test_install_from_github_uses_zip_when_file_listing_is_blocked(tmp_path: Path):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("Ren-bot-plugin-main/dice_plugin/plugin.py", "class Plugin:\n    pass\n")
        archive.writestr(
            "Ren-bot-plugin-main/dice_plugin/config.json",
            json.dumps({"name": "骰子", "trigger": "dice_roll", "version": "1.0.0"}),
        )
        archive.writestr("Ren-bot-plugin-main/other/plugin.py", "class Plugin:\n    pass\n")
    blob = buffer.getvalue()

    def get_bytes(url, timeout=30):
        if "git/trees" in url or "raw.githubusercontent.com" in url:
            raise PluginMarketFetchError("blocked")
        return blob

    target = install_from_github(
        _listing(),
        installed_root=str(tmp_path / "installed"),
        bundled_ids=set(),
        bundled_triggers=set(),
        get_bytes=get_bytes,
    )

    assert (Path(target) / "plugin.py").is_file()
    assert not (Path(target).parent / "other").exists()


def test_install_refuses_plugin_already_shipped_with_the_app(tmp_path: Path):
    with pytest.raises(PluginMarketError, match="已经在主程序里"):
        install_listing(
            _listing(),
            installed_root=str(tmp_path),
            bundled_ids={"dice_plugin"},
            bundled_triggers=set(),
            fetch_tree=lambda: [],
            fetch_bytes=lambda path: b"",
        )


def test_uninstall_only_removes_the_market_copy(tmp_path: Path):
    installed = tmp_path / "installed"
    _plugin_files(installed / "dice_plugin", "dice_roll", "1.0.0")

    uninstall_plugin("dice_plugin", str(installed))

    assert not (installed / "dice_plugin").exists()
    with pytest.raises(PluginMarketError):
        uninstall_plugin("../dice_plugin", str(installed))


def test_market_status_marks_builtin_and_update(tmp_path: Path):
    installed = tmp_path / "installed"
    _plugin_files(installed / "dice_plugin", "dice_roll", "1.0.0")

    assert (
        market_status(
            _listing(version="1.2.0"),
            bundled_ids=set(),
            bundled_triggers=set(),
            installed_root=str(installed),
        )
        == "update"
    )
    assert (
        market_status(
            _listing(),
            bundled_ids=set(),
            bundled_triggers={"dice_roll"},
            installed_root=str(installed),
        )
        == "builtin"
    )
    assert is_safe_plugin_id("dice_plugin")
    assert not is_safe_plugin_id(".hidden")


def test_merge_plugin_config_preserves_existing_defaults():
    merged = merge_plugin_config(
        {"version": "1.0.0", "settings": {"nickname": {"default": "旧"}}},
        {"version": "1.2.0", "settings": {"nickname": {"default": "新"}, "limit": {"default": 3}}},
    )

    assert merged["version"] == "1.2.0"
    assert merged["settings"]["nickname"]["default"] == "旧"
    assert merged["settings"]["limit"]["default"] == 3


def test_manager_loads_market_plugins_without_replacing_builtin(tmp_path: Path):
    bundled = tmp_path / "plugins"
    market = tmp_path / "installed"
    _plugin_files(bundled / "dice_plugin", "dice_roll", "1.0.0", "内置")
    _plugin_files(market / "dice_plugin", "dice_roll", "9.0.0", "市场")
    _plugin_files(market / "timer", "timer", "1.0.0", "计时")
    manager = PluginManager(plugin_dir=str(bundled), extra_plugin_dirs=[str(market)])

    manager.load_plugins()

    assert manager.plugins["dice_roll"].name == "内置"
    assert manager.plugins["timer"].name == "计时"
    assert Path(manager.plugin_roots["timer"]).parent == market.resolve()

    assert manager.save_plugin_config(
        "timer",
        {
            "name": "计时器",
            "trigger": "timer",
            "version": "1.0.0",
            "type": "direct",
            "settings": {},
        },
    )
    saved = json.loads((market / "timer" / "config.json").read_text(encoding="utf-8"))
    assert saved["name"] == "计时器"
    bundled_saved = json.loads((bundled / "dice_plugin" / "config.json").read_text(encoding="utf-8"))
    assert bundled_saved["name"] == "内置"
