import importlib.util
import json
import sys
from pathlib import Path

import pytest


PLUGIN_DIR = Path("plugins/code_agent")


def load_plugin_class():
    spec = importlib.util.spec_from_file_location(
        "test_code_agent_plugin", PLUGIN_DIR / "plugin.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.Plugin


def test_code_agent_config_is_direct_and_owner_only_for_remote():
    config = json.loads((PLUGIN_DIR / "config.json").read_text(encoding="utf-8-sig"))

    assert config["trigger"] == "code_agent"
    assert config["type"] == "direct"
    assert config["access_control"]["allow_local"] is True
    assert config["access_control"]["allow_remote_qq"] is True
    assert config["access_control"]["allow_qq_owner"] is True
    assert config["access_control"]["allow_qq_others"] is False
    assert "codex" in config["aliases"]


def test_code_agent_handles_obvious_natural_language_only():
    Plugin = load_plugin_class()
    plugin = Plugin()

    assert plugin.should_handle_direct("让 Codex 分析这个项目为什么启动失败", {}, "codex")
    assert plugin.should_handle_direct("用 Claude Code 修改 README", {}, "claude code")
    assert plugin.should_handle_direct("让 cc 看一下报错不要改文件", {}, "cc")
    assert plugin.should_handle_direct("调用 Codex 看看这个项目", {}, "codex")
    assert plugin.should_handle_direct("用 Codex 看看这个项目", {}, "codex")
    assert plugin.should_handle_direct("让 Codex 接手这个项目", {}, "codex")
    assert plugin.should_handle_direct("让 Codex 帮我画一张丰川祥子的图", {}, "codex")
    assert not plugin.should_handle_direct("我喜欢 codex 这个名字", {}, "codex")
    assert not plugin.should_handle_direct("普通聊天", {}, "code_agent")


def test_code_agent_declares_natural_capabilities():
    Plugin = load_plugin_class()
    plugin = Plugin()

    capabilities = plugin.get_capabilities()

    assert [cap.id for cap in capabilities] == [
        "code_agent.codex_task",
        "code_agent.claude_task",
    ]


def test_code_agent_capability_matches_code_task_and_drawing():
    Plugin = load_plugin_class()
    plugin = Plugin()
    capabilities = plugin.get_capabilities()

    codex_match = capabilities[0].match("让 Codex 分析这个项目为什么启动失败", {})
    draw_match = capabilities[0].match("让 Codex 帮我画一张丰川祥子的图", {})
    claude_match = capabilities[1].match("用 Claude Code 修改 README", {})

    assert codex_match is not None
    assert codex_match.plugin == "code_agent"
    assert codex_match.args == {"provider": "codex_cli", "action": "analyze"}
    assert draw_match is not None
    assert draw_match.args == {"provider": "codex_cli", "action": "analyze"}
    assert claude_match is not None
    assert claude_match.args == {"provider": "claude_code", "action": "modify"}


class FakeRunner:
    def __init__(self):
        self.calls = []

    async def __call__(self, request):
        self.calls.append(request)
        return type(
            "Result",
            (),
            {
                "ok": True,
                "stdout": "analysis ok",
                "stderr": "",
                "exit_code": 0,
                "duration_sec": 1.2,
                "command_preview": "codex exec",
            },
        )()


@pytest.mark.asyncio
async def test_code_agent_analyze_runs_with_exec_permission(tmp_path):
    Plugin = load_plugin_class()
    runner = FakeRunner()
    plugin = Plugin(runner=runner, discoverer=lambda provider: f"{provider} {{prompt_stdin}}")

    result = await plugin.run(
        f"analyze ||| codex_cli ||| {tmp_path} ||| 检查启动失败",
        {"allow_exec": True},
    )

    assert "analysis ok" in result
    assert runner.calls[0].provider == "codex_cli"
    assert runner.calls[0].allow_write is False
    assert runner.calls[0].allow_exec is True


@pytest.mark.asyncio
async def test_code_agent_analyze_without_exec_permission_returns_clear_message(tmp_path):
    Plugin = load_plugin_class()
    plugin = Plugin(discoverer=lambda provider: "codex {prompt_stdin}")

    result = await plugin.run(
        f"analyze ||| codex_cli ||| {tmp_path} ||| 检查启动失败",
        {},
    )

    assert "允许执行命令" in result
    assert "代码助手" in result


@pytest.mark.asyncio
async def test_code_agent_modify_requires_confirmation(tmp_path):
    Plugin = load_plugin_class()
    runner = FakeRunner()
    plugin = Plugin(runner=runner, discoverer=lambda provider: f"{provider} {{prompt_stdin}}")

    result = await plugin.run(
        f"modify ||| claude_code ||| {tmp_path} ||| 修改 README",
        {"allow_exec": True, "allow_write": True},
    )

    assert result["__agent_result__"] == "confirmation_required"
    assert result["trigger"] == "code_agent"
    assert result["payload"]["provider"] == "claude_code"
    assert runner.calls == []

    confirmed = await plugin.confirm_agent_action(result["payload"], {"allow_exec": True})

    assert "analysis ok" in confirmed
    assert runner.calls[0].allow_write is True


@pytest.mark.asyncio
async def test_code_agent_confirm_does_not_default_to_exec_permission(tmp_path):
    Plugin = load_plugin_class()
    runner = FakeRunner()
    plugin = Plugin(runner=runner, discoverer=lambda provider: f"{provider} {{prompt_stdin}}")
    payload = {
        "provider": "codex_cli",
        "cwd": str(tmp_path),
        "prompt": "修改 README",
        "command_template": "codex_cli {prompt_stdin}",
        "timeout_sec": 300,
        "allow_write": True,
        "task_id": "t1",
    }

    await plugin.confirm_agent_action(payload, {})

    assert runner.calls[0].allow_exec is False


@pytest.mark.asyncio
async def test_code_agent_natural_language_defaults_to_analyze(tmp_path):
    Plugin = load_plugin_class()
    runner = FakeRunner()
    plugin = Plugin(runner=runner, discoverer=lambda provider: f"{provider} {{prompt_stdin}}")

    result = await plugin.run(
        f"让 Codex 分析 {tmp_path} 为什么启动失败",
        {"allow_exec": True},
    )

    assert "analysis ok" in result
    assert runner.calls[0].provider == "codex_cli"
    assert runner.calls[0].cwd == str(tmp_path)


@pytest.mark.asyncio
async def test_code_agent_defaults_cwd_to_context_app_root(tmp_path):
    Plugin = load_plugin_class()
    runner = FakeRunner()
    plugin = Plugin(runner=runner, discoverer=lambda provider: f"{provider} {{prompt_stdin}}")

    result = await plugin.run(
        "让 Codex 分析为什么启动失败",
        {"allow_exec": True, "app_root": str(tmp_path)},
    )

    assert "analysis ok" in result
    assert runner.calls[0].cwd == str(tmp_path)
