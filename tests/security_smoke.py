import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from integrations.gui_http import GuiHttpServer
from integrations.gui_ws import GuiWebSocketServer
from integrations.mcp.bridge import _build_stdio_env
import modules.memory_sqlite as memory_sqlite
from modules.plugin_manager import PluginManager
from modules.security_redaction import redact_sensitive_text
from plugins.code_executor.plugin import Plugin as CodeExecutorPlugin
from plugins.mcp_tools.plugin import Plugin as McpToolsPlugin
from plugins.qq_file_browser.plugin import _validate_public_download_url
from plugins.search.plugin import Plugin as SearchPlugin
from plugins.workspace_ops.plugin import Plugin as WorkspaceOpsPlugin
from plugins.web_reader.plugin import Plugin as WebReaderPlugin
from services.chat_service import ChatService
from services.chat_support import reply_flow_service, text_utils


def _audit_count(store, action: str, entity: str, entity_id: str | None) -> int:
    if entity_id is None:
        row = store._connect().execute(
            "SELECT COUNT(1) AS c FROM audit_log "
            "WHERE action=? AND entity=? AND entity_id IS NULL",
            (action, entity),
        ).fetchone()
    else:
        row = store._connect().execute(
            "SELECT COUNT(1) AS c FROM audit_log "
            "WHERE action=? AND entity=? AND entity_id=?",
            (action, entity, entity_id),
        ).fetchone()
    return int(row["c"] if row else 0)


def _close_memory_store(store) -> None:
    conn = getattr(getattr(store, "_local", None), "conn", None)
    if conn is not None:
        conn.close()
        delattr(store._local, "conn")


def test_plugin_default_access() -> None:
    with tempfile.TemporaryDirectory(prefix="plugin_manager_smoke_") as plugin_dir:
        pm = PluginManager(plugin_dir=plugin_dir)
        default_access = pm._normalize_access_control(None)
    assert default_access["allow_local"] is True
    assert default_access["allow_remote_qq"] is False
    assert default_access["allow_qq_owner"] is False
    assert default_access["allow_qq_others"] is False


def test_mcp_tools_config_rejects_remote_qq() -> None:
    config_path = ROOT / "plugins" / "mcp_tools" / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    access = config.get("access_control") or {}
    assert access["allow_local"] is True
    assert access["allow_remote_qq"] is False
    assert access["allow_qq_owner"] is False
    assert access["allow_qq_others"] is False


def test_search_plugin_config_keeps_delegate_mode() -> None:
    config_path = ROOT / "plugins" / "search" / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))

    assert SearchPlugin.type == "delegate"
    assert config.get("type") == "delegate"
    assert config.get("llm_command") == "search"


def test_costly_qq_plugins_are_owner_only_by_default() -> None:
    for rel_path in (
        "plugins/qq_draw/config.json",
        "plugins/qq_music/config.json",
    ):
        config = json.loads((ROOT / rel_path).read_text(encoding="utf-8"))
        access = config.get("access_control") or {}

        assert access["allow_remote_qq"] is True
        assert access["allow_qq_owner"] is True
        assert access["allow_qq_others"] is False
        assert access["allow_group_without_at"] is False


def test_search_retry_correction_forces_followup_search() -> None:
    text = "今年是2026年你找到还是过时的吧，重新查一下"

    assert text_utils.is_generic_search_followup_request(text) is True


def test_search_retry_correction_prefers_current_query_over_recent_topic() -> None:
    class FakeGatewayContextService:
        @staticmethod
        def conversation_session_key(_ctx):
            return "private:smoke"

    service = object.__new__(ChatService)
    service.gateway_context_service = FakeGatewayContextService()
    service._last_search_topic_by_session = {}
    service._load_recent_user_topic_from_store = lambda _ctx, current_text="": "上海卷是什么"
    service.brain = None
    service._looks_structured_reply = lambda _text: False

    text = "今年是2026年你找到还是过时的吧，重新查一下"

    assert service._resolve_followup_search_query(text, {"source": "qq_private"}) == text


def test_internal_prompt_instruction_leak_is_stripped() -> None:
    leaked = (
        "【动作/微表情】你自己决定是否让 Live2D 做动作。\n"
        "正文不要引用标签和动作。回复只要「正文」本身，不含任何额外说明。\n"
        "今年高考作文题主要围绕人工智能、成长和现实思考展开。"
    )

    cleaned = text_utils.strip_internal_tags(leaked)

    assert cleaned == "今年高考作文题主要围绕人工智能、成长和现实思考展开。"
    assert "动作/微表情" not in cleaned
    assert "正文不要引用标签" not in cleaned


def test_stream_flush_strips_internal_prompt_instruction_leak() -> None:
    leaked = (
        "【动作/微表情】你自己决定是否让 Live2D 做动作。\n"
        "正文不要引用标签和动作。回复只要「正文」本身，不含任何额外说明。\n"
        "今年高考作文题主要围绕人工智能、成长和现实思考展开。"
    )

    flushed = reply_flow_service.flush_stream_buffer(
        leaked,
        final=True,
        clean_text_for_tts=lambda text: text.strip(),
        strip_internal_tags=text_utils.strip_internal_tags,
        strip_cmd_anywhere=lambda text: text,
        strip_emo_tags_anywhere=lambda text: text,
        strip_model_catchphrase=lambda text: text,
    )

    assert flushed.chunk == "今年高考作文题主要围绕人工智能、成长和现实思考展开。"
    assert "动作/微表情" not in flushed.chunk


def test_mcp_tools_remote_allowlist_policy() -> None:
    plugin = McpToolsPlugin()

    assert plugin._is_tool_allowed("plugin.list")[0] is True
    allowed, _ = plugin._is_tool_allowed("mcp.demo.read")
    assert allowed is False

    plugin.settings = {"allowed_server_names": ["demo"]}
    assert plugin._is_tool_allowed("mcp.demo.read")[0] is True

    plugin.settings = {"allowed_tool_names": ["mcp.other.read"]}
    assert plugin._is_tool_allowed("mcp.other.read")[0] is True
    assert plugin._is_tool_allowed("mcp.demo.read")[0] is False

    plugin.settings = {"allow_all_remote_tools": {"default": True}}
    assert plugin._is_tool_allowed("mcp.any.run")[0] is True

    plugin.settings = {"allow_all_local_tools": False}
    assert plugin._is_tool_allowed("plugin.list")[0] is False

    plugin.settings = {
        "allow_all_local_tools": False,
        "allowed_tool_names": "plugin.list, mcp.demo.read",
    }
    assert plugin._is_tool_allowed("plugin.list")[0] is True
    assert plugin._is_tool_allowed("mcp.demo.read")[0] is True


def test_mcp_tools_blocks_disallowed_remote_call_before_bridge() -> None:
    class FailingBridge:
        async def call_tool(self, *_args, **_kwargs):
            raise AssertionError("bridge should not be called")

        def list_tools(self):
            return []

        def list_server_status(self):
            return []

    plugin = McpToolsPlugin()
    plugin.settings = {}
    result = asyncio.run(
        plugin.run(
            "call_tool ||| mcp.demo.read ||| {}",
            {"delegate_mode": True, "mcp_bridge": FailingBridge()},
        )
    )
    assert "allowlist" in result


def test_web_reader_blocks_private_urls() -> None:
    reader = WebReaderPlugin()
    blocked_urls = [
        "http://127.0.0.1:8097/gui",
        "http://localhost:8097/gui",
        "http://10.0.0.1/",
        "http://172.16.0.1/",
        "http://192.168.1.1/",
        "http://169.254.169.254/latest/meta-data/",
    ]
    for url in blocked_urls:
        try:
            reader._validate_public_url(url)
        except ValueError:
            continue
        raise AssertionError(f"Should be blocked: {url}")


def test_code_executor_static_guards_and_env() -> None:
    executor = CodeExecutorPlugin()
    payloads = [
        "__import__('os').system('whoami')",
        "import importlib; importlib.import_module('os').system('whoami')",
        "getattr(__builtins__, '__import__')('os').system('whoami')",
    ]
    for payload in payloads:
        result = executor._validate_code(payload)
        assert not result["valid"], f"Should be rejected: {payload}"

    old_key = os.environ.get("OPENAI_API_KEY")
    os.environ["OPENAI_API_KEY"] = "should_not_leak"
    try:
        env = executor._build_execution_env(tempfile.gettempdir())
    finally:
        if old_key is None:
            os.environ.pop("OPENAI_API_KEY", None)
        else:
            os.environ["OPENAI_API_KEY"] = old_key
    assert "OPENAI_API_KEY" not in env
    assert env["PYTHONPATH"] == ""
    assert env["PYTHONNOUSERSITE"] == "1"


def test_sensitive_text_redaction() -> None:
    raw = (
        "OPENAI_API_KEY=sk-testvalue123456789 "
        "Authorization: Bearer abcdefghijklmnopqrstuvwxyz "
        "password: hunter2"
    )
    redacted = redact_sensitive_text(raw)
    assert "sk-testvalue" not in redacted
    assert "abcdefghijklmnopqrstuvwxyz" not in redacted
    assert "hunter2" not in redacted
    assert "[REDACTED]" in redacted or "[REDACTED_KEY]" in redacted

    executor = CodeExecutorPlugin()
    output = executor._format_output(
        {
            "success": False,
            "stdout": "",
            "stderr": "TOKEN=secret-token-value\nOPENAI_API_KEY=sk-testvalue123456789",
            "execution_time": 0.01,
            "returncode": 1,
        }
    )
    assert "secret-token-value" not in output
    assert "sk-testvalue" not in output


def test_config_safe_env_parsing() -> None:
    import config

    old_bad_int = os.environ.get("SECURITY_SMOKE_BAD_INT")
    old_bad_float = os.environ.get("SECURITY_SMOKE_BAD_FLOAT")
    old_large_int = os.environ.get("SECURITY_SMOKE_LARGE_INT")
    try:
        os.environ["SECURITY_SMOKE_BAD_INT"] = "not-an-int"
        os.environ["SECURITY_SMOKE_BAD_FLOAT"] = "not-a-float"
        os.environ["SECURITY_SMOKE_LARGE_INT"] = "999999"

        assert config.get_env_int("SECURITY_SMOKE_BAD_INT", 12) == 12
        assert config.get_env_float("SECURITY_SMOKE_BAD_FLOAT", 2.5) == 2.5
        assert config.get_env_int("SECURITY_SMOKE_LARGE_INT", 12, 1, 100) == 100
        assert config.get_env_int("SECURITY_SMOKE_MISSING_INT", 7, 1, 100) == 7
    finally:
        for key, old_value in {
            "SECURITY_SMOKE_BAD_INT": old_bad_int,
            "SECURITY_SMOKE_BAD_FLOAT": old_bad_float,
            "SECURITY_SMOKE_LARGE_INT": old_large_int,
        }.items():
            if old_value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old_value


def test_gui_http_rejects_query_token() -> None:
    server = GuiHttpServer(access_token="secret")
    query_request = SimpleNamespace(headers={}, query={"token": "secret"})
    header_request = SimpleNamespace(headers={"X-GUI-Token": "secret"}, query={})
    bearer_request = SimpleNamespace(
        headers={"Authorization": "Bearer secret"},
        query={},
    )
    assert server._extract_token(query_request) == ""
    assert server._extract_token(header_request) == "secret"
    assert server._extract_token(bearer_request) == "secret"


def test_gui_ws_rejects_query_token() -> None:
    server = GuiWebSocketServer(access_token="secret")
    query_only = SimpleNamespace(request_headers={}, path="/gui?token=secret")
    header_ok = SimpleNamespace(request_headers={"X-GUI-Token": "secret"}, path="/gui")
    bearer_ok = SimpleNamespace(
        request_headers={"Authorization": "Bearer secret"},
        path="/gui",
    )
    assert server._extract_token(query_only, "/gui?token=secret") == ""
    assert server._authorized(query_only, "/gui?token=secret") is False
    assert server._extract_token(header_ok, "/gui") == "secret"
    assert server._extract_token(bearer_ok, "/gui") == "secret"


def test_gui_http_origin_uses_exact_loopback_match() -> None:
    server = GuiHttpServer(host="127.0.0.1", port=8097, access_token="secret")
    assert server._origin_allowed("http://localhost:8097") is True
    assert server._origin_allowed("http://127.0.0.1:8097") is True
    assert server._origin_allowed("http://localhost") is False
    assert server._origin_allowed("http://localhost:8098") is False
    assert server._origin_allowed("http://localhost:8097/path") is False
    assert server._origin_allowed("http://localhost:bad") is False
    assert server._origin_allowed("http://localhost.evil.test:8097") is False
    assert server._origin_allowed("http://127.0.0.1.evil.test:8097") is False
    assert server._origin_allowed("file://localhost") is False


def test_workspace_ops_list_changes_does_not_echo_confirm_token() -> None:
    plugin = WorkspaceOpsPlugin()
    plugin.pending_changes["abc123"] = {
        "target": plugin.workspace_root / "README.md",
        "old": "old",
        "new": "new",
        "diff": "",
        "time": "2026-06-15T00:00:00",
        "confirm_token": "secret-token",
        "task_id": "task-1",
    }
    listed = plugin._list_changes()
    assert "abc123" in listed
    assert "README.md" in listed
    assert "task-1" in listed
    assert "secret-token" not in listed
    assert "token=" not in listed


def test_gui_dependency_install_requires_confirmation() -> None:
    class FakeDependencyModule:
        installed = False

        @staticmethod
        def scan_missing_dependencies(_plugins_dir):
            return [{"module": "fake_module", "package": "fake-package"}]

        @staticmethod
        def build_install_command(_rows):
            return "python -m pip install fake-package"

        @classmethod
        def install_missing(cls, _rows, timeout=900):
            cls.installed = True
            return {"ok": "1", "message": f"installed with timeout={timeout}"}

    server = GuiHttpServer(access_token="secret")
    server._load_local_module = lambda *_args, **_kwargs: FakeDependencyModule

    result = server._install_dependencies(str(ROOT), confirm=False)
    assert result["ok"] == "0"
    assert result["code"] == "confirmation_required"
    assert FakeDependencyModule.installed is False

    confirmed = server._install_dependencies(str(ROOT), confirm=True)
    assert confirmed["ok"] == "1"
    assert FakeDependencyModule.installed is True


def test_mcp_stdio_env_does_not_inherit_secrets() -> None:
    old_key = os.environ.get("OPENAI_API_KEY")
    os.environ["OPENAI_API_KEY"] = "implicit_secret"
    try:
        env = _build_stdio_env({})
        explicit_env = _build_stdio_env({"OPENAI_API_KEY": "explicit_secret"})
    finally:
        if old_key is None:
            os.environ.pop("OPENAI_API_KEY", None)
        else:
            os.environ["OPENAI_API_KEY"] = old_key
    assert "OPENAI_API_KEY" not in env
    assert explicit_env["OPENAI_API_KEY"] == "explicit_secret"


def test_qq_file_browser_blocks_private_download_urls() -> None:
    blocked_urls = [
        "http://127.0.0.1:8097/gui",
        "http://localhost:8097/gui",
        "http://10.0.0.1/file",
        "http://172.16.0.1/file",
        "http://192.168.1.1/file",
        "http://169.254.169.254/latest/meta-data/",
    ]
    for url in blocked_urls:
        try:
            _validate_public_download_url(url)
        except ValueError:
            continue
        raise AssertionError(f"Should be blocked: {url}")


def test_memory_store_delete_methods_write_audit() -> None:
    with tempfile.TemporaryDirectory(prefix="memory_store_smoke_") as tmp:
        tmp_dir = Path(tmp)
        old_profile = memory_sqlite.LEGACY_PROFILE_JSON
        old_events = memory_sqlite.LEGACY_EVENTS_DB
        store = None
        memory_sqlite.LEGACY_PROFILE_JSON = str(tmp_dir / "missing_profile.json")
        memory_sqlite.LEGACY_EVENTS_DB = str(tmp_dir / "missing_events.db")
        try:
            store = memory_sqlite.MemorySQLite(str(tmp_dir / "memory.db"))
        finally:
            memory_sqlite.LEGACY_PROFILE_JSON = old_profile
            memory_sqlite.LEGACY_EVENTS_DB = old_events

        try:
            item_id = store.upsert_item(
                {
                    "id": "smoke-item",
                    "type": "note",
                    "text": "delete audit smoke item",
                    "source": "security_smoke",
                }
            )
            assert store.delete_item(item_id) is True
            assert store.get_item(item_id) is None
            assert _audit_count(store, "delete", "memory_items", item_id) == 1

            episode_id = store.upsert_episode(
                {
                    "id": "smoke-episode",
                    "title": "delete audit smoke episode",
                    "summary": "delete audit smoke episode",
                    "tags": ["smoke"],
                }
            )
            assert store.delete_episode(episode_id) is True
            assert store.get_episode(episode_id) is None
            assert _audit_count(store, "delete", "episodes", episode_id) == 1

            store.add_transcript("user", "first smoke transcript")
            store.add_transcript("assistant", "second smoke transcript")
            assert store.clear_transcript() == 2
            remaining = store._connect().execute(
                "SELECT COUNT(1) AS c FROM transcript"
            ).fetchone()
            assert int(remaining["c"]) == 0
            assert _audit_count(store, "clear", "transcript", None) == 1
        finally:
            if store is not None:
                _close_memory_store(store)


def test_activity_events_list_uses_insert_order_for_mixed_timestamp_formats() -> None:
    with tempfile.TemporaryDirectory(prefix="activity_events_order_") as tmp:
        tmp_dir = Path(tmp)
        old_profile = memory_sqlite.LEGACY_PROFILE_JSON
        old_events = memory_sqlite.LEGACY_EVENTS_DB
        store = None
        memory_sqlite.LEGACY_PROFILE_JSON = str(tmp_dir / "missing_profile.json")
        memory_sqlite.LEGACY_EVENTS_DB = str(tmp_dir / "missing_events.db")
        try:
            store = memory_sqlite.MemorySQLite(str(tmp_dir / "memory.db"))
        finally:
            memory_sqlite.LEGACY_PROFILE_JSON = old_profile
            memory_sqlite.LEGACY_EVENTS_DB = old_events

        try:
            store.add_activity_event(
                {
                    "event_id": "local-time",
                    "ts": "2026-06-17T17:03:09.615924",
                    "kind": "foreground_changed",
                    "presence": "active",
                    "app": {"name": "Chrome"},
                    "source": "python-screen-sensor",
                }
            )
            store.add_activity_event(
                {
                    "event_id": "utc-time",
                    "ts": "2026-06-17T09:03:35.849065700+00:00",
                    "kind": "activity_sample",
                    "presence": "active",
                    "app": {"name": "chrome.exe"},
                    "source": "live2d-tauri",
                }
            )

            rows = store.list_activity_events(limit=2)

            assert [row["event_id"] for row in rows] == ["utc-time", "local-time"]
        finally:
            if store is not None:
                _close_memory_store(store)


def main() -> None:
    test_plugin_default_access()
    test_mcp_tools_config_rejects_remote_qq()
    test_search_plugin_config_keeps_delegate_mode()
    test_search_retry_correction_forces_followup_search()
    test_search_retry_correction_prefers_current_query_over_recent_topic()
    test_internal_prompt_instruction_leak_is_stripped()
    test_stream_flush_strips_internal_prompt_instruction_leak()
    test_mcp_tools_remote_allowlist_policy()
    test_mcp_tools_blocks_disallowed_remote_call_before_bridge()
    test_web_reader_blocks_private_urls()
    test_code_executor_static_guards_and_env()
    test_sensitive_text_redaction()
    test_config_safe_env_parsing()
    test_gui_http_rejects_query_token()
    test_gui_ws_rejects_query_token()
    test_gui_http_origin_uses_exact_loopback_match()
    test_workspace_ops_list_changes_does_not_echo_confirm_token()
    test_gui_dependency_install_requires_confirmation()
    test_mcp_stdio_env_does_not_inherit_secrets()
    test_qq_file_browser_blocks_private_download_urls()
    test_memory_store_delete_methods_write_audit()
    test_activity_events_list_uses_insert_order_for_mixed_timestamp_formats()
    print("security smoke ok")


if __name__ == "__main__":
    main()
