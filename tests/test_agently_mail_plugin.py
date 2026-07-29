import asyncio
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

from modules.plugin_model_gateway import PluginModelCallResult


PLUGIN_DIR = Path("plugins/agently_mail")


def load_plugin_class():
    spec = importlib.util.spec_from_file_location(
        "test_agently_mail_plugin", PLUGIN_DIR / "plugin.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.Plugin


def test_agently_mail_config_allows_qq_owner_direct_tool():
    config = json.loads((PLUGIN_DIR / "config.json").read_text(encoding="utf-8-sig"))

    assert config["trigger"] == "agently_mail"
    assert config["type"] == "direct"
    assert config["access_control"]["allow_local"] is True
    assert config["access_control"]["allow_remote_qq"] is True
    assert config["access_control"]["allow_qq_owner"] is True
    assert config["access_control"]["allow_qq_others"] is False
    assert "邮件" in config["aliases"]
    assert config["settings"]["model_queue"]["purpose"] == ["tool_reasoning"]


@pytest.mark.asyncio
async def test_agently_mail_intent_uses_selected_main_model():
    Plugin = load_plugin_class()
    plugin = Plugin()
    plugin.settings = {"model_queue": {"default": ["reason-a"]}}

    class FakeGateway:
        def __init__(self):
            self.calls = []

        async def invoke_text(self, messages, **kwargs):
            self.calls.append((messages, kwargs))
            return PluginModelCallResult(
                ok=True,
                text=json.dumps(
                    {
                        "action": "send",
                        "to": "foo@example.com",
                        "subject": "Hi",
                        "body": "Hello",
                        "confidence": 0.9,
                    }
                ),
                model_id="reason-a",
            )

    gateway = FakeGateway()
    result = await plugin._resolve_intent_with_llm(
        "给 foo@example.com 发邮件",
        {"model_gateway": gateway},
    )

    assert result["action"] == "send"
    assert gateway.calls[0][1]["selected_ids"] == ["reason-a"]


def test_agently_mail_outer_timeout_covers_intent_and_cli_steps():
    config = json.loads((PLUGIN_DIR / "config.json").read_text(encoding="utf-8-sig"))

    cli_timeout = int(config["settings"]["request_timeout_sec"]["default"])
    intent_timeout = int(config["settings"]["intent_timeout_sec"]["default"])

    assert intent_timeout >= 120
    # Natural-language send first calls the LLM intent resolver and then agently-cli
    # for a confirmation token, so the plugin-level timeout must cover both steps.
    assert config["timeout_sec"] >= cli_timeout + intent_timeout + 10


def test_agently_mail_plugin_contract():
    Plugin = load_plugin_class()
    plugin = Plugin()

    assert plugin.type == "direct"
    assert callable(plugin.should_handle_direct)
    assert callable(plugin.run)


def test_agently_mail_parses_llm_json_fence():
    Plugin = load_plugin_class()
    plugin = Plugin()

    parsed = plugin._parse_llm_json(
        '```json\n{"action":"send","to":"a@example.com","subject":"Hi","body":"Hello","confidence":0.8}\n```'
    )

    assert parsed["action"] == "send"
    assert parsed["to"] == "a@example.com"


def test_agently_mail_natural_language_requires_clear_mail_intent():
    Plugin = load_plugin_class()
    plugin = Plugin()

    assert plugin.should_handle_direct("我最近收到了哪些邮件？", {"source": "text_input"}, "邮件")
    assert plugin.should_handle_direct("查一下邮箱", {"source": "text_input"}, "邮箱")
    assert plugin.should_handle_direct("发一封上海本周天气邮件到123456789@qq.com", {"source": "qq_gateway"}, "邮件")
    assert plugin.should_handle_direct("给foo@example.com邮箱发送问候", {"source": "qq_gateway"}, "邮件")
    assert not plugin.should_handle_direct("把这个文件邮寄给我", {"source": "text_input"}, "邮件")
    assert not plugin.should_handle_direct("邮件这个词只是举例", {"source": "text_input"}, "邮件")


def test_agently_mail_handles_named_persona_mail_requests():
    Plugin = load_plugin_class()
    plugin = Plugin()

    assert plugin.should_handle_direct(
        "让小祥发送邮件到foo@example.com问候一下",
        {"source": "qq_gateway"},
        "邮件",
    )
    assert plugin.should_handle_direct(
        "让小祥回复邮件 msg_123",
        {"source": "qq_gateway"},
        "邮件",
    )


class FakeCliRunner:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def __call__(self, argv, timeout_sec):
        self.calls.append((list(argv), timeout_sec))
        return self.responses.pop(0)


class FakeIntentResolver:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    async def __call__(self, text, ctx):
        self.calls.append((text, dict(ctx or {})))
        return dict(self.payload)


class SlowIntentResolver:
    def __init__(self, delay):
        self.delay = delay
        self.calls = []

    async def __call__(self, text, ctx):
        self.calls.append((text, dict(ctx or {})))
        await asyncio.sleep(self.delay)
        return {}


class FakePersonaResolver:
    def __init__(self):
        self.actor = {
            "character_id": "sakiko",
            "name": "丰川祥子",
            "prompt": "你是丰川祥子。",
            "matched_text": "小祥",
            "matched_by": "alias",
        }

    def extract_leading_actor(self, text):
        raw = str(text or "")
        if raw.startswith("让小祥"):
            return self.actor, raw.replace("让小祥", "", 1).strip()
        if raw.startswith("让丰川祥子"):
            return {
                **self.actor,
                "matched_text": "丰川祥子",
                "matched_by": "name",
            }, raw.replace("让丰川祥子", "", 1).strip()
        return None, raw


def plugin_with_runner(responses, *, intent_payload=None, persona_resolver=None):
    Plugin = load_plugin_class()
    runner = FakeCliRunner(responses)
    plugin = Plugin(
        cli_runner=runner,
        intent_resolver=FakeIntentResolver(intent_payload) if intent_payload is not None else None,
        persona_resolver=persona_resolver,
    )
    plugin.settings = {
        "cli_path": {"default": "agently-cli"},
        "default_limit": {"default": 10},
        "max_body_chars": {"default": 120},
        "request_timeout_sec": {"default": 7},
        "intent_timeout_sec": {"default": 2},
    }
    return plugin, runner


@pytest.mark.asyncio
async def test_recent_mail_lists_inbox():
    plugin, runner = plugin_with_runner(
        [
            {
                "returncode": 0,
                "stdout": json.dumps(
                    {
                        "ok": True,
                        "data": {
                            "data": [
                                {
                                    "message_id": "msg_1",
                                    "subject": "日报",
                                    "from": {
                                        "name": "Alice",
                                        "email": "a@example.com",
                                    },
                                    "created_at": "2026-06-26T08:00:00Z",
                                    "snippet": "今天的内容",
                                    "is_read": False,
                                }
                            ],
                            "pagination": {"has_more": False},
                        },
                    },
                    ensure_ascii=False,
                ),
                "stderr": "",
            }
        ]
    )

    result = await plugin.run("我最近收到了哪些邮件？", {"source": "text_input"})

    assert "最近邮件" in result
    assert "msg_1" in result
    assert "日报" in result
    assert runner.calls[0][0] == [
        "agently-cli",
        "message",
        "+list",
        "--dir",
        "inbox",
        "--limit",
        "10",
    ]


@pytest.mark.asyncio
async def test_recent_mail_returns_gateway_image_card_for_qq():
    plugin, runner = plugin_with_runner(
        [
            {
                "returncode": 0,
                "stdout": json.dumps(
                    {
                        "ok": True,
                        "data": {
                            "data": [
                                {
                                    "message_id": "msg_1",
                                    "subject": "日报",
                                    "from": {"name": "Alice", "email": "a@example.com"},
                                    "created_at": "2026-06-26T08:00:00Z",
                                    "snippet": "今天的内容",
                                    "is_read": False,
                                },
                                {
                                    "message_id": "msg_2",
                                    "subject": "会议纪要",
                                    "from": {"name": "", "email": "b@example.com"},
                                    "created_at": "2026-06-26T09:00:00Z",
                                    "snippet": "会议重点和后续安排",
                                    "is_read": True,
                                },
                            ],
                            "pagination": {"has_more": False},
                        },
                    },
                    ensure_ascii=False,
                ),
                "stderr": "",
            }
        ]
    )

    result = await plugin.run(
        "我最近收到了哪些邮件？",
        {"source": "qq_gateway", "channel_meta": {"session_id": "private:1"}},
    )

    assert result["__type__"] == "gateway_image"
    assert result["image_path"].endswith(".png")
    assert os.path.exists(result["image_path"])
    assert "最近邮件：共 2 封，未读 1 封" in result["post_send_text"]
    os.remove(result["image_path"])


@pytest.mark.asyncio
async def test_search_mail_uses_keyword():
    plugin, runner = plugin_with_runner(
        [
            {
                "returncode": 0,
                "stdout": json.dumps(
                    {"ok": True, "data": {"data": [], "pagination": {"has_more": False}}},
                    ensure_ascii=False,
                ),
                "stderr": "",
            }
        ]
    )

    result = await plugin.run("搜索邮件 账单", {"source": "text_input"})

    assert "没有找到邮件" in result
    assert runner.calls[0][0] == [
        "agently-cli",
        "message",
        "+search",
        "--q",
        "账单",
        "--limit",
        "10",
    ]


@pytest.mark.asyncio
async def test_read_mail_truncates_body():
    plugin, runner = plugin_with_runner(
        [
            {
                "returncode": 0,
                "stdout": json.dumps(
                    {
                        "ok": True,
                        "data": {
                            "message_id": "msg_1",
                            "subject": "长邮件",
                            "from": {
                                "name": "Alice",
                                "email": "a@example.com",
                            },
                            "created_at": "2026-06-26T08:00:00Z",
                            "body": "A" * 200,
                            "attachments": [],
                        },
                    },
                    ensure_ascii=False,
                ),
                "stderr": "",
            }
        ]
    )

    result = await plugin.run("读邮件 msg_1", {"source": "text_input"})

    assert "长邮件" in result
    assert "正文已截断" in result
    assert runner.calls[0][0] == ["agently-cli", "message", "+read", "--id", "msg_1"]


@pytest.mark.asyncio
async def test_send_returns_runtime_confirmation_request():
    plugin, runner = plugin_with_runner(
        [
            {
                "returncode": 0,
                "stdout": json.dumps(
                    {
                        "ok": True,
                        "data": {
                            "confirmation_required": True,
                            "confirmation_token": "ctk_123",
                            "expires_in": 300,
                            "summary": {
                                "action": "send",
                                "to": ["bob@example.com"],
                                "subject": "Hi",
                                "attachment_count": 0,
                            },
                        },
                    },
                    ensure_ascii=False,
                ),
                "stderr": "",
            }
        ]
    )

    result = await plugin.run(
        "发邮件 to=bob@example.com subject=Hi body=Hello",
        {"source": "text_input"},
    )

    assert result["__agent_result__"] == "confirmation_required"
    assert result["trigger"] == "agently_mail"
    assert result["payload"]["confirmation_token"] == "ctk_123"
    assert "--confirmation-token" not in runner.calls[0][0]


@pytest.mark.asyncio
async def test_send_natural_language_with_missing_body_asks_for_body():
    plugin, runner = plugin_with_runner([])
    plugin.settings["llm_intent_enabled"] = {"default": False}

    result = await plugin.run(
        "发一封上海本周天气邮件到123456789@qq.com",
        {"source": "qq_gateway"},
    )

    assert "正文(body=)" in result
    assert "to=123456789@qq.com" in result
    assert "subject=上海本周天气" in result
    assert runner.calls == []


@pytest.mark.asyncio
async def test_send_uses_llm_intent_resolver_for_natural_language_body():
    plugin, runner = plugin_with_runner(
        [
            {
                "returncode": 0,
                "stdout": json.dumps(
                    {
                        "ok": True,
                        "data": {
                            "confirmation_required": True,
                            "confirmation_token": "ctk_456",
                            "summary": {
                                "action": "send",
                                "to": ["123456789@qq.com"],
                                "subject": "上海本周天气",
                            },
                        },
                    },
                    ensure_ascii=False,
                ),
                "stderr": "",
            }
        ],
        intent_payload={
            "action": "send",
            "to": "123456789@qq.com",
            "subject": "上海本周天气",
            "body": "上海本周天气以多云为主，气温约 25-32 度。",
            "confidence": 0.9,
        },
    )

    result = await plugin.run(
        "发一封上海本周天气邮件到123456789@qq.com",
        {"source": "qq_gateway"},
    )

    assert result["__agent_result__"] == "confirmation_required"
    assert runner.calls[0][0] == [
        "agently-cli",
        "message",
        "+send",
        "--to",
        "123456789@qq.com",
        "--subject",
        "上海本周天气",
        "--body",
        "上海本周天气以多云为主，气温约 25-32 度。",
    ]


@pytest.mark.asyncio
async def test_send_greeting_to_mailbox_uses_llm_generated_persona_body():
    plugin, runner = plugin_with_runner(
        [
            {
                "returncode": 0,
                "stdout": json.dumps(
                    {
                        "ok": True,
                        "data": {
                            "confirmation_required": True,
                            "confirmation_token": "ctk_greet",
                            "summary": {
                                "action": "send",
                                "to": ["foo@example.com"],
                                "subject": "问候",
                            },
                        },
                    },
                    ensure_ascii=False,
                ),
                "stderr": "",
            }
        ],
        intent_payload={
            "action": "send",
            "to": "foo@example.com",
            "subject": "问候",
            "body": "愿今天也有温柔的风经过你窗前。",
            "confidence": 0.9,
        },
    )

    result = await plugin.run(
        "给foo@example.com邮箱发送问候",
        {"source": "qq_gateway"},
    )

    assert result["__agent_result__"] == "confirmation_required"
    assert runner.calls[0][0] == [
        "agently-cli",
        "message",
        "+send",
        "--to",
        "foo@example.com",
        "--subject",
        "问候",
        "--body",
        "愿今天也有温柔的风经过你窗前。",
    ]


@pytest.mark.asyncio
async def test_send_with_named_persona_uses_tool_actor_context():
    plugin, runner = plugin_with_runner(
        [
            {
                "returncode": 0,
                "stdout": json.dumps(
                    {
                        "ok": True,
                        "data": {
                            "confirmation_required": True,
                            "confirmation_token": "ctk_sakiko",
                            "summary": {
                                "action": "send",
                                "to": ["foo@example.com"],
                                "subject": "问候",
                            },
                        },
                    },
                    ensure_ascii=False,
                ),
                "stderr": "",
            }
        ],
        intent_payload={
            "action": "send",
            "to": "foo@example.com",
            "subject": "问候",
            "body": "愿今日的风也能优雅地抵达您的窗前，desuwa。",
            "confidence": 0.9,
        },
        persona_resolver=FakePersonaResolver(),
    )

    resolver = plugin._intent_resolver
    result = await plugin.run(
        "让小祥发送邮件到 foo@example.com 问候一下",
        {"source": "qq_gateway"},
    )

    assert result["__agent_result__"] == "confirmation_required"
    assert "丰川祥子" in result["summary"]
    assert result["payload"]["actor"]["name"] == "丰川祥子"
    assert resolver.calls[0][1]["tool_actor"]["name"] == "丰川祥子"
    assert runner.calls[0][0] == [
        "agently-cli",
        "message",
        "+send",
        "--to",
        "foo@example.com",
        "--subject",
        "问候",
        "--body",
        "愿今日的风也能优雅地抵达您的窗前，desuwa。",
    ]


@pytest.mark.asyncio
async def test_send_with_named_persona_handles_user_phrase_fa_yi_feng():
    plugin, runner = plugin_with_runner(
        [
            {
                "returncode": 0,
                "stdout": json.dumps(
                    {
                        "ok": True,
                        "data": {
                            "confirmation_required": True,
                            "confirmation_token": "ctk_mujica",
                            "summary": {
                                "action": "send",
                                "to": ["123456789@qq.com"],
                                "subject": "Mujica的假面舞会邀请",
                            },
                        },
                    },
                    ensure_ascii=False,
                ),
                "stderr": "",
            }
        ],
        intent_payload={
            "action": "send",
            "to": "123456789@qq.com",
            "subject": "Mujica的假面舞会邀请",
            "body": "诚邀您参加Mujica的假面舞会，愿夜色与面具一同见证这场优雅的相逢，desuwa。",
            "confidence": 0.9,
        },
        persona_resolver=FakePersonaResolver(),
    )

    result = await plugin.run(
        "让小祥发一封邮件到123456789@qq.com，邀请她参见mujica的假面舞会",
        {"source": "qq_gateway"},
    )

    assert result["__agent_result__"] == "confirmation_required"
    assert runner.calls[0][0] == [
        "agently-cli",
        "message",
        "+send",
        "--to",
        "123456789@qq.com",
        "--subject",
        "Mujica的假面舞会邀请",
        "--body",
        "诚邀您参加Mujica的假面舞会，愿夜色与面具一同见证这场优雅的相逢，desuwa。",
    ]


@pytest.mark.asyncio
async def test_send_returns_intent_timeout_before_outer_plugin_timeout():
    Plugin = load_plugin_class()
    runner = FakeCliRunner([])
    slow_resolver = SlowIntentResolver(delay=0.2)
    plugin = Plugin(
        cli_runner=runner,
        intent_resolver=slow_resolver,
        persona_resolver=FakePersonaResolver(),
    )
    plugin.settings = {
        "cli_path": {"default": "agently-cli"},
        "default_limit": {"default": 10},
        "max_body_chars": {"default": 120},
        "request_timeout_sec": {"default": 7},
        "intent_timeout_sec": {"default": 0.05},
    }

    result = await plugin.run(
        "让小祥发送邮件到 foo@example.com 问候一下",
        {"source": "qq_gateway"},
    )

    assert "草稿生成超时" in result
    assert runner.calls == []


@pytest.mark.asyncio
async def test_reply_with_named_persona_reads_source_and_generates_body():
    plugin, runner = plugin_with_runner(
        [
            {
                "returncode": 0,
                "stdout": json.dumps(
                    {
                        "ok": True,
                        "data": {
                            "message_id": "msg_123",
                            "subject": "近况",
                            "from": {"name": "Alice", "email": "alice@example.com"},
                            "body": "最近还好吗？",
                            "attachments": [],
                        },
                    },
                    ensure_ascii=False,
                ),
                "stderr": "",
            },
            {
                "returncode": 0,
                "stdout": json.dumps(
                    {
                        "ok": True,
                        "data": {
                            "confirmation_required": True,
                            "confirmation_token": "ctk_reply",
                            "summary": {"action": "reply", "id": "msg_123"},
                        },
                    },
                    ensure_ascii=False,
                ),
                "stderr": "",
            },
        ],
        intent_payload={
            "action": "reply",
            "id": "msg_123",
            "body": "承蒙关心，我一切尚好。愿您也安好，desuwa。",
            "confidence": 0.9,
        },
        persona_resolver=FakePersonaResolver(),
    )

    resolver = plugin._intent_resolver
    result = await plugin.run(
        "让小祥回复邮件 msg_123",
        {"source": "qq_gateway"},
    )

    assert result["__agent_result__"] == "confirmation_required"
    assert "丰川祥子" in result["summary"]
    assert result["payload"]["actor"]["name"] == "丰川祥子"
    assert runner.calls[0][0] == ["agently-cli", "message", "+read", "--id", "msg_123"]
    assert runner.calls[1][0] == [
        "agently-cli",
        "message",
        "+reply",
        "--id",
        "msg_123",
        "--body",
        "承蒙关心，我一切尚好。愿您也安好，desuwa。",
    ]
    assert resolver.calls[0][1]["tool_actor"]["name"] == "丰川祥子"
    assert resolver.calls[0][1]["source_mail"]["subject"] == "近况"


@pytest.mark.asyncio
async def test_default_llm_intent_prompt_includes_active_character(monkeypatch):
    Plugin = load_plugin_class()
    plugin = Plugin()
    captured = {}

    class FakeCharacterManager:
        def get_active_character(self):
            return {
                "name": "万年樱",
                "prompt": "说话温柔，像守在窗边的少女。",
            }

    def fake_chat(messages, *args, **kwargs):
        captured["messages"] = messages
        return json.dumps(
            {
                "action": "send",
                "to": "foo@example.com",
                "subject": "问候",
                "body": "你好。",
                "confidence": 0.9,
            },
            ensure_ascii=False,
        )

    import modules.llm as llm_module
    import modules.character_manager as character_manager_module

    monkeypatch.setattr(llm_module, "chat_with_ai", fake_chat)
    monkeypatch.setattr(
        character_manager_module,
        "character_manager",
        FakeCharacterManager(),
    )

    result = await plugin._resolve_intent_with_llm(
        "给foo@example.com邮箱发送问候",
        {"source": "qq_gateway"},
    )

    system_prompt = captured["messages"][0]["content"]
    assert result["body"] == "你好。"
    assert "当前角色人设" in system_prompt
    assert "万年樱" in system_prompt
    assert "说话温柔" in system_prompt


@pytest.mark.asyncio
async def test_confirm_agent_action_sends_with_token():
    plugin, runner = plugin_with_runner(
        [
            {
                "returncode": 0,
                "stdout": json.dumps({"ok": True, "queued": True}, ensure_ascii=False),
                "stderr": "",
            }
        ]
    )

    reply = await plugin.confirm_agent_action(
        {
            "argv": [
                "agently-cli",
                "message",
                "+send",
                "--to",
                "bob@example.com",
                "--subject",
                "Hi",
                "--body",
                "Hello",
            ],
            "confirmation_token": "ctk_123",
            "action": "send",
        },
        {"source": "text_input"},
    )

    assert "已提交发送" in reply
    assert runner.calls[0][0][-2:] == ["--confirmation-token", "ctk_123"]


def test_agently_mail_notification_settings_exist():
    config = json.loads((PLUGIN_DIR / "config.json").read_text(encoding="utf-8-sig"))
    settings = config["settings"]

    assert settings["enable_notifications"]["default"] is True
    assert settings["notification_target_sessions"]["default"] == []
    assert settings["notification_character_name"]["default"] == "丰川祥子"
    assert settings["notification_character_name"]["type"] == "choice"
    assert settings["notification_character_name"]["choices_source"] == "characters"
    assert int(settings["notification_check_interval_sec"]["default"]) >= 30


def test_mail_list_card_renders_without_overlap_metrics():
    Plugin = load_plugin_class()
    plugin = Plugin()
    items = [
        {
            "message_id": "msg_long",
            "subject": "特别长的中文主题用来验证按像素宽度截断而不是按字符数乱切",
            "from": {"name": "爱丽丝", "email": "alice@example.com"},
            "created_at": "2026-07-28T10:00:00+08:00",
            "snippet": "摘要内容" * 20,
            "is_read": False,
        }
    ]
    image_path = plugin._render_mail_list_card("最近邮件", items, has_more=False)
    assert image_path.endswith(".png")
    assert os.path.exists(image_path)
    assert os.path.getsize(image_path) > 1000
    os.remove(image_path)


def test_mail_detail_card_renders_body():
    Plugin = load_plugin_class()
    plugin = Plugin()
    image_path = plugin._render_mail_detail_card(
        {
            "message_id": "msg_body",
            "subject": "正文卡片",
            "from": {"name": "Alice", "email": "a@example.com"},
            "created_at": "2026-07-28T10:00:00+08:00",
            "body": "第一行\n第二行是更长的中文内容，用来检查换行。" * 5,
            "attachments": [{"filename": "a.pdf"}],
        },
        body_limit=300,
    )
    assert os.path.exists(image_path)
    assert os.path.getsize(image_path) > 1000
    os.remove(image_path)


def test_mail_detail_card_strips_html_body():
    Plugin = load_plugin_class()
    plugin = Plugin()
    plain = plugin._html_to_plain_text(
        '<div style="font-family: apple-system;">你还好吗</div>'
    )
    assert plain == "你还好吗"
    assert "<div" not in plain

    image_path = plugin._render_mail_detail_card(
        {
            "message_id": "msg_html",
            "subject": "你还好吗",
            "from": {"name": "李曜同", "email": "123456789@qq.com"},
            "created_at": "2026-07-28T02:26:21Z",
            "body": '<div style="font-family:apple-system, system-ui; font-size: 14px; color: rgb(0, 0, 0); line-height: 1.43;">你还好吗</div>',
        },
        body_limit=500,
    )
    assert os.path.exists(image_path)
    # 渲染应成功；进一步靠 plain-text helper 保证不再塞 HTML 原文
    os.remove(image_path)


def test_notification_summary_merges_trailing_particle():
    Plugin = load_plugin_class()
    plugin = Plugin()
    text = plugin._normalize_notification_summary(
        "李曜同发来的邮件，标题是“你还好吗”，感觉他/她可能是在关心我的近况呢\ndesuwa"
    )
    assert "\n" not in text
    assert text.endswith("desuwa")
    assert "desuwa" in text
    # 不应再单独成行
    assert text.count("desuwa") == 1


@pytest.mark.asyncio
async def test_notification_baseline_keeps_unread_for_push(tmp_path, monkeypatch):
    Plugin = load_plugin_class()
    runner = FakeCliRunner(
        [
            {
                "returncode": 0,
                "stdout": json.dumps(
                    {
                        "ok": True,
                        "data": {
                            "data": [
                                {
                                    "message_id": "msg_unread",
                                    "subject": "未读邮件",
                                    "from": {"name": "A", "email": "a@example.com"},
                                    "created_at": "2026-07-28T09:00:00+08:00",
                                    "snippet": "unread",
                                    "is_read": False,
                                },
                                {
                                    "message_id": "msg_read",
                                    "subject": "已读邮件",
                                    "from": {"name": "B", "email": "b@example.com"},
                                    "created_at": "2026-07-28T08:00:00+08:00",
                                    "snippet": "read",
                                    "is_read": True,
                                },
                            ],
                            "pagination": {"has_more": False},
                        },
                    },
                    ensure_ascii=False,
                ),
                "stderr": "",
            },
            {
                "returncode": 0,
                "stdout": json.dumps(
                    {
                        "ok": True,
                        "data": {
                            "message_id": "msg_unread",
                            "subject": "未读邮件",
                            "from": {"name": "A", "email": "a@example.com"},
                            "created_at": "2026-07-28T09:00:00+08:00",
                            "body": "未读正文",
                            "attachments": [],
                        },
                    },
                    ensure_ascii=False,
                ),
                "stderr": "",
            },
        ]
    )
    plugin = Plugin(cli_runner=runner)
    plugin.settings = {
        "enable_notifications": True,
        "notification_target_sessions": ["private:123456789"],
        "notification_character_name": "丰川祥子",
        "notification_poll_limit": 10,
        "request_timeout_sec": 20,
        "cli_path": "agently-cli",
        "seen_state_path": str(tmp_path / "seen.json"),
    }

    class FakeChat:
        def __init__(self):
            self.chat_gateway = object()
            self.replies = []

        async def _send_gateway_reply(self, text, ctx, emotion=None):
            self.replies.append(("text", text, ctx))
            return True

        async def _send_gateway_image_reply(self, image_path, ctx, caption=""):
            self.replies.append(("image", image_path, ctx))
            return True

    chat = FakeChat()
    plugin._chat_service = chat

    async def fake_summary(mail, character_name):
        return f"【{character_name}】{mail.get('subject')}"

    monkeypatch.setattr(plugin, "_generate_notification_summary", fake_summary)
    await plugin._check_new_mails_once()
    assert len(chat.replies) == 2
    assert "msg_unread" in plugin._seen_ids
    assert "msg_read" in plugin._seen_ids


@pytest.mark.asyncio
async def test_notification_pushes_summary_and_body_image(tmp_path, monkeypatch):
    Plugin = load_plugin_class()
    runner = FakeCliRunner(
        [
            {
                "returncode": 0,
                "stdout": json.dumps(
                    {
                        "ok": True,
                        "data": {
                            "data": [
                                {
                                    "message_id": "msg_new",
                                    "subject": "新邮件主题",
                                    "from": {"name": "Bob", "email": "b@example.com"},
                                    "created_at": "2026-07-28T12:00:00+08:00",
                                    "snippet": "新内容",
                                    "is_read": False,
                                }
                            ],
                            "pagination": {"has_more": False},
                        },
                    },
                    ensure_ascii=False,
                ),
                "stderr": "",
            },
            {
                "returncode": 0,
                "stdout": json.dumps(
                    {
                        "ok": True,
                        "data": {
                            "message_id": "msg_new",
                            "subject": "新邮件主题",
                            "from": {"name": "Bob", "email": "b@example.com"},
                            "created_at": "2026-07-28T12:00:00+08:00",
                            "body": "这是新邮件正文，用于推送图片。",
                            "attachments": [],
                        },
                    },
                    ensure_ascii=False,
                ),
                "stderr": "",
            },
        ]
    )
    plugin = Plugin(cli_runner=runner)
    seen_path = tmp_path / "seen.json"
    seen_path.write_text(
        json.dumps({"seen_ids": ["msg_old"]}, ensure_ascii=False), encoding="utf-8"
    )
    plugin.settings = {
        "enable_notifications": True,
        "notification_target_sessions": ["private:123456789"],
        "notification_character_name": "丰川祥子",
        "notification_poll_limit": 10,
        "notification_body_chars": 800,
        "request_timeout_sec": 20,
        "cli_path": "agently-cli",
        "seen_state_path": str(seen_path),
        "model_queue": [],
    }

    class FakeChat:
        def __init__(self):
            self.chat_gateway = object()
            self.replies = []

        async def _send_gateway_reply(self, text, ctx, emotion=None):
            self.replies.append(("text", text, ctx.get("channel_meta", {}).get("session_id")))
            return True

        async def _send_gateway_image_reply(self, image_path, ctx, caption=""):
            self.replies.append(
                ("image", os.path.exists(image_path), ctx.get("channel_meta", {}).get("session_id"))
            )
            return True

    chat = FakeChat()
    plugin._chat_service = chat

    async def fake_summary(mail, character_name):
        return f"【{character_name}】有新邮件：{mail.get('subject')}"

    monkeypatch.setattr(plugin, "_generate_notification_summary", fake_summary)
    plugin._load_seen_ids()
    await plugin._check_new_mails_once()

    assert len(chat.replies) == 2
    assert chat.replies[0][0] == "text"
    assert "丰川祥子" in chat.replies[0][1]
    assert chat.replies[0][2] == "private:123456789"
    assert chat.replies[1][0] == "image"
    assert chat.replies[1][1] is True
    assert "msg_new" in plugin._seen_ids
