from modules.plugin_manager import PluginManager
from modules.plugin_security_audit import (
    build_plugin_security_matrix,
    summarize_plugin_security_matrix,
)


def test_security_matrix_uses_access_control_defaults_for_missing_keys(tmp_path) -> None:
    manager = PluginManager(plugin_dir=str(tmp_path))
    matrix = build_plugin_security_matrix(
        {
            "mcp_tools": {
                "name": "MCP Tools",
                "trigger": "mcp_tools",
                "type": "delegate",
                "access_control": {
                    "allow_local": True,
                    "allow_remote_qq": False,
                    "allow_qq_owner": False,
                    "allow_qq_others": False,
                },
            }
        },
        manager._normalize_access_control,
    )

    assert matrix[0]["trigger"] == "mcp_tools"
    assert matrix[0]["allow_group_without_at"] is False
    assert matrix[0]["allow_qq_others"] is False
    assert "code_or_skill" in matrix[0]["risk_flags"]


def test_security_summary_groups_remote_other_and_group_without_at(tmp_path) -> None:
    manager = PluginManager(plugin_dir=str(tmp_path))
    matrix = build_plugin_security_matrix(
        {
            "qq_help": {
                "name": "QQ Help",
                "trigger": "qq_help",
                "type": "direct",
                "access_control": {
                    "allow_remote_qq": True,
                    "allow_qq_owner": True,
                    "allow_qq_others": True,
                    "allow_group_without_at": True,
                },
            },
            "qq_draw": {
                "name": "QQ Draw",
                "trigger": "qq_draw",
                "type": "direct",
                "access_control": {
                    "allow_remote_qq": True,
                    "allow_qq_owner": True,
                    "allow_qq_others": False,
                    "allow_group_without_at": False,
                },
            },
        },
        manager._normalize_access_control,
    )

    summary = summarize_plugin_security_matrix(matrix)

    assert summary["other_qq_plugins"] == ["qq_help"]
    assert summary["group_without_at_plugins"] == ["qq_help"]
    assert summary["owner_remote_high_risk_plugins"] == ["qq_draw"]
