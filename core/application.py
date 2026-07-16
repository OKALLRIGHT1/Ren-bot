"""
搴旂敤涓荤被
绠＄悊鏁翠釜搴旂敤鐨勭敓鍛藉懆鏈熷拰缁勪欢鍒濆鍖?
"""

import asyncio
import json
import os
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Optional, Any, Dict
from datetime import datetime
from datetime import timedelta
from urllib.parse import unquote, urlparse

import config


def _read_existing_napcat_token() -> str:
    search_roots = []
    env_config = os.getenv("NAPCAT_ONEBOT_CONFIG", "").strip()
    if env_config:
        search_roots.append(Path(env_config))
    search_roots.append(Path(r"D:\tools\napcat"))
    for root in search_roots:
        try:
            paths = [root] if root.is_file() else root.rglob("onebot11_*.json")
        except Exception:
            continue
        for path in paths:
            try:
                payload = json.loads(Path(path).read_text(encoding="utf-8"))
            except Exception:
                continue
            clients = []
            network = payload.get("network")
            if isinstance(network, dict) and isinstance(
                network.get("websocketClients"), list
            ):
                clients = network.get("websocketClients") or []
            elif isinstance(payload.get("websocketClients"), list):
                clients = payload.get("websocketClients") or []
            for client in clients:
                if not isinstance(client, dict):
                    continue
                url = str(client.get("url") or "")
                if "8095" not in url:
                    continue
                token = str(client.get("token") or "").strip()
                if token:
                    return token
    return ""

try:
    import psutil
except Exception:
    psutil = None
try:
    import paho.mqtt.client as mqtt
except Exception:
    mqtt = None
from config import (
    TTS_ENABLED,
    TTS_MAX_CHARS,
    TTS_USE_LIVE2D_PLAYER,
    TTS_CHANNEL,
    TTS_VOLUME,
    VOICE_NAME,
    GUI_BACKEND,
    TTS_RATE,
    TTS_SPLIT_LONG_TEXT,
    TTS_CHUNK_CHARS,
    EMO_TO_LIVE2D,
    LIP_SYNC_ENABLED,
    RHUBARB_PATH,
    LIP_SYNC_SMOOTH_WINDOW,
    MCP_SERVER_CONFIGS,
    MCP_ENABLED,
    NAPCAT_ENABLED,
    NAPCAT_WEBHOOK_HOST,
    NAPCAT_WEBHOOK_PORT,
    NAPCAT_WEBHOOK_PATH,
    NAPCAT_ACCESS_TOKEN,
    NAPCAT_API_BASE,
    NAPCAT_API_TOKEN,
    NAPCAT_REPLY_ENABLED,
    NAPCAT_ALLOW_PRIVATE,
    NAPCAT_ALLOW_GROUP,
    NAPCAT_GROUP_REQUIRE_AT,
    NAPCAT_VOICE_REPLY_ENABLED,
    NAPCAT_VOICE_REPLY_PROBABILITY,
    GUI_WS_HOST,
    GUI_WS_PORT,
    GUI_WS_PATH,
    GUI_HTTP_HOST,
    GUI_HTTP_PORT,
    GUI_HTTP_PREFIX,
    SEDENTARY_REMINDER_MINUTES,
    SEDENTARY_REMINDER_COOLDOWN_MINUTES,
    SEDENTARY_POPUP_ENABLED,
    SEDENTARY_POPUP_TITLE,
    SEDENTARY_POPUP_MESSAGE,
    SEDENTARY_POPUP_IMAGE_PATH,
    SEDENTARY_POPUP_SNOOZE_MINUTES,
    SEDENTARY_POPUP_AUTO_CLOSE_SECONDS,
    MQTT_DISPLAY_ENABLED,
    MQTT_DISPLAY_HOST,
    MQTT_DISPLAY_PORT,
    MQTT_DISPLAY_TOPIC,
)

from modules.advanced_memory import AdvancedMemorySystem
from modules.memory_sqlite import get_memory_store
from modules.emotion_controller import EmotionController
from modules.plugin_manager import PluginManager
from modules.skill_manager import SkillManager
from modules.tool_router import ToolRouter
from modules.tts import TTSRouter
from modules.state_machine import AgentStateMachine, AgentState
from modules.live2d import (
    send_bubble,
    trigger_motion,
    change_costume,
    play_motion,
    set_expression,
    estimate_bubble_display_ms,
)


# 导出ChatService（在chat_service.py中定义）
from services.chat_service import ChatService
from services.chat_support.text_splitter import split_chat_text_parts

# [新增] 尝试导入屏幕感知模块 (容错处理)
try:
    from modules.screen_sensor import ScreenSensor
except ImportError:
    print("[App] modules.screen_sensor not found; screen sensing disabled")
    ScreenSensor = None

from core.container import ServiceContainer
from core.event_bus import EventBus, Events
from core.message_source import build_output_profile
from modules.event_logger import EventLogger
from integrations.mcp import MCPToolBridge
from integrations.chat_gateway import (
    ChatGateway,
    NapCatOneBotAdapter,
    NapCatWebhookServer,
)
from integrations.gui_ws import GuiWebSocketServer
from integrations.gui_http import GuiHttpServer
from integrations.gui_access import get_or_create_gui_access_token
from modules.live2d_transport import (
    GuiWebSocketTransport,
    LegacyLocalWebSocketTransport,
    Live2DTransportBus,
    configure_live2d_transport,
)


LIVE2D_ONLY_APP_ID = "com.live2d-only.app"
LIVE2D_ACTIVITY_SETTING_KEYS = (
    "gui_activity_endpoint",
    "gui_access_token",
    "sedentary_reminder_minutes",
    "sedentary_break_minutes",
    "sedentary_cooldown_minutes",
)

try:
    from modules.live2d import go_idle
except Exception:
    go_idle = None


try:
    from modules.music_sensor import MusicSensor
except ImportError:
    MusicSensor = None


def split_local_bubble_text_parts(text: str) -> list[str]:
    return split_chat_text_parts(text, max_len=TTS_CHUNK_CHARS)


class Live2DApplication:
    # Live2D application.

    def __init__(self):
        # 鏍稿績缁勪欢
        self.container = ServiceContainer()
        self.event_bus = EventBus()
        self.state_machine = AgentStateMachine()
        self.logger = None
        self.event_logger = None

        # 涓氬姟缁勪欢
        self.brain = None
        self.memory_store = None
        self.emotion_controller = None
        self.plugin_manager = None
        self.skill_manager = None
        self.tool_router = None
        self.tts = None
        self.presenter = None
        self.chat_service = None
        self.mcp_bridge = None
        self.chat_gateway = None
        self.chat_gateway_server = None
        self._silent_bubble_seq = 0

        # [鏂板] 灞忓箷鎰熺煡涓庤闊崇粍浠?
        self.screen_sensor = None
        self.voice_sensor = None  # 馃煝 璇煶浼犳劅鍣?

        # GUI鐩稿叧
        self.qt_ui = None
        self.loop = None
        self.gui_ws_server = None
        self.gui_http_server = None
        self.display_mqtt_client = None
        self.display_state_config_path = Path("./data/display_state_config.json")
        self.display_mqtt_last_error = "未初始化"
        self._runtime_mode = ""
        self._headless_stop_event = threading.Event()
        self._requested_exit_code = 0

        # 鏃ヨ鐘舵€佹爣璁帮紝闃叉閲嶅璁板綍
        self.last_summary_date = None

        # 閰嶇疆
        self.tts_enabled = bool(TTS_ENABLED)
        self.runtime_settings_path = Path("./data/runtime_settings.json")
        self.think_motion_enabled = True
        try:
            from config import THINK_MOTION_ENABLED, THINK_MOTION_NAME

            self.think_motion_enabled = bool(THINK_MOTION_ENABLED)
            self.think_motion_name = THINK_MOTION_NAME or "think"
        except Exception:
            self.think_motion_name = "think"

        # 闊充箰閰嶇疆
        self.music_sensor = None

    def _load_runtime_settings(self) -> Dict[str, Any]:
        path = self.runtime_settings_path
        if not path.exists():
            return {}
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception as e:
            if self.logger:
                self.logger.warning(f"鍔犺浇杩愯鏃惰缃け璐? {e}")
            return {}

    def _save_runtime_settings(self, settings: Dict[str, Any]):
        path = self.runtime_settings_path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", encoding="utf-8") as f:
                json.dump(settings, f, ensure_ascii=False, indent=2)
        except Exception as e:
            if self.logger:
                self.logger.warning(f"淇濆瓨杩愯鏃惰缃け璐? {e}")

    def _publish_gui_activity_endpoint(self) -> None:
        server = getattr(self, "gui_http_server", None)
        if server is None:
            return
        endpoint = (
            server.activity_ingest_url()
            if hasattr(server, "activity_ingest_url")
            else self._build_gui_activity_endpoint(
                getattr(server, "host", GUI_HTTP_HOST),
                getattr(server, "port", GUI_HTTP_PORT),
                getattr(server, "path_prefix", GUI_HTTP_PREFIX),
            )
        )
        settings = self._load_runtime_settings()
        patch = {
            "gui_http_host": getattr(server, "host", GUI_HTTP_HOST),
            "gui_http_port": int(getattr(server, "port", GUI_HTTP_PORT)),
            "gui_http_prefix": getattr(server, "path_prefix", GUI_HTTP_PREFIX),
            "gui_activity_endpoint": endpoint,
        }
        access_token = str(getattr(server, "access_token", "") or "").strip()
        if access_token:
            patch["gui_access_token"] = access_token
        if not all(settings.get(key) == value for key, value in patch.items()):
            settings.update(patch)
            self._save_runtime_settings(settings)
            if self.logger:
                self.logger.info(f"GUI activity endpoint published: {endpoint}")
        self._sync_live2d_activity_settings(settings)

    def _live2d_activity_settings_path(self) -> Optional[Path]:
        appdata = str(os.getenv("APPDATA") or "").strip()
        if not appdata:
            return None
        return Path(appdata) / LIVE2D_ONLY_APP_ID / "runtime_settings.json"

    def _sync_live2d_activity_settings(self, settings: Dict[str, Any]) -> bool:
        path = self._live2d_activity_settings_path()
        if path is None:
            return False
        patch = {
            key: settings[key]
            for key in LIVE2D_ACTIVITY_SETTING_KEYS
            if key in settings and settings[key] not in (None, "")
        }
        if not patch:
            return False
        current: Dict[str, Any] = {}
        if path.exists():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    current = loaded
            except Exception as exc:
                if self.logger:
                    self.logger.warning(
                        f"Live2D activity settings read failed: {exc}"
                    )
        if all(current.get(key) == value for key, value in patch.items()):
            return True
        current.update(patch)
        temp_path = path.with_suffix(path.suffix + ".tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temp_path.write_text(
                json.dumps(current, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(temp_path, path)
        except Exception as exc:
            if self.logger:
                self.logger.warning(f"Live2D activity settings sync failed: {exc}")
            return False
        if self.logger:
            self.logger.info("Live2D activity settings synchronized")
        return True

    @staticmethod
    def _build_gui_activity_endpoint(host: str, port: int, path_prefix: str) -> str:
        prefix = str(path_prefix or "/gui").strip() or "/gui"
        if not prefix.startswith("/"):
            prefix = "/" + prefix
        prefix = prefix.rstrip("/") or "/"
        base = f"http://{host}:{int(port)}{prefix}"
        return f"{base}/activity-ingest"

    def _load_runtime_tts_enabled(self) -> bool:
        settings = self._load_runtime_settings()
        value = settings.get("tts_enabled")
        if isinstance(value, bool):
            return value
        return bool(TTS_ENABLED)

    def _default_display_state_config(self) -> Dict[str, Any]:
        return {
            "metric_mode": "auto_ram",
            "metric_text": "",
            "default_icon_bits": "",
            "default_icon_w": 0,
            "default_icon_h": 0,
            "emotion_icons": {},
        }

    def load_display_state_config(self) -> Dict[str, Any]:
        path = self.display_state_config_path
        default = self._default_display_state_config()
        if not path.exists():
            return default
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return default
            cfg = dict(default)
            cfg.update(data)
            if not isinstance(cfg.get("emotion_icons"), dict):
                cfg["emotion_icons"] = {}
            return cfg
        except Exception:
            return default

    def save_display_state_config(self, cfg: Dict[str, Any]):
        path = self.display_state_config_path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except Exception as e:
            if self.logger:
                self.logger.warning(f"淇濆瓨鐘舵€佸睆閰嶇疆澶辫触: {e}")

    def _load_external_runtime_settings(self) -> Dict[str, Any]:
        return self._normalize_external_runtime_settings(self._load_runtime_settings())

    def _normalize_external_runtime_settings(
        self, settings: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        settings = settings if isinstance(settings, dict) else {}

        def _parse_id_list(value):
            if isinstance(value, str):
                return [
                    item.strip()
                    for item in re.split(r"[,锛孿n\s]+", value)
                    if item.strip()
                ]
            if isinstance(value, list):
                return [str(item).strip() for item in value if str(item).strip()]
            return []

        def _parse_text_list(value):
            if isinstance(value, str):
                return [
                    item.strip()
                    for item in re.split(r"[,锛?\n]+", value)
                    if item.strip()
                ]
            if isinstance(value, list):
                return [str(item).strip() for item in value if str(item).strip()]
            return []

        owner_ids_raw = settings.get("napcat_owner_user_ids", [])
        owner_ids = _parse_id_list(owner_ids_raw)
        user_whitelist = _parse_id_list(settings.get("napcat_user_whitelist", []))
        user_blacklist = _parse_id_list(settings.get("napcat_user_blacklist", []))
        group_whitelist = _parse_id_list(settings.get("napcat_group_whitelist", []))
        group_blacklist = _parse_id_list(settings.get("napcat_group_blacklist", []))
        skill_search_paths = _parse_text_list(
            settings.get("skill_search_paths", ["./skills", "~/.codex/skills"])
        )
        active_skills = _parse_text_list(settings.get("active_skills", []))
        image_prompt = str(settings.get("napcat_image_prompt", "") or "").strip()
        voice_probability_raw = settings.get(
            "napcat_voice_reply_probability", NAPCAT_VOICE_REPLY_PROBABILITY
        )
        try:
            voice_probability = max(0, min(100, int(voice_probability_raw)))
        except Exception:
            voice_probability = int(NAPCAT_VOICE_REPLY_PROBABILITY)
        try:
            expression_max_prompt_items = int(
                settings.get("expression_library_max_prompt_items", 4) or 4
            )
        except Exception:
            expression_max_prompt_items = 4

        def _int_setting(key: str, default: int, minimum: int) -> int:
            try:
                value = int(settings.get(key, default) or default)
            except Exception:
                value = int(default)
            return max(int(minimum), value)

        gui_http_host = str(settings.get("gui_http_host", GUI_HTTP_HOST) or GUI_HTTP_HOST)
        gui_http_port = int(settings.get("gui_http_port", GUI_HTTP_PORT) or GUI_HTTP_PORT)
        gui_http_prefix = str(
            settings.get("gui_http_prefix", GUI_HTTP_PREFIX) or GUI_HTTP_PREFIX
        )
        gui_activity_endpoint = str(settings.get("gui_activity_endpoint") or "").strip()
        if not gui_activity_endpoint:
            gui_activity_endpoint = self._build_gui_activity_endpoint(
                gui_http_host, gui_http_port, gui_http_prefix
            )

        napcat_access_token = str(
            settings.get("napcat_access_token", NAPCAT_ACCESS_TOKEN) or ""
        ).strip()
        if not napcat_access_token:
            napcat_access_token = _read_existing_napcat_token()
        if not napcat_access_token:
            napcat_access_token = secrets.token_urlsafe(32)
        if str(settings.get("napcat_access_token") or "").strip() != napcat_access_token:
            settings["napcat_access_token"] = napcat_access_token
            self._save_runtime_settings(settings)
        return {
            "mcp_enabled": bool(settings.get("mcp_enabled", MCP_ENABLED)),
            "mcp_server_configs": settings.get("mcp_server_configs", MCP_SERVER_CONFIGS)
            if isinstance(settings.get("mcp_server_configs", MCP_SERVER_CONFIGS), list)
            else MCP_SERVER_CONFIGS,
            "napcat_enabled": bool(settings.get("napcat_enabled", NAPCAT_ENABLED)),
            "napcat_webhook_host": str(
                settings.get("napcat_webhook_host", NAPCAT_WEBHOOK_HOST)
                or NAPCAT_WEBHOOK_HOST
            ),
            "napcat_webhook_port": int(
                settings.get("napcat_webhook_port", NAPCAT_WEBHOOK_PORT)
                or NAPCAT_WEBHOOK_PORT
            ),
            "napcat_webhook_path": str(
                settings.get("napcat_webhook_path", NAPCAT_WEBHOOK_PATH)
                or NAPCAT_WEBHOOK_PATH
            ),
            "napcat_access_token": napcat_access_token,
            "napcat_api_base": str(
                settings.get("napcat_api_base", NAPCAT_API_BASE) or NAPCAT_API_BASE
            ),
            "napcat_api_token": str(
                settings.get("napcat_api_token", NAPCAT_API_TOKEN) or ""
            ),
            "napcat_reply_enabled": bool(
                settings.get("napcat_reply_enabled", NAPCAT_REPLY_ENABLED)
            ),
            "napcat_allow_private": bool(
                settings.get("napcat_allow_private", NAPCAT_ALLOW_PRIVATE)
            ),
            "napcat_allow_group": bool(
                settings.get("napcat_allow_group", NAPCAT_ALLOW_GROUP)
            ),
            "napcat_group_require_at": bool(
                settings.get("napcat_group_require_at", NAPCAT_GROUP_REQUIRE_AT)
            ),
            "napcat_owner_user_ids": owner_ids,
            "napcat_owner_label": str(
                settings.get("napcat_owner_label", "涓讳汉") or "涓讳汉"
            ),
            "napcat_image_vision_enabled": bool(
                settings.get("napcat_image_vision_enabled", True)
            ),
            "napcat_image_prompt": image_prompt
            or "请客观详细描述这张 QQ 图片的内容，并提取可用于回复的关键信息。",
            "napcat_voice_reply_enabled": bool(
                settings.get("napcat_voice_reply_enabled", NAPCAT_VOICE_REPLY_ENABLED)
            ),
            "napcat_voice_reply_probability": voice_probability,
            "napcat_filter_mode": str(
                settings.get("napcat_filter_mode", "off") or "off"
            )
            .strip()
            .lower(),
            "skill_enabled": bool(settings.get("skill_enabled", True)),
            "skill_search_paths": skill_search_paths
            or ["./skills", "~/.codex/skills"],
            "active_skills": active_skills,
            "napcat_user_whitelist": user_whitelist,
            "napcat_user_blacklist": user_blacklist,
            "napcat_group_whitelist": group_whitelist,
            "napcat_group_blacklist": group_blacklist,
            "gui_ws_host": str(settings.get("gui_ws_host", GUI_WS_HOST) or GUI_WS_HOST),
            "gui_ws_port": int(settings.get("gui_ws_port", GUI_WS_PORT) or GUI_WS_PORT),
            "gui_ws_path": str(settings.get("gui_ws_path", GUI_WS_PATH) or GUI_WS_PATH),
            "gui_http_host": gui_http_host,
            "gui_http_port": gui_http_port,
            "gui_http_prefix": gui_http_prefix,
            "gui_activity_endpoint": gui_activity_endpoint,
            "expression_library_enabled": bool(
                settings.get("expression_library_enabled", True)
            ),
            "expression_library_use_in_chat": bool(
                settings.get("expression_library_use_in_chat", True)
            ),
            "expression_library_use_in_screen": bool(
                settings.get("expression_library_use_in_screen", True)
            ),
            "expression_library_max_prompt_items": max(
                1,
                min(8, expression_max_prompt_items),
            ),
            "sedentary_reminder_minutes": _int_setting(
                "sedentary_reminder_minutes", SEDENTARY_REMINDER_MINUTES, 1
            ),
            "sedentary_break_minutes": _int_setting(
                "sedentary_break_minutes",
                int(getattr(config, "SEDENTARY_BREAK_MINUTES", 5)),
                1,
            ),
            "sedentary_cooldown_minutes": _int_setting(
                "sedentary_cooldown_minutes",
                SEDENTARY_REMINDER_COOLDOWN_MINUTES,
                1,
            ),
            "sedentary_popup_enabled": bool(
                settings.get("sedentary_popup_enabled", SEDENTARY_POPUP_ENABLED)
            ),
            "sedentary_status_visible": bool(
                settings.get("sedentary_status_visible", True)
            ),
            "sedentary_popup_title": str(
                settings.get("sedentary_popup_title", SEDENTARY_POPUP_TITLE)
                or SEDENTARY_POPUP_TITLE
            ),
            "sedentary_popup_message": str(
                settings.get("sedentary_popup_message", SEDENTARY_POPUP_MESSAGE)
                or SEDENTARY_POPUP_MESSAGE
            ),
            "sedentary_popup_image_path": str(
                settings.get("sedentary_popup_image_path", SEDENTARY_POPUP_IMAGE_PATH)
                or ""
            ),
            "sedentary_popup_snooze_minutes": _int_setting(
                "sedentary_popup_snooze_minutes", SEDENTARY_POPUP_SNOOZE_MINUTES, 1
            ),
            "sedentary_popup_auto_close_seconds": _int_setting(
                "sedentary_popup_auto_close_seconds",
                SEDENTARY_POPUP_AUTO_CLOSE_SECONDS,
                0,
            ),
        }

    async def render_gateway_voice_reply(
        self, text: str, emotion: Optional[str] = None, **kwargs
    ) -> Optional[str]:
        clean = str(text or "").strip()
        if not clean or self.tts is None:
            return None
        try:
            path, _duration = await self.tts.synthesize_once(clean, emotion=emotion)
            return path
        except Exception as e:
            if self.logger:
                self.logger.warning(f"Gateway voice synth failed: {e}")
            return None

    def _register_mcp_local_tools(self):
        if not self.mcp_bridge:
            return
        self.mcp_bridge.clear_local_tools()
        self.mcp_bridge.register_local_tool(
            "plugin.list",
            lambda: [
                getattr(p, "name", k) for k, p in self.plugin_manager.plugins.items()
            ],
            description="List currently loaded local plugin display names.",
        )
        self.mcp_bridge.register_local_tool(
            "chat.process",
            lambda text, source="text_input": self.on_gui_send(
                text, {"source": source}
            ),
            description="Send a message into the chat pipeline.",
            input_schema={
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "source": {"type": "string"},
                },
            },
        )
        self.mcp_bridge.register_local_tool(
            "chat.gateway.dispatch",
            self.dispatch_gateway_payload,
            description="Dispatch a raw inbound payload to a registered external chat adapter.",
            input_schema={
                "type": "object",
                "properties": {
                    "adapter_name": {"type": "string"},
                    "payload": {"type": "object"},
                },
                "required": ["adapter_name", "payload"],
            },
        )
        self.mcp_bridge.register_local_tool(
            "mcp.list_tools",
            lambda provider="": [
                {
                    "name": spec.name,
                    "provider": spec.provider,
                    "description": spec.description,
                    "input_schema": spec.input_schema,
                }
                for spec in self.mcp_bridge.list_tools(provider=provider or None)
            ],
            description="List currently available MCP tools, including remote tools.",
            input_schema={
                "type": "object",
                "properties": {"provider": {"type": "string"}},
            },
        )
        self.mcp_bridge.register_local_tool(
            "mcp.server_status",
            self.mcp_bridge.list_server_status,
            description="List current remote MCP server connection status.",
        )
        self.mcp_bridge.register_local_tool(
            "mcp.call_tool",
            self.mcp_bridge.call_tool,
            description="Call a local or remote MCP tool by unified tool name.",
            input_schema={
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "arguments": {"type": "object"},
                },
                "required": ["name"],
            },
        )

    def get_mcp_tool_names(self):
        if not self.mcp_bridge:
            return []
        try:
            return [spec.name for spec in self.mcp_bridge.list_tools()]
        except Exception:
            return []

    async def _select_sedentary_meme_image_path_async(
        self, app_name: str, active_minutes: int
    ) -> str:
        qq_path = await self._select_sedentary_qq_meme_image_path_async(
            app_name, active_minutes
        )
        if qq_path:
            return qq_path

        plugin = getattr(self.plugin_manager, "plugins", {}).get("meme_pack")
        if plugin is None or not hasattr(plugin, "select_meme_image_path"):
            return ""
        result = await plugin.select_meme_image_path(
            user_text=(
                f"久坐提醒：连续使用 {app_name} {active_minutes} 分钟；"
                "标签：久坐、休息、提醒、伸展、护眼"
            ),
            reply_text="起来活动一下吧",
            emotion="提醒",
            ctx={"source": "desktop", "reason": "sedentary"},
            mark_used=False,
            force_pick=True,
        )
        return self._extract_existing_image_path(result)

    async def _select_sedentary_qq_meme_image_path_async(
        self, app_name: str, active_minutes: int
    ) -> str:
        context = {
            "source": "desktop",
            "reason": "sedentary",
            "app_name": str(app_name or "电脑"),
            "active_minutes": int(active_minutes or 0),
        }
        sedentary_user_text = (
            f"久坐提醒：连续使用 {app_name} {active_minutes} 分钟；"
            "标签：久坐、休息、提醒、伸展、护眼"
        )
        plugins = getattr(self.plugin_manager, "plugins", {}) or {}
        for plugin in plugins.values():
            selector = getattr(plugin, "select_qq_meme_image_path", None)
            if not callable(selector):
                selector = getattr(plugin, "select_qq_expression_image_path", None)
            if not callable(selector):
                continue
            try:
                result = selector(
                    user_text=sedentary_user_text,
                    reply_text="起来活动一下吧",
                    emotion="提醒",
                    ctx=context,
                    mark_used=False,
                    force_pick=True,
                )
                if asyncio.iscoroutine(result):
                    result = await result
            except TypeError:
                try:
                    result = selector(app_name, active_minutes)
                    if asyncio.iscoroutine(result):
                        result = await result
                except Exception as exc:
                    if self.logger:
                        self.logger.debug(f"[SedentaryMeme] QQ selector failed: {exc}")
                    continue
            except Exception as exc:
                if self.logger:
                    self.logger.debug(f"[SedentaryMeme] QQ selector failed: {exc}")
                continue
            image_path = self._extract_existing_image_path(result)
            if image_path:
                return image_path

        gateway = getattr(self, "chat_gateway", None)
        adapter = None
        try:
            adapter = getattr(gateway, "adapters", {}).get("napcat_qq")
        except Exception:
            adapter = None
        selector = getattr(adapter, "select_qq_meme_image_path", None)
        if callable(selector):
            try:
                result = selector(context)
                if asyncio.iscoroutine(result):
                    result = await result
                image_path = self._extract_existing_image_path(result)
                if image_path:
                    return image_path
            except Exception as exc:
                if self.logger:
                    self.logger.debug(
                        f"[SedentaryMeme] NapCat QQ selector failed: {exc}"
                    )
        return ""

    def _extract_existing_image_path(self, result: Any) -> str:
        candidates = []
        if isinstance(result, str):
            candidates.append(result)
        elif isinstance(result, dict):
            for key in (
                "image_path",
                "path",
                "file_path",
                "file",
                "local_path",
                "url",
            ):
                value = result.get(key)
                if isinstance(value, str):
                    candidates.append(value)
            for key in ("data", "item", "asset"):
                value = result.get(key)
                if isinstance(value, dict):
                    nested = self._extract_existing_image_path(value)
                    if nested:
                        return nested
        elif isinstance(result, list):
            for item in result:
                nested = self._extract_existing_image_path(item)
                if nested:
                    return nested

        for value in candidates:
            text = str(value or "").strip()
            if not text or text.startswith(("http://", "https://", "base64://")):
                continue
            if text.startswith("file://"):
                parsed = urlparse(text)
                text = unquote(parsed.path or "")
                if parsed.netloc and parsed.netloc not in {"localhost", "127.0.0.1"}:
                    continue
                if os.name == "nt" and re.match(r"^/[A-Za-z]:/", text):
                    text = text[1:]
            path = Path(text).expanduser()
            if not path.is_absolute():
                path = Path.cwd() / path
            try:
                path = path.resolve()
            except Exception:
                path = path.absolute()
            if path.is_file():
                return str(path)
        return ""

    def select_sedentary_meme_image_path(self, app_name: str, active_minutes: int):
        if not self.loop:
            return ""
        return asyncio.run_coroutine_threadsafe(
            self._select_sedentary_meme_image_path_async(app_name, active_minutes),
            self.loop,
        )

    def apply_external_settings(
        self, settings: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        if isinstance(settings, dict):
            merged_settings = self._load_runtime_settings()
            merged_settings.update(settings)
        else:
            merged_settings = self._load_runtime_settings()
        external_settings = self._normalize_external_runtime_settings(merged_settings)
        self._sync_live2d_activity_settings(
            {
                **merged_settings,
                "gui_activity_endpoint": external_settings["gui_activity_endpoint"],
                "sedentary_reminder_minutes": external_settings[
                    "sedentary_reminder_minutes"
                ],
                "sedentary_break_minutes": external_settings["sedentary_break_minutes"],
                "sedentary_cooldown_minutes": external_settings[
                    "sedentary_cooldown_minutes"
                ],
            }
        )
        result = {
            "mcp_enabled": bool(external_settings["mcp_enabled"]),
            "napcat_enabled": bool(external_settings["napcat_enabled"]),
            "skill_enabled": bool(external_settings["skill_enabled"]),
            "mcp_live_applied": False,
            "napcat_live_applied": False,
            "skill_live_applied": False,
            "napcat_server_running": False,
            "mcp_servers": [],
            "mcp_tools": [],
            "skill_count": 0,
            "active_skills": [],
            "sedentary_live_applied": False,
        }

        config.SEDENTARY_REMINDER_MINUTES = int(
            external_settings["sedentary_reminder_minutes"]
        )
        config.SEDENTARY_BREAK_MINUTES = int(
            external_settings["sedentary_break_minutes"]
        )
        config.SEDENTARY_REMINDER_COOLDOWN_MINUTES = int(
            external_settings["sedentary_cooldown_minutes"]
        )
        config.SEDENTARY_POPUP_ENABLED = bool(
            external_settings["sedentary_popup_enabled"]
        )
        config.SEDENTARY_POPUP_TITLE = str(external_settings["sedentary_popup_title"])
        config.SEDENTARY_POPUP_MESSAGE = str(
            external_settings["sedentary_popup_message"]
        )
        config.SEDENTARY_POPUP_IMAGE_PATH = str(
            external_settings["sedentary_popup_image_path"]
        )
        config.SEDENTARY_POPUP_SNOOZE_MINUTES = int(
            external_settings["sedentary_popup_snooze_minutes"]
        )
        config.SEDENTARY_POPUP_AUTO_CLOSE_SECONDS = int(
            external_settings["sedentary_popup_auto_close_seconds"]
        )
        screen_sensor = getattr(self, "screen_sensor", None)
        if screen_sensor is not None:
            screen_sensor.sedentary_interval_sec = max(
                60, config.SEDENTARY_REMINDER_MINUTES * 60
            )
            screen_sensor.sedentary_cooldown_sec = max(
                60, config.SEDENTARY_REMINDER_COOLDOWN_MINUTES * 60
            )
            old_next_alert = float(
                getattr(screen_sensor, "next_sedentary_alert_time", 0) or 0
            )
            new_next_alert = time.time() + screen_sensor.sedentary_interval_sec
            screen_sensor.next_sedentary_alert_time = (
                min(old_next_alert, new_next_alert)
                if old_next_alert > 0
                else new_next_alert
            )
        result["sedentary_live_applied"] = True

        if self.skill_manager is not None:
            self.skill_manager.configure(
                enabled=external_settings.get("skill_enabled", True),
                search_paths=external_settings.get("skill_search_paths") or None,
                active_skills=external_settings.get("active_skills") or [],
            )
            result["skill_live_applied"] = True
            result["skill_count"] = len(self.skill_manager.skills)
            result["active_skills"] = list(self.skill_manager.active_skills)

        if self.mcp_bridge is not None:
            if external_settings["mcp_enabled"]:
                self._register_mcp_local_tools()
                result["mcp_servers"] = self.mcp_bridge.configure_remote_servers(
                    external_settings.get("mcp_server_configs") or []
                )
            else:
                self.mcp_bridge.clear_local_tools()
                self.mcp_bridge.clear_remote_servers()
            result["mcp_live_applied"] = True
            result["mcp_tools"] = self.get_mcp_tool_names()

        napcat_adapter = None
        group_no_at_keywords = []
        if self.plugin_manager is not None:
            try:
                group_no_at_keywords = self.plugin_manager.get_group_no_at_keywords()
            except Exception:
                group_no_at_keywords = []
        if self.chat_gateway is not None:
            napcat_adapter = NapCatOneBotAdapter(
                api_base=external_settings["napcat_api_base"],
                api_token=external_settings["napcat_api_token"],
                reply_enabled=external_settings["napcat_reply_enabled"],
                allow_group=external_settings["napcat_allow_group"],
                allow_private=external_settings["napcat_allow_private"],
                group_require_at=external_settings["napcat_group_require_at"],
                owner_user_ids=external_settings["napcat_owner_user_ids"],
                owner_label=external_settings["napcat_owner_label"],
                image_vision_enabled=external_settings["napcat_image_vision_enabled"],
                image_prompt=external_settings["napcat_image_prompt"],
                filter_mode=external_settings["napcat_filter_mode"],
                user_whitelist=external_settings["napcat_user_whitelist"],
                user_blacklist=external_settings["napcat_user_blacklist"],
                group_whitelist=external_settings["napcat_group_whitelist"],
                group_blacklist=external_settings["napcat_group_blacklist"],
                group_no_at_keywords=group_no_at_keywords,
            )
            self.chat_gateway.register_adapter(napcat_adapter)
            result["napcat_live_applied"] = True

        if self.chat_gateway_server:
            try:
                self.chat_gateway_server.stop()
            except Exception as e:
                if self.logger:
                    self.logger.warning(f"NapCat gateway stop failed: {e}")
            finally:
                self.chat_gateway_server = None

        if external_settings["napcat_enabled"] and self.chat_gateway and self.loop:
            try:
                self.chat_gateway_server = NapCatWebhookServer(
                    gateway=self.chat_gateway,
                    loop=self.loop,
                    host=external_settings["napcat_webhook_host"],
                    port=external_settings["napcat_webhook_port"],
                    path=external_settings["napcat_webhook_path"],
                    access_token=external_settings["napcat_access_token"],
                    logger=self.logger,
                )
                self.chat_gateway_server.start()
                if napcat_adapter is not None and hasattr(
                    napcat_adapter, "set_ws_action_sender"
                ):
                    napcat_adapter.set_ws_action_sender(
                        self.chat_gateway_server.call_action
                    )
                result["napcat_server_running"] = True
            except Exception as e:
                result["napcat_live_applied"] = False
                if self.logger:
                    self.logger.error(f"NapCat gateway start failed: {e}")

        if self.logger:
            self.logger.info(
                "External settings applied: "
                f"skills={external_settings['skill_enabled']} active={len(result['active_skills'])} loaded={result['skill_count']}, "
                f"mcp={external_settings['mcp_enabled']} tools={len(result['mcp_tools'])} servers={len(result['mcp_servers'])}, "
                f"napcat={external_settings['napcat_enabled']} server_running={result['napcat_server_running']}"
            )
        if self.chat_service is not None:
            self.chat_service.configure_gateway_voice_reply(
                enabled=external_settings.get("napcat_voice_reply_enabled", False),
                probability=external_settings.get("napcat_voice_reply_probability", 0),
                renderer=self.render_gateway_voice_reply,
            )

        return result

    def initialize(self):
        # Initialize application.
        # 1. 璁剧疆鏃ュ織
        try:
            from core.console_capture import install_console_capture

            install_console_capture("./logs/console.log")
        except Exception:
            pass
        from core.logger import setup_logging, set_logger

        self.logger = setup_logging(log_dir="./logs", log_name="agent", level="INFO")
        set_logger(self.logger)

        # 鍚姩鏃惰鍙?TTS 杩愯鏃跺紑鍏筹紙浼樺厛浜?config.py 榛樿鍊硷級
        self.tts_enabled = self._load_runtime_tts_enabled()
        try:
            import config as runtime_config

            runtime_config.TTS_ENABLED = self.tts_enabled
        except Exception:
            pass
        self.logger.info(f"TTS startup state: {'on' if self.tts_enabled else 'off'}")

        # 2. 鍒濆鍖栦簨浠舵棩蹇?
        self.event_logger = EventLogger("./data/events.sqlite")
        self.skill_manager = SkillManager(logger=self.logger)

        # 3. 鍒濆鍖栨牳蹇冪粍浠?
        self.brain = AdvancedMemorySystem()
        self.memory_store = get_memory_store()
        self.emotion_controller = EmotionController(mapping=EMO_TO_LIVE2D)
        self.logger.info("EmotionController 宸插垵濮嬪寲")

        # 4. 鍒濆鍖栨彃浠剁郴缁?
        self.plugin_manager = PluginManager(plugin_dir="./plugins")
        self.plugin_manager.load_plugins()

        self.tool_router = ToolRouter(
            react_map=self.plugin_manager.react_map,
            delegate_map=self.plugin_manager.delegate_map,
            direct_map=self.plugin_manager.direct_map,
        )

        # 5. 初始化TTS
        def _pick_edge_volume_str():
            if isinstance(TTS_VOLUME, str) and TTS_VOLUME.strip():
                return TTS_VOLUME.strip()
            return "+0%"

        def _pick_live2d_volume_float():
            if isinstance(TTS_VOLUME, (int, float)):
                try:
                    v = float(TTS_VOLUME)
                    return max(0.0, min(1.0, v))
                except Exception:
                    return 1.0
            return 1.0

        edge_cfg = {
            "voice": VOICE_NAME,
            "rate": TTS_RATE,
            "volume": _pick_edge_volume_str(),
            "enabled": True,
            "max_chars": TTS_MAX_CHARS,
            "use_live2d_player": TTS_USE_LIVE2D_PLAYER,
            "live2d_channel": TTS_CHANNEL,
            "live2d_volume": _pick_live2d_volume_float(),
            "enable_lip_sync": LIP_SYNC_ENABLED,
            "rhubarb_path": RHUBARB_PATH,
            "lip_sync_smooth_window": LIP_SYNC_SMOOTH_WINDOW,
        }

        if LIP_SYNC_ENABLED:
            rhubarb_abs = os.path.abspath(RHUBARB_PATH)
            if os.path.exists(rhubarb_abs):
                self.logger.info(
                    f"鍙ｅ瀷鍚屾宸插惎鐢?(Rhubarb: {rhubarb_abs}, 骞虫粦绐楀彛: {LIP_SYNC_SMOOTH_WINDOW})"
                )
            else:
                self.logger.warning(f"鍙ｅ瀷鍚屾宸插紑鍚絾 Rhubarb 涓嶅瓨鍦? {rhubarb_abs}")
        else:
            self.logger.info("鍙ｅ瀷鍚屾鏈惎鐢?(LIP_SYNC_ENABLED=0)")

        async def _bubble_to_event(
            text: str, emo: Optional[str], duration_ms: Optional[int]
        ):
            await self.event_bus.emit(
                Events.UI_BUBBLE, text=text, emotion=emo, duration_ms=duration_ms
            )

        async def _tts_go_idle_to_event():
            await self.event_bus.emit(Events.LIVE2D_GO_IDLE)

        self.tts = TTSRouter(
            edge_cfg=edge_cfg,
            verbose=True,
            log_each_utterance=True,
            bubble_sender=_bubble_to_event,
            go_idle_fn=_tts_go_idle_to_event,
            split_long_default=TTS_SPLIT_LONG_TEXT,
            chunk_chars_default=TTS_CHUNK_CHARS,
            state_machine=self.state_machine,
            enable_lip_sync=LIP_SYNC_ENABLED,
            rhubarb_path=RHUBARB_PATH,
            lip_sync_smooth_window=LIP_SYNC_SMOOTH_WINDOW,
        )
        try:
            from modules.character_manager import character_manager

            self.tts.apply_role_tts_config(character_manager.get_tts_config())
        except Exception:
            pass

        # 6. 初始化Presenter
        self.presenter = EventPresenter(
            tts_enabled=self.tts_enabled,
            speak_direct_result=False,
            verbose=True,
            event_bus=self.event_bus,
        )

        # 7. 鍒濆鍖栬亰澶╂湇鍔?
        self.mcp_bridge = MCPToolBridge()
        self.chat_gateway = ChatGateway()
        self.chat_gateway.on_message(self._handle_external_chat_message)
        self.chat_gateway.on_notice(self._handle_external_chat_notice)
        initial_external_settings = self._load_external_runtime_settings()
        self.apply_external_settings(initial_external_settings)

        self.chat_service = ChatService(
            brain=self.brain,
            plugin_manager=self.plugin_manager,
            tool_router=self.tool_router,
            presenter=self.presenter,
            event_bus=self.event_bus,
            logger=self.logger,
            chat_gateway=self.chat_gateway,
            mcp_bridge=self.mcp_bridge,
        )
        self.chat_service.skill_manager = self.skill_manager
        self.chat_service.app = self
        self._init_display_mqtt()
        self.chat_service.configure_gateway_voice_reply(
            enabled=initial_external_settings.get("napcat_voice_reply_enabled", False),
            probability=initial_external_settings.get(
                "napcat_voice_reply_probability", 0
            ),
            renderer=self.render_gateway_voice_reply,
        )

        gui_access_token = get_or_create_gui_access_token()
        self.gui_ws_server = GuiWebSocketServer(
            host=initial_external_settings.get("gui_ws_host", GUI_WS_HOST),
            port=initial_external_settings.get("gui_ws_port", GUI_WS_PORT),
            path=initial_external_settings.get("gui_ws_path", GUI_WS_PATH),
            logger=self.logger,
            access_token=gui_access_token,
        )
        self.gui_ws_server.set_message_handler(self._on_gui_ws_message)
        configure_live2d_transport(
            Live2DTransportBus(
                [
                    LegacyLocalWebSocketTransport(),
                    GuiWebSocketTransport(self.gui_ws_server),
                ],
                logger=self.logger,
            )
        )
        self.gui_http_server = GuiHttpServer(
            host=initial_external_settings.get("gui_http_host", GUI_HTTP_HOST),
            port=initial_external_settings.get("gui_http_port", GUI_HTTP_PORT),
            path_prefix=initial_external_settings.get("gui_http_prefix", GUI_HTTP_PREFIX),
            logger=self.logger,
            app_ref=self,
            access_token=gui_access_token,
        )
        if MusicSensor:
            self.music_sensor = MusicSensor(self.chat_service)

        # 8. 鍒濆鍖栧睆骞曟劅鐭?(蹇呴』鍦?chat_service 涔嬪悗)
        if ScreenSensor:
            try:
                self.screen_sensor = ScreenSensor(self.chat_service)
                self.chat_service.screen_sensor_ref = self.screen_sensor
                self.logger.info("ScreenSensor initialized")
            except Exception as e:
                self.logger.error(f"鉂?ScreenSensor 鍒濆鍖栧け璐? {e}")
        else:
            self.logger.warning("ScreenSensor module not loaded")

        # 馃煝 8.5 鍒濆鍖栬闊虫劅鐭?        self._init_voice_sensor_if_configured()

        # 9. 娉ㄥ唽浜嬩欢澶勭悊鍣?
        self._wire_events()

        # 10. 娉ㄥ唽鍒板鍣?
        self._register_services()

        self.logger.info("Application initialized")

    def _ensure_voice_sensor_loaded(self):
        if self.voice_sensor:
            return self.voice_sensor
        try:
            from modules.voice_sensor import VoiceSensor

            self.voice_sensor = VoiceSensor(
                chat_service=self.chat_service,
                event_bus=self.event_bus,
                config_path=getattr(config, "SHERPA_MODEL_CONFIG", {}),
            )
            if self.logger:
                self.logger.info("VoiceSensor loaded on demand")
            return self.voice_sensor
        except ImportError:
            if self.logger:
                self.logger.warning("modules.voice_sensor not found; voice disabled")
        except Exception as e:
            if self.logger:
                self.logger.error(f"鉂?VoiceSensor 鍒濆鍖栧け璐? {e}")
        self.voice_sensor = None
        return None

    def _init_voice_sensor_if_configured(self):
        if not getattr(config, "VOICE_SENSOR_ENABLED", False):
            self.voice_sensor = None
            if self.logger:
                self.logger.info("VoiceSensor disabled; voice module not preloaded")
            return None
        return self._ensure_voice_sensor_loaded()

    def _wire_events(self):
        # Connect events.
        # UI浜嬩欢
        self.event_bus.on(Events.UI_BUBBLE, self._on_ui_bubble)
        self.event_bus.on(Events.UI_STATUS, self._on_ui_status)
        self.event_bus.on(Events.UI_APPEND, self._on_ui_append)

        # 鐘舵€佷簨浠讹紙灏嗕簨浠舵€荤嚎鐨勭姸鎬佸彉鍖栬浆鎹负鐘舵€佹満鐘舵€侊級
        self.event_bus.on("state.changed", self._on_state_changed_event)

        # Live2D浜嬩欢
        self.event_bus.on(Events.LIVE2D_EMOTION, self._on_live2d_emotion)
        self.event_bus.on(Events.LIVE2D_MOTION, self._on_live2d_motion)
        self.event_bus.on(Events.LIVE2D_GO_IDLE, self._on_live2d_go_idle)

        # 鐘舵€佹満鐩戝惉鍣紙鐩存帴澶勭悊鐘舵€侊紝涓嶉€氳繃浜嬩欢鎬荤嚎锛?
        self.state_machine.add_listener(self._on_state_machine_change)

        # TTS浜嬩欢
        self.event_bus.on(Events.ASSISTANT_UTTER, self._on_assistant_utter)
        self.event_bus.on(Events.ASSISTANT_STREAM_START, self._on_stream_start)
        self.event_bus.on(Events.ASSISTANT_STREAM_FEED, self._on_stream_feed)
        self.event_bus.on(Events.ASSISTANT_STREAM_END, self._on_stream_end)

        # 鏃ュ織浜嬩欢
        self.event_bus.on(Events.CHAT_LOG, self._on_chat_log)
        self.event_bus.on(
            Events.MEMORY_ADD_OK, lambda p: print("[Memory] added")
        )
        self.event_bus.on(
            Events.MEMORY_ADD_FAIL, lambda p: print("[Memory] add failed")
        )

        # [鍙€塢 鐩戝惉琚拷鐣ョ殑娑堟伅
        self.event_bus.on(
            "chat.ignored",
            lambda p: print(f"馃毇 [Gatekeeper] 宸插拷鐣? {p.get('content', '')[:20]}..."),
        )

    async def _on_ui_bubble(self, payload: Dict[str, Any]):
        # Handle UI bubble event.
        self._emit_gui_ws(
            {
                "type": "bubble",
                "text": payload.get("text", ""),
                "emotion": payload.get("emotion"),
                "duration_ms": payload.get("duration_ms"),
            }
        )
        await send_bubble(
            payload.get("text", ""), payload.get("emotion"), payload.get("duration_ms")
        )

    def _on_ui_status(self, payload: Dict[str, Any]):
        # Handle UI status events.
        if self.qt_ui:
            try:
                self.qt_ui.set_status(payload.get("text", ""))
            except Exception as e:
                self.logger.warning(f"璁剧疆UI鐘舵€佸け璐? {e}")

        self._emit_gui_ws(
            {
                "type": "status",
                "text": payload.get("text", ""),
                "level": "info",
            }
        )

    def _on_ui_append(self, payload: Dict[str, Any]):
        # Handle UI append event.
        if self.qt_ui:
            try:
                self.qt_ui.append(
                    payload.get("role", "assistant"), payload.get("text", "")
                )
            except Exception as e:
                self.logger.warning(f"追加UI内容失败: {e}")

        self._emit_gui_ws(
            {
                "type": "log",
                "role": payload.get("role", "assistant"),
                "text": payload.get("text", ""),
            }
        )

    def _emit_gui_ws(self, payload: Dict[str, Any]):
        if self.gui_ws_server:
            self.gui_ws_server.emit(payload)

    def _build_gui_config(self) -> Dict[str, Any]:
        try:
            import config
        except Exception:
            return {"tts": True, "voice": False, "dnd": False}
        return {
            "tts": bool(getattr(config, "TTS_ENABLED", True)),
            "voice": bool(getattr(config, "VOICE_SENSOR_ENABLED", False)),
            "dnd": bool(getattr(config, "DND_MODE", False)),
        }

    def _build_gui_character(self) -> Dict[str, Any]:
        try:
            from modules.character_manager import character_manager
        except Exception:
            return {"name": "", "costume": ""}
        try:
            active_id = character_manager.data.get("active_id")
            char = (character_manager.data.get("characters") or {}).get(active_id or "")
            if not isinstance(char, dict):
                return {"name": "", "costume": ""}
            return {
                "name": str(char.get("name") or ""),
                "costume": str(char.get("current_costume") or ""),
            }
        except Exception:
            return {"name": "", "costume": ""}

    def _build_gui_costumes(self) -> Dict[str, Any]:
        try:
            from modules.character_manager import character_manager
        except Exception:
            return {"items": [], "current": ""}
        try:
            # Refresh from JSON so GUI does not depend on storage path.
            character_manager.load()
            active_id = character_manager.data.get("active_id")
            char = (character_manager.data.get("characters") or {}).get(active_id or "")
            costumes = (char or {}).get("costumes") or {}
            items = [{"name": k} for k in sorted(costumes.keys())]
            current = (
                character_manager.get_current_costume_name(active_id)
                or (char or {}).get("current_costume")
                or ""
            )
            return {"items": items, "current": str(current or "")}
        except Exception:
            return {"items": [], "current": ""}

    def _current_gui_status_text(self) -> str:
        state = getattr(self.state_machine, "state", None)
        if state == AgentState.THINKING:
            return "Thinking."
        if state == AgentState.SPEAKING:
            return "Speaking."
        return "Idle"

    async def _send_gui_snapshot(self, ws=None) -> None:
        if not self.gui_ws_server:
            return
        payloads = [
            {
                "type": "status",
                "text": self._current_gui_status_text(),
                "level": "info",
            },
            {"type": "config", **self._build_gui_config()},
            {"type": "character", **self._build_gui_character()},
            {"type": "costumes", **self._build_gui_costumes()},
        ]
        for payload in payloads:
            if ws is None:
                await self.gui_ws_server.broadcast(payload)
            else:
                await self.gui_ws_server.send(ws, payload)

    def _apply_mode_preset(self, preset: str) -> str:
        name = str(preset or "").strip().lower()
        if not name:
            return "mode preset is empty"
        try:
            from plugins.mode_preset.plugin import Plugin as ModePresetPlugin

            plugin = ModePresetPlugin()
            result = plugin._apply(name, {"chat_service": self.chat_service})
            try:
                import config as runtime_config

                self.tts_enabled = bool(
                    getattr(runtime_config, "TTS_ENABLED", self.tts_enabled)
                )
            except Exception:
                pass
            return str(result or "")
        except Exception as exc:
            return f"mode preset apply failed: {exc}"

    async def _on_gui_ws_message(self, payload: Dict[str, Any], ws) -> None:
        msg_type = str(payload.get("type") or "").strip().lower()
        if msg_type == "hello":
            await self._send_gui_snapshot(ws)
            return
        if msg_type != "command":
            return
        name = str(payload.get("name") or "").strip().lower()
        if not name:
            return
        if name == "send_text":
            text = str(payload.get("text") or "").strip()
            if text:
                self.on_gui_send(text, {"source": "tauri_gui"})
            return
        if name == "toggle_tts":
            desired = payload.get("value")
            if desired is None:
                desired = not bool(self.tts_enabled)
            self.set_tts_enabled(bool(desired))
            await self._send_gui_snapshot(ws)
            return
        if name == "toggle_voice":
            try:
                import config as runtime_config

                desired = payload.get("value")
                if desired is None:
                    desired = not bool(
                        getattr(runtime_config, "VOICE_SENSOR_ENABLED", False)
                    )
                runtime_config.VOICE_SENSOR_ENABLED = bool(desired)
                self.set_voice_sensor_enabled(bool(desired))
            except Exception:
                pass
            await self._send_gui_snapshot(ws)
            return
        if name == "toggle_dnd":
            try:
                import config as runtime_config

                desired = payload.get("value")
                if desired is None:
                    desired = not bool(getattr(runtime_config, "DND_MODE", False))
                runtime_config.DND_MODE = bool(desired)
            except Exception:
                pass
            await self._send_gui_snapshot(ws)
            return
        if name == "mode_status":
            await self._send_gui_snapshot(ws)
            return
        if name == "mode_preset":
            preset = payload.get("value") or payload.get("preset")
            result = self._apply_mode_preset(str(preset or "").strip())
            if result:
                await self.gui_ws_server.send(
                    ws, {"type": "log", "role": "system", "text": result}
                )
            await self._send_gui_snapshot(ws)
            return
        if name in {"reload_models", "reload_runtime", "reload_characters"}:
            status_text = ""
            level = "info"
            try:
                if name == "reload_models":
                    import config as runtime_config

                    runtime_config.load_custom_models(force=True)
                    status_text = "Models reloaded"
                elif name == "reload_runtime":
                    self.apply_external_settings()
                    status_text = "Runtime settings applied"
                else:
                    from modules.character_manager import character_manager

                    character_manager.load()
                    status_text = "Character data reloaded"
            except Exception as exc:
                status_text = f"Reload failed: {exc}"
                level = "error"
            try:
                await self.gui_ws_server.send(
                    ws, {"type": "status", "text": status_text, "level": level}
                )
            except Exception:
                pass
            await self._send_gui_snapshot(ws)
            return
        if name == "costume_list":
            try:
                payload = {"type": "costumes", **self._build_gui_costumes()}
                await self.gui_ws_server.send(ws, payload)
            except Exception as exc:
                await self.gui_ws_server.send(
                    ws,
                    {
                        "type": "status",
                        "text": f"Fetch costume list failed: {exc}",
                        "level": "error",
                    },
                )
            return
        if name == "costume_apply":
            costume_name = str(
                payload.get("value")
                or payload.get("costume")
                or payload.get("name")
                or ""
            ).strip()
            if not costume_name:
                await self.gui_ws_server.send(
                    ws,
                    {
                        "type": "status",
                        "text": "Costume name is empty",
                        "level": "warn",
                    },
                )
                return
            try:
                from modules.character_manager import character_manager

                active_id = character_manager.data.get("active_id")
                char = (character_manager.data.get("characters") or {}).get(
                    active_id or ""
                )
                costumes = (char or {}).get("costumes") or {}
                costume_entry = costumes.get(costume_name)
                if costume_entry is None:
                    await self.gui_ws_server.send(
                        ws,
                        {
                            "type": "status",
                            "text": "Costume not found",
                            "level": "warn",
                        },
                    )
                    return
                if isinstance(costume_entry, dict):
                    path = str(costume_entry.get("path") or "")
                else:
                    path = str(costume_entry)
                if not path:
                    await self.gui_ws_server.send(
                        ws,
                        {
                            "type": "status",
                            "text": "Costume path empty",
                            "level": "warn",
                        },
                    )
                    return
                runtime_cfg = character_manager.get_costume_runtime_config(
                    active_id, costume_name
                )
                character_manager.set_current_costume_name(active_id, costume_name)
                self.on_gui_change_costume(path, runtime_cfg)
                await self.gui_ws_server.send(
                    ws,
                    {
                        "type": "status",
                        "text": f"Switched to {costume_name}",
                        "level": "info",
                    },
                )
                await self.gui_ws_server.send(
                    ws,
                    {
                        "type": "character",
                        "name": str((char or {}).get("name") or ""),
                        "costume": costume_name,
                    },
                )
            except Exception as exc:
                await self.gui_ws_server.send(
                    ws,
                    {
                        "type": "status",
                        "text": f"Switch costume failed: {exc}",
                        "level": "error",
                    },
                )
            return
        if name in {"open_panel", "open_settings", "quick_costume"}:
            if not self.qt_ui:
                await self.gui_ws_server.send(
                    ws, {"type": "status", "text": "Qt GUI ??????????", "level": "warn"}
                )
                return
            try:
                if name == "open_settings":
                    if hasattr(self.qt_ui, "_on_settings_clicked"):
                        self.qt_ui._on_settings_clicked()
                    await self.gui_ws_server.send(
                        ws,
                        {
                            "type": "status",
                            "text": "Costume name is empty",
                            "level": "info",
                        },
                    )
                    return
                if name == "quick_costume":
                    if hasattr(self.qt_ui, "_on_quick_costume_clicked"):
                        self.qt_ui._on_quick_costume_clicked()
                    await self.gui_ws_server.send(
                        ws, {"type": "status", "text": "???????", "level": "info"}
                    )
                    return

                value = (
                    str(payload.get("value") or payload.get("panel") or "")
                    .strip()
                    .lower()
                )
                if value in {"panel", "main", "toggle"}:
                    if hasattr(self.qt_ui, "toggle_show_hide"):
                        self.qt_ui.toggle_show_hide()
                    await self.gui_ws_server.send(
                        ws,
                        {
                            "type": "status",
                            "text": "Costume not found",
                            "level": "info",
                        },
                    )
                    return
                if value == "codex":
                    if hasattr(self.qt_ui, "_on_codex_clicked"):
                        self.qt_ui._on_codex_clicked()
                    await self.gui_ws_server.send(
                        ws, {"type": "status", "text": "???????", "level": "info"}
                    )
                    return
                if value == "monitor":
                    if hasattr(self.qt_ui, "_on_monitor_clicked"):
                        self.qt_ui._on_monitor_clicked()
                    await self.gui_ws_server.send(
                        ws, {"type": "status", "text": "???????", "level": "info"}
                    )
                    return
                if value == "plugins":
                    if hasattr(self.qt_ui, "_on_plugin_clicked"):
                        self.qt_ui._on_plugin_clicked()
                    await self.gui_ws_server.send(
                        ws, {"type": "status", "text": "???????", "level": "info"}
                    )
                    return
                if value == "memory":
                    if hasattr(self.qt_ui, "_on_memory_clicked"):
                        self.qt_ui._on_memory_clicked()
                    await self.gui_ws_server.send(
                        ws, {"type": "status", "text": "???????", "level": "info"}
                    )
                    return
                if value == "settings":
                    if hasattr(self.qt_ui, "_on_settings_clicked"):
                        self.qt_ui._on_settings_clicked()
                    await self.gui_ws_server.send(
                        ws,
                        {
                            "type": "status",
                            "text": "Costume name is empty",
                            "level": "info",
                        },
                    )
                    return
                if value == "restart":
                    if hasattr(self.qt_ui, "_handle_restart"):
                        self.qt_ui._handle_restart()
                    else:
                        self.restart_app()
                    await self.gui_ws_server.send(
                        ws,
                        {
                            "type": "status",
                            "text": "Costume name is empty",
                            "level": "warn",
                        },
                    )
                    return

                await self.gui_ws_server.send(
                    ws, {"type": "status", "text": f"????: {value}", "level": "warn"}
                )
                return
            except Exception as exc:
                await self.gui_ws_server.send(
                    ws,
                    {
                        "type": "status",
                        "text": f"Switch costume failed: {exc}",
                        "level": "error",
                    },
                )
                return

    async def _on_live2d_emotion(self, payload: Dict[str, Any]):
        # Handle Live2D emotion events.
        emo = (payload.get("emotion") or "").strip()
        intensity = payload.get("intensity")
        prefer_motion = payload.get("prefer_motion")
        reason = payload.get("reason", "")

        if not emo:
            return

        try:
            self._emit_gui_ws(
                {
                    "type": "live2d",
                    "action": "emotion",
                    "emotion": emo,
                    "intensity": intensity,
                    "prefer_motion": prefer_motion,
                    "reason": reason,
                }
            )
            self.logger.debug(
                f"馃幁 [Emotion] 鏀跺埌鎯呯华璇锋眰: {emo} (prefer_motion={prefer_motion})"
            )

            # 鏍囪娲诲姩锛堥€€鍑虹┖闂叉ā寮忥級
            self.emotion_controller.mark_activity(reason or "emotion_request")

            # 閫氳繃 EmotionController 澶勭悊鎯呯华璇锋眰
            await self.emotion_controller.request_emotion(
                label=emo,
                intensity=intensity,
                prefer_motion=prefer_motion,
                reason=reason,
            )

            self.logger.debug(f"鉁?[Emotion] 鎯呯华澶勭悊瀹屾垚: {emo}")

        except Exception as e:
            self.logger.error(f"鉂?[Emotion] 澶勭悊澶辫触: {e}", exc_info=True)

    async def _on_live2d_motion(self, payload: Dict[str, Any]):
        # Handle Live2D motion events.
        m = (payload.get("motion") or "").strip()
        if not m:
            return
        try:
            self._emit_gui_ws(
                {
                    "type": "live2d",
                    "action": "motion",
                    "motion": m,
                }
            )
            await trigger_motion(m)
        except Exception:
            pass

    async def _on_live2d_go_idle(self, payload: Dict[str, Any]):
        # Handle Live2D idle events.
        self._emit_gui_ws({"type": "live2d", "action": "idle"})
        if self.emotion_controller:
            try:
                await self.emotion_controller.maybe_enter_idle()
            except Exception:
                pass
            return
        if callable(go_idle):
            try:
                await go_idle()
            except Exception:
                pass

    async def _on_state_changed_event(self, payload: dict):
        # Handle state changed events.
        state_name = payload.get("state")
        reason = payload.get("reason", "unknown")

        self.logger.debug(f"鏀跺埌鐘舵€佸彉鍖栦簨浠? {state_name} (鍘熷洜: {reason})")

        # 灏嗗瓧绗︿覆鐘舵€佽浆鎹负 AgentState 鏋氫妇
        state_map = {
            "idle": AgentState.IDLE,
            "thinking": AgentState.THINKING,
            "speaking": AgentState.SPEAKING,
        }

        target_state = state_map.get(state_name.lower() if state_name else "")
        if target_state:
            await self.state_machine.set_state(target_state, reason=reason)
        else:
            self.logger.warning(f"鏈煡鐘舵€? {state_name}")

    async def _on_state_machine_change(
        self, new_state: AgentState, prev_state: AgentState, meta: dict
    ):
        # Handle state machine transitions.
        reason = meta.get("reason", "unknown")
        self.logger.debug(
            f"馃攧 [State] {prev_state.value} -> {new_state.value} (鍘熷洜: {reason})"
        )

        try:
            # 鏇存柊 EmotionController 鐨勭姸鎬?
            self.emotion_controller.set_agent_state(new_state)

            # 鏍规嵁鐘舵€佽Е鍙戝搴旂殑鍔ㄤ綔
            if new_state == AgentState.THINKING:
                self.logger.debug("[State] enter thinking")

                # 寮傛瑙﹀彂 UI 鐘舵€佹洿鏂?
                asyncio.create_task(
                    self.event_bus.emit(Events.UI_STATUS, text="Thinking.")
                )

                if self.think_motion_enabled:
                    self.logger.debug(
                        f"馃幀 [State] 瑙﹀彂鎬濊€冨姩浣? {self.think_motion_name}"
                    )

                    # 鏍囪娲诲姩锛堥€€鍑虹┖闂诧級
                    self.emotion_controller.mark_activity("thinking")

                    # 鐩存帴閫氳繃 EmotionController 瑙﹀彂鍔ㄤ綔
                    asyncio.create_task(
                        self.emotion_controller.request_emotion(
                            label=self.think_motion_name,
                            prefer_motion=True,
                            reason="thinking_state",
                        )
                    )

            elif new_state == AgentState.SPEAKING:
                self.logger.debug("[State] enter speaking")
                asyncio.create_task(
                    self.event_bus.emit(Events.UI_STATUS, text="Speaking.")
                )

                # 璇磋瘽鏃舵爣璁版椿鍔?
                self.emotion_controller.mark_activity("speaking")

            elif new_state == AgentState.IDLE:
                self.logger.debug("[State] enter idle")
                asyncio.create_task(self.event_bus.emit(Events.UI_STATUS, text="Idle"))
                if reason not in {
                    "tts_disabled",
                    "tts_stream_disabled",
                    "all_done",
                }:
                    asyncio.create_task(self.event_bus.emit(Events.LIVE2D_GO_IDLE))

        except Exception as e:
            self.logger.error(f"鉂?[State] 鐘舵€佷簨浠堕敊璇? {e}", exc_info=True)

    async def _on_assistant_utter(self, payload: Dict[str, Any]):
        # Handle assistant utter event.
        text = (payload.get("text") or "").strip()
        if not text:
            return
        emotion = payload.get("emotion")
        interrupt = bool(payload.get("interrupt", True))
        show_bubble = bool(payload.get("show_bubble", True))
        speak = bool(payload.get("speak", True))

        if not speak:
            if show_bubble:
                try:
                    self._silent_bubble_seq += 1
                    bubble_seq = self._silent_bubble_seq
                    await self.state_machine.set_state(
                        AgentState.SPEAKING, reason="tts_disabled_preview"
                    )
                    from modules.live2d import send_lip_sync
                    from modules.text_lip_sync import (
                        build_text_lip_sync,
                        estimate_text_speech_duration,
                    )

                    fake_duration = estimate_text_speech_duration(text)
                    lip_data = build_text_lip_sync(text, fake_duration)
                    if lip_data:
                        await send_lip_sync(lip_data)
                    read_ms = estimate_bubble_display_ms(text)
                    duration_ms = max(int(fake_duration * 1000) + 600, int(read_ms))
                except Exception as exc:
                    self.logger.debug(f"Text lip sync fallback skipped: {exc}")
                    duration_ms = estimate_bubble_display_ms(text)
                    bubble_seq = self._silent_bubble_seq
                bubble_parts = split_local_bubble_text_parts(text) or [text]
                per_part_ms = max(1200, int(duration_ms / max(1, len(bubble_parts))))

                async def _emit_silent_bubbles(seq: int):
                    for part in bubble_parts:
                        if seq != self._silent_bubble_seq:
                            return
                        await self.event_bus.emit(
                            Events.UI_BUBBLE,
                            text=part,
                            emotion=emotion,
                            duration_ms=per_part_ms,
                        )
                        await asyncio.sleep(max(0.35, per_part_ms / 1000.0 + 0.08))

                asyncio.create_task(_emit_silent_bubbles(bubble_seq))
                if duration_ms and duration_ms > 0:
                    async def _idle_after_silent_bubble(delay_ms: int, seq: int):
                        total_delay = len(bubble_parts) * (per_part_ms / 1000.0 + 0.08)
                        await asyncio.sleep(max(0.35, total_delay + 0.18))
                        if seq != self._silent_bubble_seq:
                            return
                        if self.state_machine.state != AgentState.SPEAKING:
                            return
                        self.logger.debug("TTS disabled; set idle")
                        await self.state_machine.set_state(
                            AgentState.IDLE, reason="tts_disabled"
                        )
                        await self.event_bus.emit(Events.LIVE2D_GO_IDLE)

                    asyncio.create_task(
                        _idle_after_silent_bubble(duration_ms, bubble_seq)
                    )
                else:
                    self.logger.debug("TTS disabled; set idle")
                    await self.state_machine.set_state(
                        AgentState.IDLE, reason="tts_disabled"
                    )
                    await self.event_bus.emit(Events.LIVE2D_GO_IDLE)
            else:
                self.logger.debug("Silent output finished; UI only")
                await self.state_machine.set_state(
                    AgentState.IDLE, reason="tts_disabled"
                )
                await self.event_bus.emit(Events.UI_STATUS, text="Idle")
                await self.event_bus.emit(Events.LIVE2D_GO_IDLE)
            return

        self.logger.debug(f"TTS预览: {text[:50]}...")
        self._silent_bubble_seq += 1
        await self.tts.say(
            text, emotion=emotion, interrupt=interrupt, show_bubble=show_bubble
        )

    async def _on_stream_start(self, payload: Dict[str, Any]):
        # Handle stream start event.
        self._silent_bubble_seq += 1
        if not self.tts_enabled or not bool(payload.get("speak", True)):
            return
        self.tts.start_stream()

    async def _on_stream_feed(self, payload: Dict[str, Any]):
        # Handle stream chunk event.
        if not self.tts_enabled or not bool(payload.get("speak", True)):
            return
        chunk = payload.get("chunk") or ""
        if chunk:
            await self.tts.feed_stream(chunk, emotion=payload.get("emotion"))

    async def _on_stream_end(self, payload: Dict[str, Any]):
        # Handle stream end event.
        self.logger.debug(f"流结束，等待 TTS 播放完成")
        if not self.tts_enabled or not bool(payload.get("speak", True)):
            if bool(payload.get("show_bubble", True)):
                await self.state_machine.set_state(
                    AgentState.IDLE, reason="tts_stream_disabled"
                )
                await self.event_bus.emit(Events.LIVE2D_GO_IDLE)
            else:
                await self.event_bus.emit(Events.UI_STATUS, text="Idle")
                await self.event_bus.emit(Events.LIVE2D_GO_IDLE)
            return
        await self.tts.stop_stream(emotion=payload.get("emotion"))

        # 纭繚鐘舵€佸垏鎹㈠埌 IDLE锛堝鏋?TTS 娌℃湁姝ｅ父瑙﹀彂锛?
        # async def ensure_idle():
        #     await asyncio.sleep(2)  # 等待 TTS 播放完成
        #     if self.state_machine.state == AgentState.SPEAKING:
        #         self.logger.warning(f"TTS 未触发空闲，强制设置 IDLE")
        #         await self.state_machine.set_state(AgentState.IDLE, reason="force_idle")
        #
        # asyncio.create_task(ensure_idle())

    def restart_app(self):
        # Trigger restart flow.
        print("鈾伙笍 [App] 鎺ユ敹鍒伴噸鍚姹?..")

        if not self._watchdog_is_running():
            self._spawn_delayed_watchdog_restart()

        # 1. 鍏堝仛娓呯悊 (淇濆瓨鏃ヨ銆佸叧闂暟鎹簱杩炴帴绛?
        self.cleanup()

        # 2. 閫€鍑鸿繘绋嬶紝杩斿洖 100 缁欏畧鎶よ繘绋?
        import sys

        sys.exit(100)

    def _watchdog_is_running(self) -> bool:
        """Return True when the outer main.py watchdog owns its instance lock."""
        try:
            from core.single_instance import SingleInstanceLock

            lock = SingleInstanceLock(Path(__file__).resolve().parents[1], "watchdog")
            acquired = lock.acquire()
            if acquired:
                lock.release()
                return False
            return True
        except Exception as exc:
            self.logger.warning(f"Watchdog lock check failed; scheduling restart helper: {exc}")
            return False

    def _spawn_delayed_watchdog_restart(self) -> None:
        """Start main.py after the current core process releases its single-instance lock."""
        import subprocess
        import sys

        root = Path(__file__).resolve().parents[1]
        entry = root / "main.py"
        code = (
            "import subprocess, sys, time\n"
            "from pathlib import Path\n"
            "from core.single_instance import SingleInstanceLock\n"
            f"root = Path({str(root)!r})\n"
            f"entry = Path({str(entry)!r})\n"
            "deadline = time.time() + 30.0\n"
            "while time.time() < deadline:\n"
            "    lock = SingleInstanceLock(root, 'core')\n"
            "    if lock.acquire():\n"
            "        lock.release()\n"
            "        break\n"
            "    time.sleep(0.5)\n"
            "flags = getattr(subprocess, 'DETACHED_PROCESS', 0) | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)\n"
            "subprocess.Popen([sys.executable, str(entry)], cwd=str(root), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True, creationflags=flags)\n"
        )
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0
        )
        try:
            subprocess.Popen(
                [sys.executable, "-c", code],
                cwd=str(root),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                creationflags=flags,
            )
            self.logger.info("Scheduled delayed watchdog restart helper")
        except Exception as exc:
            self.logger.error(f"Failed to schedule delayed watchdog restart helper: {exc}")

    def _on_chat_log(self, payload: Dict[str, Any]):
        # Relay chat log to console and event log.
        role = payload.get("role", "unknown")
        content = payload.get("content", "")
        meta = payload.get("meta", {})

        try:
            source = str((meta or {}).get("source") or "").strip().lower()
            session_id = str((meta or {}).get("session_id") or "").strip()
            sender_name = str(
                (meta or {}).get("sender_name") or (meta or {}).get("user_id") or ""
            ).strip()

            if source in {"qq_gateway", "napcat_qq"}:
                channel_label = "QQ"
                if sender_name and role == "user":
                    channel_label = f"QQ:{sender_name}"
            else:
                channel_label = "LOCAL"

            role_map = {
                "user": "USER",
                "assistant": "BOT",
                "system": "SYS",
            }
            role_label = role_map.get(str(role).strip().lower(), str(role))

            text_line = str(content or "").replace("\r", " ").replace("\n", " ").strip()
            if len(text_line) > 240:
                text_line = text_line[:240] + "..."

            sid_suffix = f"[{session_id}]" if session_id else ""
            prefix = (
                "QQ-IN"
                if source in {"qq_gateway", "napcat_qq"} and role == "user"
                else "CHAT"
            )
            self.logger.info(
                f"[{prefix}]{sid_suffix}[{channel_label}][{role_label}] {text_line}"
            )
        except Exception:
            pass

        try:
            self.event_logger.add_message(role, content, meta)
        except Exception:
            pass

        try:
            session_id = str((meta or {}).get("session_id") or "").strip() or None
            store = self.memory_store
            if store is not None:
                brain_store = getattr(
                    getattr(self, "brain", None), "sqlite_store", None
                )
                if store is not brain_store:
                    store.add_transcript(role, content, meta, session_id=session_id)
        except Exception:
            pass

    def _register_services(self):
        # Register services in the container.
        self.container.register("event_bus", lambda c: self.event_bus)
        self.container.register("state_machine", lambda c: self.state_machine)
        self.container.register("chat_service", lambda c: self.chat_service)
        self.container.register("tts", lambda c: self.tts)
        self.container.register("plugin_manager", lambda c: self.plugin_manager)
        self.container.register("brain", lambda c: self.brain)
        self.container.register("emotion_controller", lambda c: self.emotion_controller)

        # [新增] 注册 screen_sensor (如果存在)
        if self.screen_sensor:
            self.container.register("screen_sensor", lambda c: self.screen_sensor)

    #  瀹氭椂浠诲姟璋冨害鍣?(澶勭悊鑷姩鏃ヨ + 琛ュ綍)
    async def _scheduler_loop(self):
        # Background scheduler loop.
        try:
            from config import AUTO_DIARY_ENABLED, AUTO_DIARY_TIME
        except ImportError:
            AUTO_DIARY_ENABLED = False
            AUTO_DIARY_TIME = "23:30"

        self.logger.info("鈴?瀹氭椂浠诲姟璋冨害鍣ㄥ凡鍚姩")

        # 鍐呭瓨鏍囪锛堥槻姝㈠崟娆¤繍琛屼腑姣?0绉掓煡涓€娆℃暟鎹簱锛屾氮璐规€ц兘锛?
        _last_makeup_check_date = None

        while True:
            try:
                now = datetime.now()
                current_time_str = now.strftime("%H:%M")
                current_date_str = now.strftime("%Y-%m-%d")

                # ================= 鍦烘櫙 A: 鍑嗙偣瑙﹀彂 (23:30) =================
                if AUTO_DIARY_ENABLED and current_time_str == AUTO_DIARY_TIME:
                    if self.memory_store:
                        today_stats = (
                            self.memory_store.get_daily_screen_stats(current_date_str)
                            or {}
                        )
                        if today_stats.get("diary_done") is True:
                            self.last_summary_date = current_date_str
                    if self.last_summary_date != current_date_str:
                        self.logger.info("Triggering daily summary")
                        if self.screen_sensor and self.chat_service:
                            report = self.screen_sensor.get_formatted_report()
                            if len(report) > 10:
                                diary_text = await self.chat_service.summarize_day(
                                    report, auto=True
                                )
                                if diary_text:
                                    self.last_summary_date = current_date_str
                                    self.logger.info("Daily diary archived")
                                else:
                                    self.logger.warning(
                                        f"⚠️ 今日日记生成失败，未写入归档标记: {current_date_str}"
                                    )

                # ================= 鍦烘櫙 B: 琛ュ綍鏄ㄥぉ (鍏ㄥぉ鍊欐鏌? =================
                # 馃煝 [淇敼] 鍘绘帀浜?and now.hour < 12
                # 鍙浠婂ぉ杩樻病妫€鏌ヨ繃琛ュ綍(鍐呭瓨鏍囪)锛屽氨鍘绘鏌ヤ竴娆?
                if AUTO_DIARY_ENABLED and _last_makeup_check_date != current_date_str:
                    _last_makeup_check_date = (
                        current_date_str  # 标记：今天已经检查过了，别再查了
                    )
                    await self._run_diary_backfill(now.date())

                await asyncio.sleep(30)
            except Exception as e:
                self.logger.error(f"璋冨害鍣ㄥ嚭閿? {e}")
                import traceback

                traceback.print_exc()
                await asyncio.sleep(60)

    # 閫€鍑烘竻鐞?(澶勭悊鍏虫満淇濆瓨)
    def cleanup(self):
        # Cleanup before application exit.
        print("馃洃 姝ｅ湪鍏抽棴搴旂敤...")

        # 🟢 停止语音监听，释放麦克风
        if self.voice_sensor:
            try:
                self.voice_sensor.stop()
            except Exception as e:
                print(f"鍏抽棴璇煶浼犳劅鍣ㄥ嚭閿? {e}")

        if self.plugin_manager and self.loop and self.loop.is_running():
            try:
                future = asyncio.run_coroutine_threadsafe(
                    self.plugin_manager.stop_all_plugins(), self.loop
                )
                future.result(timeout=3)
            except Exception as e:
                print(f"停止插件后台任务出错: {e}")

        if self.gui_ws_server:
            try:
                self.gui_ws_server.stop()
            except Exception as e:
                print(f"GUI WS shutdown error: {e}")
        if self.gui_http_server:
            try:
                self.gui_http_server.stop()
            except Exception as e:
                print(f"GUI HTTP shutdown error: {e}")
        # 灏濊瘯鏈€鍚庝竴娆′繚瀛橈紙濡傛灉浠婂ぉ杩樻病鍐欐棩璁帮級
        try:
            if self.chat_gateway_server:
                try:
                    self.chat_gateway_server.stop()
                except Exception as e:
                    print(f"鍏抽棴 NapCat webhook 鍑洪敊: {e}")
            current_date = datetime.now().strftime("%Y-%m-%d")
            # 濡傛灉閰嶇疆寮€鍚紝涓斾粖澶╄繕娌¤褰曪紝涓斾紶鎰熷櫒鏈夋暟鎹?
            if self.last_summary_date != current_date and self.screen_sensor:
                print("馃摑 妫€娴嬪埌閫€鍑烘椂浠婃棩灏氭湭鍐欐棩璁帮紝姝ｅ湪灏濊瘯淇濆瓨鏁版嵁...")
                report = self.screen_sensor.get_formatted_report()
                if len(report) > 20:
                    self.logger.info(f"銆愰€€鍑哄瓨妗ｃ€戜粖鏃ユ湭褰掓。鏁版嵁:\n{report}")
        except Exception as e:
            print(f"閫€鍑烘竻鐞嗗嚭閿? {e}")

    def _has_daily_log_for_date(self, date_str: str) -> bool:
        if not self.memory_store:
            return False
        try:
            episodes = self.memory_store.list_episodes(status="active", limit=500, offset=0)
        except Exception:
            return False
        target_tag = f"date:{date_str}"
        for episode in episodes:
            tags = episode.get("tags") or []
            if target_tag in tags and "daily_log" in tags:
                return True
        return False

    async def _run_diary_backfill(self, today_date: datetime.date, lookback_days: int = 7):
        if not self.memory_store or not self.chat_service:
            return
        max_days = max(1, int(lookback_days))
        for delta_days in range(1, max_days + 1):
            target_date = today_date - timedelta(days=delta_days)
            target_str = target_date.strftime("%Y-%m-%d")
            if self._has_daily_log_for_date(target_str):
                self.logger.info(f"{target_str} diary already exists; skip backfill")
                continue

            stats = self.memory_store.get_daily_screen_stats(target_str) or {}
            if stats.get("diary_done") is True:
                self.logger.info(f"{target_str} marked diary_done; skip backfill")
                continue

            summary_text = str(stats.get("summary_text") or "").strip()
            if len(summary_text) <= 10:
                self.logger.debug(f"{target_str} has no valid activity stats; skip backfill")
                continue

            if delta_days == 1:
                self.logger.info("Backfilling yesterday diary")
            else:
                self.logger.info(f"Backfilling diary for {target_str}")

            diary_text = await self.chat_service.summarize_day(
                summary_text,
                raw_stats=stats,
                auto=True,
                target_date=target_date,
            )
            if diary_text:
                if delta_days == 1:
                    self.logger.info(f"Yesterday diary backfill completed: {target_str}")
                else:
                    self.logger.info(f"Diary backfill completed: {target_str}")
            else:
                if delta_days == 1:
                    self.logger.warning(f"Yesterday diary backfill failed: {target_str}")
                else:
                    self.logger.warning(f"Diary backfill failed: {target_str}")

    # 鍔ㄦ€佸垏鎹㈣闊崇洃鍚殑鏂规硶
    def set_voice_sensor_enabled(self, enabled: bool):
        # Enable or disable voice sensor dynamically.
        if enabled:
            if not self.voice_sensor:
                self._ensure_voice_sensor_loaded()
            if not self.voice_sensor:
                self.logger.error("⚠️ VoiceSensor 未就绪，无法切换")
                return
            if not self.voice_sensor.running and self.loop:
                self.logger.info("Voice sensor enabled")
                self.voice_sensor.start(self.loop)
        else:
            if self.voice_sensor and self.voice_sensor.running:
                self.logger.info("Voice sensor disabled")
                self.voice_sensor.stop()

    def start_async_loop(self):
        # Start async event loop.
        self.logger.info("Starting async event loop")

        def async_worker():
            # 1. 创建事件循环
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)

            # 2. 鍚姩鎯呯华鎺у埗鍣?
            if self.emotion_controller:
                try:
                    self.emotion_controller.start(self.loop)
                    self.logger.info("Emotion controller loop started")
                except Exception as e:
                    self.logger.error(f"鉂?鎯呯华鎺у埗鍣ㄥ惎鍔ㄥけ璐? {e}")

            # 3. 鍚姩鎵€鏈夋彃浠?
            self.loop.create_task(
                self.plugin_manager.start_all_plugins(
                    {"chat_service": self.chat_service}
                )
            )

            # 4. 鍚姩瀹氭椂璋冨害鍣?
            self.loop.create_task(self._scheduler_loop())

            # 6. 启动音乐感知
            if self.music_sensor:
                try:
                    self.logger.info("Starting MusicSensor thread")
                    self.music_sensor.start(self.loop)
                except Exception as e:
                    self.logger.error(f"鉂?MusicSensor 鍚姩澶辫触: {e}")

            # 馃煝 7. 鍚姩璇煶鐩戝惉 (濡傛灉閰嶇疆涓哄紑鍚?
            import config

            if getattr(config, "VOICE_SENSOR_ENABLED", False):
                try:
                    if not self.voice_sensor:
                        self._ensure_voice_sensor_loaded()
                    if not self.voice_sensor:
                        raise RuntimeError("VoiceSensor is not ready")
                    self.logger.info("Starting VoiceSensor thread")
                    self.voice_sensor.start(self.loop)
                except Exception as e:
                    self.logger.error(f"鉂?VoiceSensor 鍚姩澶辫触: {e}")

            # 8. 运行循环
            try:
                self.apply_external_settings()
            except Exception as e:
                self.logger.error(f"External settings apply failed: {e}")
            self.loop.create_task(
                self._sync_active_character_qq_profile(reason="startup")
            )
            if self.gui_ws_server:
                self.loop.create_task(self.gui_ws_server.start(self.loop))
            if self.gui_http_server:
                try:
                    self.gui_http_server.start()
                    self._publish_gui_activity_endpoint()
                except Exception as exc:
                    if self.logger:
                        self.logger.error(f"GUI HTTP start failed: {exc}")
            if self.logger:
                self.logger.info("Activity source: Live2D/Tauri activity ingest")

            # 5. 启动屏幕感知；久坐采集只消费 Live2D Rust 上报
            if self.screen_sensor:
                try:
                    self.screen_sensor.use_rust_events_only = True
                    self.logger.info(
                        "Live2D/Tauri activity ingest enabled; Python window polling disabled"
                    )
                    self.screen_sensor.start(self.loop)
                except Exception as e:
                    self.logger.error(f"鉂?ScreenSensor 鍚姩澶辫触: {e}")

            self.loop.run_forever()

        t = threading.Thread(target=async_worker, daemon=True)
        t.start()
        self.logger.info("Async event loop started")

    def on_gui_send(self, text: str, ctx: Optional[Dict[str, Any]] = None):
        # GUI send callback.
        if self.loop is None:
            print("[Application] loop is not initialized; ignoring input")
            return

        async def _process():
            # 榛樿鏉ヨ嚜鏂囨湰杈撳叆锛屽彲琚閮ㄧ獥鍙ｈ鐩栦负 codex_input 绛夋潵婧?
            merged_ctx: Dict[str, Any] = {"source": "text_input"}
            if isinstance(ctx, dict):
                merged_ctx.update(ctx)
                merged_ctx.setdefault("source", "text_input")
            try:
                await self.chat_service.process(text, ctx=merged_ctx)
            except Exception:
                try:
                    output_profile = build_output_profile(
                        str(merged_ctx.get("source") or "text_input")
                    )
                    if output_profile.get("live2d_enabled", True):
                        await self.event_bus.emit(
                            "state.changed", state="idle", reason="process_error"
                        )
                        await self.event_bus.emit(Events.LIVE2D_GO_IDLE)
                    await self.event_bus.emit(Events.UI_STATUS, text="Idle")
                except Exception:
                    pass
                raise

        fut = asyncio.run_coroutine_threadsafe(_process(), self.loop)

        def _done(f):
            try:
                f.result()
            except Exception as e:
                self.logger.error(f"鍗忕▼寮傚父: {repr(e)}")

        fut.add_done_callback(_done)

    def on_external_message(
        self,
        text: str,
        *,
        source: str = "qq_gateway",
        channel: str = "qq",
        metadata: Optional[Dict[str, Any]] = None,
    ):
        # Handle external channel messages.
        self.on_gui_send(
            text,
            {
                "source": source,
                "channel": channel,
                "channel_meta": metadata or {},
            },
        )

    async def dispatch_gateway_payload(
        self, adapter_name: str, payload: Dict[str, Any]
    ):
        if not self.chat_gateway:
            raise RuntimeError("Chat gateway not initialized")
        return await self.chat_gateway.dispatch_incoming(adapter_name, payload or {})

    async def _handle_external_chat_message(self, event):
        self.on_external_message(
            event.text,
            source=event.source,
            channel=event.channel,
            metadata={
                "session_id": event.session_id,
                "user_id": event.user_id,
                **(event.metadata or {}),
            },
        )

    async def _handle_external_chat_notice(self, event):
        service = getattr(self, "chat_service", None)
        if service is None or not hasattr(service, "handle_external_chat_notice"):
            return
        await service.handle_external_chat_notice(event)

    def on_gui_change_costume(self, path: str, config: dict):
        # GUI costume change callback.
        if self.loop is None:
            return

        async def _do():
            try:
                await change_costume(path, config)
                if not (isinstance(config, dict) and config.get("preview_mode")):
                    await asyncio.to_thread(
                        self.brain.add_memory,
                        "system",
                        f"用户为你更换了服装，文件路径为: {path}",
                    )
            except Exception as e:
                self.logger.error(f"换装失败: {e}")

        asyncio.run_coroutine_threadsafe(_do(), self.loop)

    def sync_active_character_live2d(self):
        try:
            from modules.character_manager import character_manager

            active_id = character_manager.data.get("active_id")
            if not active_id:
                return False
            active_char = character_manager.get_character(active_id) or {}
            current_costume = character_manager.get_current_costume_name(active_id)
            if not current_costume:
                return False
            costume_cfg = (active_char.get("costumes") or {}).get(current_costume) or {}
            costume_path = str(costume_cfg.get("path") or "").strip()
            if not costume_path:
                return False
            runtime_cfg = character_manager.get_costume_runtime_config(
                active_id, current_costume
            )
            self.on_gui_change_costume(costume_path, runtime_cfg)
            return True
        except Exception as e:
            if self.logger:
                self.logger.warning(f"同步当前角色 Live2D 失败: {e}")
            return False

    def _character_qq_profile_config(self, char: dict) -> Dict[str, str]:
        if not isinstance(char, dict):
            return {}
        raw = char.get("qq_profile")
        if not isinstance(raw, dict):
            return {}
        enabled = bool(raw.get("enabled", True))
        nickname = str(raw.get("nickname") or raw.get("nick") or "").strip()
        avatar = str(
            raw.get("avatar_path")
            or raw.get("avatar")
            or raw.get("avatar_url")
            or raw.get("avatar_file")
            or ""
        ).strip()
        if not enabled or not (nickname or avatar):
            return {}
        # Role switching only manages QQ nickname/avatar. Do not carry profile
        # fields such as signature/status/personal_note into NapCat actions.
        profile: Dict[str, str] = {}
        if nickname:
            profile["nickname"] = nickname
        if avatar:
            profile["avatar"] = avatar
        return profile

    def _resolve_role_qq_avatar_value(self, avatar: str) -> str:
        value = str(avatar or "").strip()
        if not value:
            return ""
        lowered = value.lower()
        if lowered.startswith(("http://", "https://", "file://", "base64://")):
            return value
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = Path(__file__).resolve().parent.parent / path
        return str(path.resolve())

    def _schedule_character_qq_profile_sync(self, char_id: str, char: dict) -> None:
        profile = self._character_qq_profile_config(char)
        if not profile:
            if self.logger:
                self.logger.info(f"[RoleQQSync] skipped: qq_profile disabled or empty for {char_id}")
            return
        if self.loop is None or not self.loop.is_running():
            if self.logger:
                self.logger.warning("[RoleQQSync] skipped: async loop not running")
            return

        async def _run():
            await self._sync_character_qq_profile(char_id, profile)

        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None
        if current_loop is self.loop:
            self.loop.create_task(_run())
        else:
            asyncio.run_coroutine_threadsafe(_run(), self.loop)

    async def _sync_active_character_qq_profile(self, reason: str = "startup") -> bool:
        try:
            from modules.character_manager import character_manager

            active_id = character_manager.data.get("active_id")
            if not active_id:
                if self.logger:
                    self.logger.info(f"[RoleQQSync] skipped: no active character ({reason})")
                return False
            char = character_manager.get_character(active_id) or {}
            profile = self._character_qq_profile_config(char)
            if not profile:
                if self.logger:
                    self.logger.info(
                        f"[RoleQQSync] skipped: qq_profile disabled or empty for active {active_id} ({reason})"
                    )
                return False
            if self.logger:
                self.logger.info(
                    f"[RoleQQSync] syncing active character {active_id} ({reason})"
                )
            await self._sync_character_qq_profile(active_id, profile)
            return True
        except Exception as exc:
            if self.logger:
                self.logger.warning(
                    f"[RoleQQSync] active character sync failed ({reason}): {exc}"
                )
            return False

    def _napcat_action_data(self, result: Any) -> Dict[str, Any]:
        if not isinstance(result, dict):
            return {}
        response = result.get("response")
        if isinstance(response, dict):
            data = response.get("data")
            if isinstance(data, dict):
                return data
            return response
        data = result.get("data")
        return data if isinstance(data, dict) else {}

    async def _call_napcat_action(
        self, action: str, params: Optional[Dict[str, Any]] = None, timeout: float = 8.0
    ) -> Dict[str, Any]:
        last_result: Dict[str, Any] = {}
        server = getattr(self, "chat_gateway_server", None)
        if server is not None and hasattr(server, "call_action"):
            try:
                result = await server.call_action(action, params or {}, timeout=timeout)
                if isinstance(result, dict):
                    last_result = result
                    if result.get("ok"):
                        return result
            except Exception as exc:
                last_result = {"ok": False, "reason": str(exc), "action": action}

        gateway = getattr(self, "chat_gateway", None)
        adapter = None
        try:
            adapter = getattr(gateway, "adapters", {}).get("napcat_qq")
        except Exception:
            adapter = None
        if adapter is not None and hasattr(adapter, "call_action"):
            try:
                result = await adapter.call_action(
                    action, params or {}, timeout=timeout
                )
                if isinstance(result, dict):
                    return result
            except Exception as exc:
                return {"ok": False, "reason": str(exc), "action": action}

        return last_result or {
            "ok": False,
            "reason": "napcat_action_unavailable",
            "action": action,
        }

    async def _sync_character_qq_profile(
        self, char_id: str, profile: Dict[str, str]
    ) -> None:
        nickname = str(profile.get("nickname") or "").strip()
        avatar = self._resolve_role_qq_avatar_value(profile.get("avatar") or "")
        try:
            if nickname:
                current_result = await self._call_napcat_action("get_login_info", {})
                current_data = self._napcat_action_data(current_result)
                current_nickname = str(current_data.get("nickname") or "").strip()
                if current_nickname == nickname:
                    if self.logger:
                        self.logger.info(
                            f"[RoleQQSync] nickname already matched for {char_id}: {nickname}"
                        )
                else:
                    # Only send nickname here. QQ signature/status/说说 are not
                    # part of role sync and must not be changed implicitly.
                    result = await self._call_napcat_action(
                        "set_qq_profile", {"nickname": nickname}
                    )
                    if self.logger:
                        if result.get("ok"):
                            self.logger.info(
                                f"[RoleQQSync] nickname synced for {char_id}: {current_nickname or '-'} -> {nickname}"
                            )
                        else:
                            self.logger.warning(
                                f"[RoleQQSync] nickname sync failed for {char_id}: {result}"
                            )

            if avatar:
                result = await self._call_napcat_action(
                    "set_qq_avatar", {"file": avatar}, timeout=15.0
                )
                if self.logger:
                    if result.get("ok"):
                        self.logger.info(
                            f"[RoleQQSync] avatar synced for {char_id}: {avatar}"
                        )
                    else:
                        self.logger.warning(
                            f"[RoleQQSync] avatar sync failed for {char_id}: {result}"
                        )
        except Exception as exc:
            if self.logger:
                self.logger.warning(f"[RoleQQSync] sync failed for {char_id}: {exc}")

    def switch_character_runtime(self, char_id: str) -> bool:
        try:
            from modules.character_manager import character_manager

            char = character_manager.set_active_character(char_id)
            if not char:
                return False
            try:
                self.tts.apply_role_tts_config(
                    character_manager.get_tts_config(char_id)
                )
            except Exception:
                pass
            try:
                current_costume = character_manager.get_current_costume_name(char_id)
                self.logger.info(
                    f"[RoleSync] 鍒囨崲瑙掕壊: {char_id} -> {char.get('name', char_id)} | costume={current_costume}"
                )
                if getattr(self, "qt_ui", None):
                    if current_costume and hasattr(
                        self.qt_ui, "trigger_costume_by_name"
                    ):
                        self.qt_ui.trigger_costume_by_name(current_costume)
                    elif hasattr(self.qt_ui, "sync_active_character_visual"):
                        self.qt_ui.sync_active_character_visual()
                    if hasattr(self.qt_ui, "refresh_character_status"):
                        self.qt_ui.refresh_character_status()
                else:
                    self.sync_active_character_live2d()
            except Exception:
                pass
            self._schedule_character_qq_profile_sync(char_id, char)
            return True
        except Exception as e:
            if self.logger:
                self.logger.warning(f"鍒囨崲瑙掕壊杩愯鏃跺け璐? {e}")
            return False

    def on_gui_preview_motion(self, motion_name: str, motion_type: int = 0):
        # GUI motion preview callback.
        if self.loop is None or not motion_name:
            return

        async def _do():
            try:
                await play_motion(str(motion_name), motion_type=int(motion_type))
            except Exception as e:
                self.logger.error(f"预览动作失败: {e}")

        asyncio.run_coroutine_threadsafe(_do(), self.loop)

    def on_gui_preview_expression(self, exp_value):
        # GUI expression preview callback.
        if self.loop is None:
            return

        async def _do():
            try:
                await set_expression(int(exp_value))
            except Exception as e:
                self.logger.error(f"预览表情失败: {e}")

        asyncio.run_coroutine_threadsafe(_do(), self.loop)

    def set_tts_enabled(self, enabled: bool):
        # Configure TTS switch.
        enabled = bool(enabled)
        self.tts_enabled = enabled
        if getattr(self, "tts", None):
            self.tts.enabled = enabled

        # 淇濇寔 config 杩愯鏃剁姸鎬佷笌 UI 鏄剧ず涓€鑷?
        try:
            import config as runtime_config

            runtime_config.TTS_ENABLED = enabled
        except Exception:
            pass

        if self.presenter:
            self.presenter.set_tts_enabled(enabled)

        settings = self._load_runtime_settings()
        settings["tts_enabled"] = enabled
        self._save_runtime_settings(settings)

    def set_think_motion_enabled(self, enabled: bool):
        # Configure thinking motion switch.
        self.think_motion_enabled = bool(enabled)
        self.logger.info(f"THINK_MOTION_ENABLED = {self.think_motion_enabled}")

    def run(self):
        # Run application.
        try:
            # 鍒濆鍖?
            self.initialize()

            self.logger.info("=== 浜斿崄閾冩€?Live2D Agent 鍚姩 ===")

            # 启动异步循环
            self.start_async_loop()

            # 鏍规嵁閰嶇疆閫夋嫨GUI
            backend = (
                getattr(config, "GUI_BACKEND", GUI_BACKEND) or "auto"
            ).strip().lower()
            self._runtime_mode = backend

            if backend == "tk":
                self._run_tk_gui()
            elif backend == "qt":
                try:
                    self._run_qt_gui()
                except Exception as e:
                    self.logger.warning(f"Qt鍚姩澶辫触锛屽洖閫€鍒癟k: {e}")
                    self._run_tk_gui()
            elif backend == "headless":
                self._run_headless()
            else:  # auto
                try:
                    self._run_qt_gui()
                except Exception as e:
                    self.logger.warning(f"Qt不可用，使用Tk: {e}")
                    self._run_tk_gui()

        except KeyboardInterrupt:
            pass
        finally:
            # 馃煝 閫€鍑烘椂瑙﹀彂娓呯悊
            self.cleanup()
        return self._requested_exit_code

    def _run_headless(self):
        self.logger.info("Headless runtime started; desktop GUI is disabled")
        self._headless_stop_event.wait()

    def request_runtime_control(self, action: str) -> tuple[bool, str]:
        action = str(action or "").strip().lower()
        if action not in {"shutdown", "restart"}:
            return False, "invalid_action"
        if self._runtime_mode != "headless":
            return False, "headless_required"
        self._requested_exit_code = 100 if action == "restart" else 0
        self._headless_stop_event.set()
        return True, ""

    def _run_tk_gui(self):
        # Run Tk GUI.
        from modules.gui.gui import ChatWindow

        window = ChatWindow(
            on_send_callback=self.on_gui_send,
            on_tts_toggle_callback=self.set_tts_enabled,
            on_think_toggle_callback=self.set_think_motion_enabled,
        )
        window.run()

    def _run_qt_gui(self):
        # Run Qt GUI.
        global qt_ui
        import config
        from modules.qt_gui import QtChatTrayApp, QtGuiConfig

        self.qt_ui = QtChatTrayApp(
            on_send_callback=self.on_gui_send,
            on_tts_toggle_callback=self.set_tts_enabled,
            on_voice_toggle_callback=self.set_voice_sensor_enabled,  # 馃煝 浼犻€掕闊虫帶鍒跺洖璋?
            on_costume_callback=self.on_gui_change_costume,
            on_preview_motion_callback=self.on_gui_preview_motion,
            on_preview_expression_callback=self.on_gui_preview_expression,
            plugin_manager=self.plugin_manager,
            on_restart_callback=self.restart_app,
            on_apply_external_settings_callback=self.apply_external_settings,
            on_display_state_callback=self.publish_display_state,
            cfg=QtGuiConfig(
                title="Live2D Agent",
                start_minimized_to_tray=bool(getattr(config, 'START_MINIMIZED_TO_TRAY', True)),
            ),
        )
        self.qt_ui.brain = self.brain
        self.qt_ui.tts = self.tts
        self.qt_ui.loop = self.loop
        self.qt_ui.chat_service = self.chat_service
        self.qt_ui.app = self
        self.qt_ui.load_display_state_config = self.load_display_state_config
        self.qt_ui.save_display_state_config = self.save_display_state_config
        self.qt_ui.publish_display_state = self.publish_display_state
        if self.screen_sensor and hasattr(self.qt_ui, "set_screen_sensor"):
            self.qt_ui.set_screen_sensor(self.screen_sensor)
        if self.screen_sensor and hasattr(self.qt_ui, "show_sedentary_popup"):
            self.screen_sensor.set_sedentary_popup_callback(
                self.qt_ui.show_sedentary_popup
            )
            self.screen_sensor.set_sedentary_meme_selector(
                self.select_sedentary_meme_image_path
            )
        self.qt_ui.set_status("Idle")
        try:
            self.logger.info("[RoleSync] syncing active character visual on startup")
            if hasattr(self.qt_ui, "sync_active_character_visual"):
                self.qt_ui.sync_active_character_visual()
            else:
                self.sync_active_character_live2d()
        except Exception:
            pass
        try:
            if hasattr(self.qt_ui, "publish_display_snapshot"):
                self.qt_ui.publish_display_snapshot()
        except Exception:
            pass
        self.qt_ui.run()

    def _init_display_mqtt(self):
        if not MQTT_DISPLAY_ENABLED or mqtt is None:
            self.display_mqtt_last_error = (
                "鏈惎鐢?MQTT_DISPLAY"
                if not MQTT_DISPLAY_ENABLED
                else "缂哄皯 paho-mqtt 渚濊禆"
            )
            return
        try:
            client = mqtt.Client(client_id="live2d-llm-display-pub")
            client.connect(MQTT_DISPLAY_HOST, MQTT_DISPLAY_PORT, 30)
            client.loop_start()
            self.display_mqtt_client = client
            self.display_mqtt_last_error = ""
            if self.logger:
                self.logger.info(
                    f"Display MQTT connected: {MQTT_DISPLAY_HOST}:{MQTT_DISPLAY_PORT} -> {MQTT_DISPLAY_TOPIC}"
                )
        except Exception as e:
            self.display_mqtt_client = None
            self.display_mqtt_last_error = str(e)
            if self.logger:
                self.logger.warning(f"Display MQTT init failed: {e}")

    def publish_display_state(self, payload: Dict[str, Any]):
        if not self.display_mqtt_client:
            return
        try:
            cfg = self.load_display_state_config()
            role = str(payload.get("role") or "未激活角色")
            status = str(payload.get("status") or "Ready")
            emotion = str(payload.get("emotion") or "[idle]")
            metric_mode = str(cfg.get("metric_mode") or "auto_ram").strip().lower()
            metric = str(payload.get("metric") or "").strip()
            if not metric:
                if metric_mode == "custom":
                    metric = str(cfg.get("metric_text") or "").strip() or "--"
                elif metric_mode == "status_priority":
                    status_lower = status.lower()
                    if "speak" in status_lower:
                        metric = "Speaking"
                    elif "think" in status_lower:
                        metric = "Thinking"
                    elif "listen" in status_lower:
                        metric = "Listening"
                    elif psutil is not None:
                        try:
                            metric = f"RAM {int(psutil.virtual_memory().percent)}%"
                        except Exception:
                            metric = "RAM --"
                else:
                    metric = "RAM --"
                    if psutil is not None:
                        try:
                            metric = f"RAM {int(psutil.virtual_memory().percent)}%"
                        except Exception:
                            pass
            message = {
                "role": role,
                "emotion": emotion,
                "status": status,
                "metric": metric,
            }
            emotion_key = emotion.strip().strip("[]").lower()
            emotion_icons = cfg.get("emotion_icons") or {}
            icon_payload = emotion_icons.get(emotion_key) or {}
            if not icon_payload:
                icon_payload = {
                    "icon_bits": cfg.get("default_icon_bits", ""),
                    "icon_rgb565": cfg.get("default_icon_rgb565", ""),
                    "icon_w": cfg.get("default_icon_w", 0),
                    "icon_h": cfg.get("default_icon_h", 0),
                }
            for key in ("icon_bits", "icon_rgb565", "icon_w", "icon_h"):
                if key in payload:
                    message[key] = payload[key]
                elif icon_payload.get(key):
                    message[key] = icon_payload.get(key)
            self.display_mqtt_client.publish(
                MQTT_DISPLAY_TOPIC,
                json.dumps(message, ensure_ascii=False),
                qos=0,
                retain=False,
            )
            if self.logger:
                self.logger.debug(f"[DisplayMQTT] {message}")
        except Exception as e:
            if self.logger:
                self.logger.debug(f"Display MQTT publish failed: {e}")

    def is_display_mqtt_ready(self) -> bool:
        return self.display_mqtt_client is not None

    def get_display_mqtt_status_text(self) -> str:
        if self.display_mqtt_client is not None:
            return "已连接 Mosquitto，可推送"
        return self.display_mqtt_last_error or "MQTT 未连接"


class EventPresenter:
    # Event presenter.

    def __init__(
        self,
        tts_enabled: bool = True,
        speak_direct_result: bool = False,
        verbose: bool = True,
        event_bus: EventBus = None,
    ):
        self.tts_enabled = bool(tts_enabled)
        self.speak_direct_result = bool(speak_direct_result)
        self.verbose = bool(verbose)
        self.event_bus = event_bus
        # 瀹归敊娓呯悊锛氶槻姝㈡ā鍨嬭緭鍑洪潪鏍囧噯鎯呯华鏍囩琚?TTS 璇诲嚭鏉?
        self._emo_tag_any_re = re.compile(
            r"<\s*/?\s*(?:emo(?:tion)?|happy|sad|angry|shy|flustered|confused|neutral|think|idle)\b[^>]*>",
            flags=re.IGNORECASE,
        )

    def set_tts_enabled(self, enabled: bool):
        # Configure TTS switch.
        self.tts_enabled = bool(enabled)
        if self.verbose:
            # 杩欓噷娌℃湁 logger锛屼娇鐢?event_bus 鍙戦€佹棩蹇?
            print(f"[Presenter] TTS: {'on' if self.tts_enabled else 'off'}")

        # 🟢 [修改] 增加 interrupt 参数

    async def present(
        self,
        text: str,
        emotion: Optional[str] = None,
        *,
        speak: Optional[bool] = None,
        interrupt: bool = True,
        show_bubble: bool = True,
    ):
        # Present text.
        text = (text or "").strip()
        if not text:
            return
        text = self._emo_tag_any_re.sub("", text).strip()
        if not text:
            return
        want_speak = bool(self.tts_enabled) and (
            True if speak is None else bool(speak)
        )

        await self.event_bus.emit(
            Events.ASSISTANT_UTTER,
            text=text,
            emotion=emotion,
            interrupt=interrupt,  # <--- 浣跨敤浼犲叆鐨勫弬鏁帮紝鑰屼笉鏄啓姝?True
            speak=want_speak,
            show_bubble=bool(show_bubble),
        )

    async def present_direct(self, text: str, emotion: Optional[str] = None):
        # Present direct tool text.
        await self.present(text, emotion=emotion, speak=self.speak_direct_result)
