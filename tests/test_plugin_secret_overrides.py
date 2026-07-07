from modules.plugin_manager import PluginManager


class _BrokenSecretStore:
    def get_all_for_plugin(self, plugin_trigger):
        raise OSError("dpapi unavailable")


def test_secret_override_failure_keeps_plugin_config_loadable():
    manager = PluginManager(plugin_dir="plugins")
    manager.secret_store = _BrokenSecretStore()
    config = {
        "trigger": "magic_daily",
        "settings": {
            "api_token": {
                "type": "secret",
                "default": "",
            },
            "render_mode": {
                "type": "choice",
                "default": "image",
            },
        },
    }

    result = manager._apply_secret_overrides("magic_daily", config)

    assert result["settings"]["api_token"]["default"] == ""
    assert result["settings"]["render_mode"]["default"] == "image"
    assert manager.load_errors == [
        {
            "plugin": "magic_daily",
            "error": "secret_override_failed: dpapi unavailable",
        }
    ]


def test_plugin_load_continues_when_secret_override_fails(tmp_path):
    plugin_dir = tmp_path / "plugins"
    sample = plugin_dir / "sample_daily"
    sample.mkdir(parents=True)
    (sample / "config.json").write_text(
        """
        {
          "name": "Sample Daily",
          "trigger": "magic_daily",
          "type": "direct",
          "aliases": ["/日报"],
          "settings": {
            "api_token": {"type": "secret", "default": ""}
          }
        }
        """,
        encoding="utf-8",
    )
    (sample / "plugin.py").write_text(
        """
class Plugin:
    async def run(self, args, ctx):
        return "daily-ok"
""",
        encoding="utf-8",
    )
    manager = PluginManager(plugin_dir=str(plugin_dir))
    manager.secret_store = _BrokenSecretStore()

    manager.load_plugins()

    assert "magic_daily" in manager.plugins
    assert "/日报" in manager.direct_map
    assert manager.direct_map["/日报"] is manager.plugins["magic_daily"]
    assert manager.load_errors == [
        {
            "plugin": "magic_daily",
            "error": "secret_override_failed: dpapi unavailable",
        }
    ]
