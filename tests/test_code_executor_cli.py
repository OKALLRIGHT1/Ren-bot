import json
from pathlib import Path

import pytest

from plugins.code_executor.plugin import Plugin as CodeExecutorPlugin


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
                "stdout": "42",
                "stderr": "",
                "exit_code": 0,
                "duration_sec": 0.5,
                "command_preview": "codex exec",
            },
        )()


def test_config_describes_external_cli():
    config = json.loads(
        Path("plugins/code_executor/config.json").read_text(encoding="utf-8-sig")
    )
    assert config["trigger"] == "code"
    assert config["llm_command"] == "execute_code"
    assert "Codex" in config["description"] or "Claude" in config["description"]
    assert config["access_control"]["allow_qq_others"] is False


@pytest.mark.asyncio
async def test_code_executor_requires_confirm_or_gate_flag():
    runner = FakeRunner()
    plugin = CodeExecutorPlugin(
        runner=runner, discoverer=lambda provider: f"{provider} {{prompt_stdin}}"
    )
    plugin.ENABLED = True
    result = await plugin.run("print(1+1)", {"source": "text_input"})
    assert isinstance(result, dict)
    assert result["__agent_result__"] == "confirmation_required"
    assert runner.calls == []


@pytest.mark.asyncio
async def test_code_executor_runs_external_cli_when_confirmed(tmp_path):
    runner = FakeRunner()
    plugin = CodeExecutorPlugin(
        runner=runner, discoverer=lambda provider: f"{provider} {{prompt_stdin}}"
    )
    plugin.ENABLED = True
    result = await plugin.run(
        "print(1+1)",
        {"action_confirmed": True, "code_path": str(tmp_path)},
    )
    assert "42" in result
    assert runner.calls[0].provider == "codex_cli"
    assert runner.calls[0].allow_exec is True
    assert str(tmp_path) in runner.calls[0].cwd


@pytest.mark.asyncio
async def test_code_executor_disabled_without_env():
    runner = FakeRunner()
    plugin = CodeExecutorPlugin(
        runner=runner, discoverer=lambda provider: f"{provider} {{prompt_stdin}}"
    )
    plugin.ENABLED = False
    result = await plugin.run("print(1)", {"action_confirmed": True})
    assert "未启用" in result
    assert runner.calls == []


@pytest.mark.asyncio
async def test_code_executor_missing_cli_message(tmp_path):
    plugin = CodeExecutorPlugin(runner=FakeRunner(), discoverer=lambda provider: "")
    plugin.ENABLED = True
    result = await plugin.run(
        "print(1)",
        {"action_confirmed": True, "code_path": str(tmp_path)},
    )
    assert "未找到" in result


@pytest.mark.asyncio
async def test_code_executor_confirm_agent_action(tmp_path):
    runner = FakeRunner()
    plugin = CodeExecutorPlugin(
        runner=runner, discoverer=lambda provider: f"{provider} {{prompt_stdin}}"
    )
    plugin.ENABLED = True
    reply = await plugin.confirm_agent_action(
        {"args": "print(2)", "mode": "gate_rerun"},
        {"code_path": str(tmp_path)},
    )
    assert "42" in reply
    assert len(runner.calls) == 1
