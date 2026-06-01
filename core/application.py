"""
应用主类
管理整个应用的生命周期和组件初始化
"""

import asyncio
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Optional, Any, Dict
from datetime import datetime
from datetime import timedelta

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
    MQTT_DISPLAY_ENABLED,
    MQTT_DISPLAY_HOST,
    MQTT_DISPLAY_PORT,
    MQTT_DISPLAY_TOPIC,
)

from modules.advanced_memory import AdvancedMemorySystem
from modules.memory_sqlite import get_memory_store
from modules.emotion_controller import EmotionController
from modules.activity_sidecar import ActivitySidecarManager
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

# [新增] 尝试导入屏幕感知模块 (容错处理)
try:
    from modules.screen_sensor import ScreenSensor
except ImportError:
    print("[App] 未找到 modules.screen_sensor，屏幕感知功能将不可用")
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

try:
    from modules.live2d import go_idle
except Exception:
    go_idle = None


try:
    from modules.music_sensor import MusicSensor
except ImportError:
    MusicSensor = None


class Live2DApplication:
    """Live2D应用主类"""

    def __init__(self):
        # 核心组件
        self.container = ServiceContainer()
        self.event_bus = EventBus()
        self.state_machine = AgentStateMachine()
        self.logger = None
        self.event_logger = None

        # 业务组件
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

        # [新增] 屏幕感知与语音组件
        self.screen_sensor = None
        self.voice_sensor = None  # 🟢 语音传感器

        # GUI相关
        self.qt_ui = None
        self.loop = None
        self.gui_ws_server = None
        self.gui_http_server = None
        self.display_mqtt_client = None
        self.display_state_config_path = Path("./data/display_state_config.json")
        self.display_mqtt_last_error = "未初始化"

        # 日记状态标记，防止重复记录
        self.last_summary_date = None

        # 配置
        self.tts_enabled = bool(TTS_ENABLED)
        self.runtime_settings_path = Path("./data/runtime_settings.json")
        self.think_motion_enabled = True
        try:
            from config import THINK_MOTION_ENABLED, THINK_MOTION_NAME

            self.think_motion_enabled = bool(THINK_MOTION_ENABLED)
            self.think_motion_name = THINK_MOTION_NAME or "think"
        except Exception:
            self.think_motion_name = "think"

        # 音乐配置
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
                self.logger.warning(f"加载运行时设置失败: {e}")
            return {}

    def _save_runtime_settings(self, settings: Dict[str, Any]):
        path = self.runtime_settings_path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", encoding="utf-8") as f:
                json.dump(settings, f, ensure_ascii=False, indent=2)
        except Exception as e:
            if self.logger:
                self.logger.warning(f"保存运行时设置失败: {e}")

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
                self.logger.warning(f"保存状态屏配置失败: {e}")

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
                    for item in re.split(r"[,，\n\s]+", value)
                    if item.strip()
                ]
            if isinstance(value, list):
                return [str(item).strip() for item in value if str(item).strip()]
            return []

        def _parse_text_list(value):
            if isinstance(value, str):
                return [
                    item.strip()
                    for item in re.split(r"[,，;\n]+", value)
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
            "napcat_access_token": str(
                settings.get("napcat_access_token", NAPCAT_ACCESS_TOKEN) or ""
            ),
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
                settings.get("napcat_owner_label", "主人") or "主人"
            ),
            "napcat_image_vision_enabled": bool(
                settings.get("napcat_image_vision_enabled", True)
            ),
            "napcat_image_prompt": image_prompt
            or "请客观详细描述这张QQ图片的内容，并提取其中可用于回复的关键信息。",
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
            "gui_http_host": str(
                settings.get("gui_http_host", GUI_HTTP_HOST) or GUI_HTTP_HOST
            ),
            "gui_http_port": int(
                settings.get("gui_http_port", GUI_HTTP_PORT) or GUI_HTTP_PORT
            ),
            "gui_http_prefix": str(
                settings.get("gui_http_prefix", GUI_HTTP_PREFIX) or GUI_HTTP_PREFIX
            ),
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

    def apply_external_settings(
        self, settings: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        external_settings = self._normalize_external_runtime_settings(
            settings or self._load_runtime_settings()
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
        }

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
        """初始化应用"""
        # 1. 设置日志
        try:
            from core.console_capture import install_console_capture

            install_console_capture("./logs/console.log")
        except Exception:
            pass
        from core.logger import setup_logging, set_logger

        self.logger = setup_logging(log_dir="./logs", log_name="agent", level="INFO")
        set_logger(self.logger)

        # 启动时读取 TTS 运行时开关（优先于 config.py 默认值）
        self.tts_enabled = self._load_runtime_tts_enabled()
        try:
            import config as runtime_config

            runtime_config.TTS_ENABLED = self.tts_enabled
        except Exception:
            pass
        self.logger.info(f"TTS 启动状态: {'开' if self.tts_enabled else '关'}")

        # 2. 初始化事件日志
        self.event_logger = EventLogger("./data/events.sqlite")
        self.skill_manager = SkillManager(logger=self.logger)

        # 3. 初始化核心组件
        self.brain = AdvancedMemorySystem()
        self.memory_store = get_memory_store()
        self.emotion_controller = EmotionController(mapping=EMO_TO_LIVE2D)
        self.logger.info("EmotionController 已初始化")

        # 4. 初始化插件系统
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
                    f"口型同步已启用 (Rhubarb: {rhubarb_abs}, 平滑窗口: {LIP_SYNC_SMOOTH_WINDOW})"
                )
            else:
                self.logger.warning(f"口型同步已开启但 Rhubarb 不存在: {rhubarb_abs}")
        else:
            self.logger.info("口型同步未启用 (LIP_SYNC_ENABLED=0)")

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

        # 7. 初始化聊天服务
        self.mcp_bridge = MCPToolBridge()
        self.chat_gateway = ChatGateway()
        self.chat_gateway.on_message(self._handle_external_chat_message)
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

        self.gui_ws_server = GuiWebSocketServer(
            host=initial_external_settings.get("gui_ws_host", GUI_WS_HOST),
            port=initial_external_settings.get("gui_ws_port", GUI_WS_PORT),
            path=initial_external_settings.get("gui_ws_path", GUI_WS_PATH),
            logger=self.logger,
        )
        self.gui_ws_server.set_message_handler(self._on_gui_ws_message)
        self.gui_http_server = GuiHttpServer(
            host=initial_external_settings.get("gui_http_host", GUI_HTTP_HOST),
            port=initial_external_settings.get("gui_http_port", GUI_HTTP_PORT),
            path_prefix=initial_external_settings.get("gui_http_prefix", GUI_HTTP_PREFIX),
            logger=self.logger,
            app_ref=self,
        )
        self.activity_sidecar = ActivitySidecarManager(logger=self.logger)

        if MusicSensor:
            self.music_sensor = MusicSensor(self.chat_service)

        # 8. 初始化屏幕感知 (必须在 chat_service 之后)
        if ScreenSensor:
            try:
                self.screen_sensor = ScreenSensor(self.chat_service)
                self.chat_service.screen_sensor_ref = self.screen_sensor
                self.logger.info("👀 ScreenSensor 屏幕感知模块已就绪")
                if getattr(self, "activity_sidecar", None):
                    self.logger.info(
                        "🦀 ScreenSensor 当前优先使用 Rust activity events"
                    )
            except Exception as e:
                self.logger.error(f"❌ ScreenSensor 初始化失败: {e}")
        else:
            self.logger.warning("⚠️ ScreenSensor 模块未加载")

        # 🟢 8.5 初始化语音感知
        try:
            from modules.voice_sensor import VoiceSensor
            from config import VOICE_SENSOR_ENABLED, SHERPA_MODEL_CONFIG

            # 无论默认是否开启，先实例化备用，由 start_async_loop 决定是否启动
            self.voice_sensor = VoiceSensor(
                chat_service=self.chat_service,
                event_bus=self.event_bus,
                config_path=SHERPA_MODEL_CONFIG,
            )
            self.logger.info("🎤 VoiceSensor 语音模块已加载 (就绪待命)")
        except ImportError:
            self.logger.warning("⚠️ 未找到 modules.voice_sensor，语音功能将不可用")
            self.voice_sensor = None
        except Exception as e:
            self.logger.error(f"❌ VoiceSensor 初始化失败: {e}")
            self.voice_sensor = None

        # 9. 注册事件处理器
        self._wire_events()

        # 10. 注册到容器
        self._register_services()

        self.logger.info("应用初始化完成")

    def _wire_events(self):
        """连接事件"""
        # UI事件
        self.event_bus.on(Events.UI_BUBBLE, self._on_ui_bubble)
        self.event_bus.on(Events.UI_STATUS, self._on_ui_status)
        self.event_bus.on(Events.UI_APPEND, self._on_ui_append)

        # 状态事件（将事件总线的状态变化转换为状态机状态）
        self.event_bus.on("state.changed", self._on_state_changed_event)

        # Live2D事件
        self.event_bus.on(Events.LIVE2D_EMOTION, self._on_live2d_emotion)
        self.event_bus.on(Events.LIVE2D_MOTION, self._on_live2d_motion)
        self.event_bus.on(Events.LIVE2D_GO_IDLE, self._on_live2d_go_idle)

        # 状态机监听器（直接处理状态，不通过事件总线）
        self.state_machine.add_listener(self._on_state_machine_change)

        # TTS事件
        self.event_bus.on(Events.ASSISTANT_UTTER, self._on_assistant_utter)
        self.event_bus.on(Events.ASSISTANT_STREAM_START, self._on_stream_start)
        self.event_bus.on(Events.ASSISTANT_STREAM_FEED, self._on_stream_feed)
        self.event_bus.on(Events.ASSISTANT_STREAM_END, self._on_stream_end)

        # 日志事件
        self.event_bus.on(Events.CHAT_LOG, self._on_chat_log)
        self.event_bus.on(
            Events.MEMORY_ADD_OK, lambda p: print(f"✅ [Memory] 记忆已添加")
        )
        self.event_bus.on(
            Events.MEMORY_ADD_FAIL, lambda p: print(f"⚠️ [Memory] 记忆添加失败")
        )

        # [可选] 监听被忽略的消息
        self.event_bus.on(
            "chat.ignored",
            lambda p: print(f"🚫 [Gatekeeper] 已忽略: {p.get('content', '')[:20]}..."),
        )

    async def _on_ui_bubble(self, payload: Dict[str, Any]):
        """处理UI气泡事件"""
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
        """处理UI状态事件"""
        if self.qt_ui:
            try:
                self.qt_ui.set_status(payload.get("text", ""))
            except Exception as e:
                self.logger.warning(f"设置UI状态失败: {e}")

        self._emit_gui_ws(
            {
                "type": "status",
                "text": payload.get("text", ""),
                "level": "info",
            }
        )

    def _on_ui_append(self, payload: Dict[str, Any]):
        """处理UI追加事件"""
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
        """处理Live2D情绪事件 - 使用 EmotionController 管理"""
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
                f"🎭 [Emotion] 收到情绪请求: {emo} (prefer_motion={prefer_motion})"
            )

            # 标记活动（退出空闲模式）
            self.emotion_controller.mark_activity(reason or "emotion_request")

            # 通过 EmotionController 处理情绪请求
            await self.emotion_controller.request_emotion(
                label=emo,
                intensity=intensity,
                prefer_motion=prefer_motion,
                reason=reason,
            )

            self.logger.debug(f"✅ [Emotion] 情绪处理完成: {emo}")

        except Exception as e:
            self.logger.error(f"❌ [Emotion] 处理失败: {e}", exc_info=True)

    async def _on_live2d_motion(self, payload: Dict[str, Any]):
        """处理Live2D动作事件"""
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
        """处理Live2D进入空闲事件"""
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
        """处理状态变化事件（从事件总线到状态机）"""
        state_name = payload.get("state")
        reason = payload.get("reason", "unknown")

        self.logger.debug(f"收到状态变化事件: {state_name} (原因: {reason})")

        # 将字符串状态转换为 AgentState 枚举
        state_map = {
            "idle": AgentState.IDLE,
            "thinking": AgentState.THINKING,
            "speaking": AgentState.SPEAKING,
        }

        target_state = state_map.get(state_name.lower() if state_name else "")
        if target_state:
            await self.state_machine.set_state(target_state, reason=reason)
        else:
            self.logger.warning(f"未知状态: {state_name}")

    async def _on_state_machine_change(
        self, new_state: AgentState, prev_state: AgentState, meta: dict
    ):
        """状态机监听器（直接处理状态变化）"""
        reason = meta.get("reason", "unknown")
        self.logger.debug(
            f"🔄 [State] {prev_state.value} -> {new_state.value} (原因: {reason})"
        )

        try:
            # 更新 EmotionController 的状态
            self.emotion_controller.set_agent_state(new_state)

            # 根据状态触发对应的动作
            if new_state == AgentState.THINKING:
                self.logger.debug(f"💭 [State] 进入思考状态")

                # 异步触发 UI 状态更新
                asyncio.create_task(
                    self.event_bus.emit(Events.UI_STATUS, text="Thinking.")
                )

                if self.think_motion_enabled:
                    self.logger.debug(
                        f"🎬 [State] 触发思考动作: {self.think_motion_name}"
                    )

                    # 标记活动（退出空闲）
                    self.emotion_controller.mark_activity("thinking")

                    # 直接通过 EmotionController 触发动作
                    asyncio.create_task(
                        self.emotion_controller.request_emotion(
                            label=self.think_motion_name,
                            prefer_motion=True,
                            reason="thinking_state",
                        )
                    )

            elif new_state == AgentState.SPEAKING:
                self.logger.debug(f"🗣️ [State] 进入说话状态")
                asyncio.create_task(
                    self.event_bus.emit(Events.UI_STATUS, text="Speaking.")
                )

                # 说话时标记活动
                self.emotion_controller.mark_activity("speaking")

            elif new_state == AgentState.IDLE:
                self.logger.debug(f"😴 [State] 进入空闲状态")
                asyncio.create_task(self.event_bus.emit(Events.UI_STATUS, text="Idle"))
                if reason not in {
                    "tts_disabled",
                    "tts_stream_disabled",
                    "all_done",
                }:
                    asyncio.create_task(self.event_bus.emit(Events.LIVE2D_GO_IDLE))

        except Exception as e:
            self.logger.error(f"❌ [State] 状态事件错误: {e}", exc_info=True)

    async def _on_assistant_utter(self, payload: Dict[str, Any]):
        """处理AI说话事件"""
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
                await self.event_bus.emit(
                    Events.UI_BUBBLE,
                    text=text,
                    emotion=emotion,
                    duration_ms=duration_ms,
                )
                if duration_ms and duration_ms > 0:
                    async def _idle_after_silent_bubble(delay_ms: int, seq: int):
                        await asyncio.sleep(max(0.35, delay_ms / 1000.0 + 0.18))
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
        """处理流开始事件"""
        self._silent_bubble_seq += 1
        if not self.tts_enabled or not bool(payload.get("speak", True)):
            return
        self.tts.start_stream()

    async def _on_stream_feed(self, payload: Dict[str, Any]):
        """处理流数据事件"""
        if not self.tts_enabled or not bool(payload.get("speak", True)):
            return
        chunk = payload.get("chunk") or ""
        if chunk:
            await self.tts.feed_stream(chunk, emotion=payload.get("emotion"))

    async def _on_stream_end(self, payload: Dict[str, Any]):
        """处理流结束事件"""
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

        # 确保状态切换到 IDLE（如果 TTS 没有正常触发）
        # async def ensure_idle():
        #     await asyncio.sleep(2)  # 等待 TTS 播放完成
        #     if self.state_machine.state == AgentState.SPEAKING:
        #         self.logger.warning(f"TTS 未触发空闲，强制设置 IDLE")
        #         await self.state_machine.set_state(AgentState.IDLE, reason="force_idle")
        #
        # asyncio.create_task(ensure_idle())

    def restart_app(self):
        """触发重启逻辑 (退出码 100)"""
        print("♻️ [App] 接收到重启请求...")

        # 1. 先做清理 (保存日记、关闭数据库连接等)
        self.cleanup()

        # 2. 退出进程，返回 100 给守护进程
        import sys

        sys.exit(100)

    def _on_chat_log(self, payload: Dict[str, Any]):
        """将聊天日志打印到控制台，并写入事件日志/转录。"""
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
        """注册服务到容器"""
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

    #  定时任务调度器 (处理自动日记 + 补录)
    async def _scheduler_loop(self):
        """后台定时任务调度器 (无时间限制补录版)"""
        try:
            from config import AUTO_DIARY_ENABLED, AUTO_DIARY_TIME
        except ImportError:
            AUTO_DIARY_ENABLED = False
            AUTO_DIARY_TIME = "23:30"

        self.logger.info("⏰ 定时任务调度器已启动")

        # 内存标记（防止单次运行中每30秒查一次数据库，浪费性能）
        _last_makeup_check_date = None

        while True:
            try:
                now = datetime.now()
                current_time_str = now.strftime("%H:%M")
                current_date_str = now.strftime("%Y-%m-%d")

                # ================= 场景 A: 准点触发 (23:30) =================
                if AUTO_DIARY_ENABLED and current_time_str == AUTO_DIARY_TIME:
                    if self.memory_store:
                        today_stats = (
                            self.memory_store.get_daily_screen_stats(current_date_str)
                            or {}
                        )
                        if today_stats.get("diary_done") is True:
                            self.last_summary_date = current_date_str
                    if self.last_summary_date != current_date_str:
                        self.logger.info(f"✨ 触发每日总结 (准点)...")
                        if self.screen_sensor and self.chat_service:
                            report = self.screen_sensor.get_formatted_report()
                            if len(report) > 10:
                                diary_text = await self.chat_service.summarize_day(
                                    report, auto=True
                                )
                                if diary_text:
                                    self.last_summary_date = current_date_str
                                    self.logger.info("✅ 今日日记已归档")
                                else:
                                    self.logger.warning(
                                        f"⚠️ 今日日记生成失败，未写入归档标记: {current_date_str}"
                                    )

                # ================= 场景 B: 补录昨天 (全天候检查) =================
                # 🟢 [修改] 去掉了 and now.hour < 12
                # 只要今天还没检查过补录(内存标记)，就去检查一次
                if AUTO_DIARY_ENABLED and _last_makeup_check_date != current_date_str:
                    _last_makeup_check_date = (
                        current_date_str  # 标记：今天已经检查过了，别再查了
                    )
                    await self._run_diary_backfill(now.date())

                await asyncio.sleep(30)
            except Exception as e:
                self.logger.error(f"调度器出错: {e}")
                import traceback

                traceback.print_exc()
                await asyncio.sleep(60)

    # 退出清理 (处理关机保存)
    def cleanup(self):
        """程序退出前的清理工作"""
        print("🛑 正在关闭应用...")

        # 🟢 停止语音监听，释放麦克风
        if self.voice_sensor:
            try:
                self.voice_sensor.stop()
            except Exception as e:
                print(f"关闭语音传感器出错: {e}")

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
        if getattr(self, "activity_sidecar", None):
            try:
                self.activity_sidecar.stop()
            except Exception as e:
                print(f"Rust activity agent shutdown error: {e}")

        # 尝试最后一次保存（如果今天还没写日记）
        try:
            if self.chat_gateway_server:
                try:
                    self.chat_gateway_server.stop()
                except Exception as e:
                    print(f"关闭 NapCat webhook 出错: {e}")
            current_date = datetime.now().strftime("%Y-%m-%d")
            # 如果配置开启，且今天还没记录，且传感器有数据
            if self.last_summary_date != current_date and self.screen_sensor:
                print("📝 检测到退出时今日尚未写日记，正在尝试保存数据...")
                report = self.screen_sensor.get_formatted_report()
                if len(report) > 20:
                    self.logger.info(f"【退出存档】今日未归档数据:\n{report}")
        except Exception as e:
            print(f"退出清理出错: {e}")

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
                self.logger.info(f"👀 {target_str} 日记已存在，跳过补录。")
                continue

            stats = self.memory_store.get_daily_screen_stats(target_str) or {}
            if stats.get("diary_done") is True:
                self.logger.info(f"👀 {target_str} 已标记 diary_done，跳过补录。")
                continue

            summary_text = str(stats.get("summary_text") or "").strip()
            if len(summary_text) <= 10:
                self.logger.debug(f"👀 {target_str} 无有效活动数据，无需补录。")
                continue

            if delta_days == 1:
                self.logger.info(f"✨ 发现昨日数据未归档，正在补录日记...")
            else:
                self.logger.info(f"✨ 发现 {target_str} 数据未归档，正在补录日记...")

            diary_text = await self.chat_service.summarize_day(
                summary_text,
                raw_stats=stats,
                auto=True,
                target_date=target_date,
            )
            if diary_text:
                if delta_days == 1:
                    self.logger.info(f"✅ 昨日({target_str})日记补录完成。")
                else:
                    self.logger.info(f"✅ {target_str} 日记补录完成。")
            else:
                if delta_days == 1:
                    self.logger.warning(f"⚠️ 昨日({target_str})日记补录失败，保留未归档状态。")
                else:
                    self.logger.warning(f"⚠️ {target_str} 日记补录失败，保留未归档状态。")

    # 动态切换语音监听的方法
    def set_voice_sensor_enabled(self, enabled: bool):
        """动态开启或关闭语音监听"""
        if not self.voice_sensor:
            self.logger.error("⚠️ VoiceSensor 未就绪，无法切换")
            return

        if enabled:
            if not self.voice_sensor.running and self.loop:
                self.logger.info("🎤 手动开启语音监听...")
                self.voice_sensor.start(self.loop)
        else:
            if self.voice_sensor.running:
                self.logger.info("🔇 手动关闭语音监听...")
                self.voice_sensor.stop()

    def start_async_loop(self):
        """启动异步事件循环"""
        self.logger.info("启动异步事件循环...")

        def async_worker():
            # 1. 创建事件循环
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)

            # 2. 启动情绪控制器
            if self.emotion_controller:
                try:
                    self.emotion_controller.start(self.loop)
                    self.logger.info("📉 情绪衰减循环已启动")
                except Exception as e:
                    self.logger.error(f"❌ 情绪控制器启动失败: {e}")

            # 3. 启动所有插件
            self.loop.create_task(
                self.plugin_manager.start_all_plugins(
                    {"chat_service": self.chat_service}
                )
            )

            # 4. 启动定时调度器
            self.loop.create_task(self._scheduler_loop())

            # 6. 启动音乐感知
            if self.music_sensor:
                try:
                    self.logger.info("🎵 启动 MusicSensor 监控线程...")
                    self.music_sensor.start(self.loop)
                except Exception as e:
                    self.logger.error(f"❌ MusicSensor 启动失败: {e}")

            # 🟢 7. 启动语音监听 (如果配置为开启)
            import config

            if getattr(config, "VOICE_SENSOR_ENABLED", False) and self.voice_sensor:
                try:
                    self.logger.info("🎤 启动 VoiceSensor 监听线程...")
                    self.voice_sensor.start(self.loop)
                except Exception as e:
                    self.logger.error(f"❌ VoiceSensor 启动失败: {e}")

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
                except Exception as exc:
                    if self.logger:
                        self.logger.error(f"GUI HTTP start failed: {exc}")
            try:
                self.activity_sidecar.start()
            except Exception as exc:
                if self.logger:
                    self.logger.warning(f"Rust activity agent start failed: {exc}")
            if self.activity_sidecar and self.activity_sidecar.is_running():
                self.logger.info("🦀 当前采集模式: Rust 优先")
            else:
                self.logger.info("🐍 当前采集模式: Python fallback")

            # 5. 启动屏幕感知（Rust sidecar 运行时，关闭 Python 本地采集线程）
            if self.screen_sensor:
                try:
                    if self.activity_sidecar and self.activity_sidecar.is_running():
                        self.screen_sensor.use_rust_events_only = True
                        self.logger.info(
                            "🦀 Rust 优先采集已启用，关闭 Python 本地窗口轮询"
                        )
                        self.logger.info(
                            "🦀 启动 ScreenSensor 监控线程（Rust 事件消费模式）"
                        )
                        self.screen_sensor.start(self.loop)
                    else:
                        self.screen_sensor.use_rust_events_only = False
                        self.logger.info(
                            "🚀 启动 ScreenSensor 监控线程（Python 本地采集模式）"
                        )
                        self.screen_sensor.start(self.loop)
                except Exception as e:
                    self.logger.error(f"❌ ScreenSensor 启动失败: {e}")

            self.loop.run_forever()

        t = threading.Thread(target=async_worker, daemon=True)
        t.start()
        self.logger.info("异步事件循环已启动")

    def on_gui_send(self, text: str, ctx: Optional[Dict[str, Any]] = None):
        """GUI发送消息回调"""
        if self.loop is None:
            print("⚠️ [Application] loop未初始化，忽略输入")
            return

        async def _process():
            # 默认来自文本输入，可被外部窗口覆盖为 codex_input 等来源
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
                self.logger.error(f"协程异常: {repr(e)}")

        fut.add_done_callback(_done)

    def on_external_message(
        self,
        text: str,
        *,
        source: str = "qq_gateway",
        channel: str = "qq",
        metadata: Optional[Dict[str, Any]] = None,
    ):
        """处理外部渠道消息。

        外部消息直接进入 chat_service，对话链路保持完整，但默认不驱动 Live2D 与桌面语音。
        """
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

    def on_gui_change_costume(self, path: str, config: dict):
        """GUI换装回调"""
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
                    f"[RoleSync] 切换角色: {char_id} -> {char.get('name', char_id)} | costume={current_costume}"
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
                self.logger.warning(f"切换角色运行时失败: {e}")
            return False

    def on_gui_preview_motion(self, motion_name: str, motion_type: int = 0):
        """GUI预览动作回调"""
        if self.loop is None or not motion_name:
            return

        async def _do():
            try:
                await play_motion(str(motion_name), motion_type=int(motion_type))
            except Exception as e:
                self.logger.error(f"预览动作失败: {e}")

        asyncio.run_coroutine_threadsafe(_do(), self.loop)

    def on_gui_preview_expression(self, exp_value):
        """GUI预览表情回调"""
        if self.loop is None:
            return

        async def _do():
            try:
                await set_expression(int(exp_value))
            except Exception as e:
                self.logger.error(f"预览表情失败: {e}")

        asyncio.run_coroutine_threadsafe(_do(), self.loop)

    def set_tts_enabled(self, enabled: bool):
        """设置TTS开关"""
        enabled = bool(enabled)
        self.tts_enabled = enabled
        if getattr(self, "tts", None):
            self.tts.enabled = enabled

        # 保持 config 运行时状态与 UI 显示一致
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
        """设置思考动作开关"""
        self.think_motion_enabled = bool(enabled)
        self.logger.info(f"THINK_MOTION_ENABLED = {self.think_motion_enabled}")

    def run(self):
        """运行应用"""
        try:
            # 初始化
            self.initialize()

            self.logger.info("=== 五十铃怜 Live2D Agent 启动 ===")

            # 启动异步循环
            self.start_async_loop()

            # 根据配置选择GUI
            backend = (GUI_BACKEND or "auto").strip().lower()

            if backend == "tk":
                self._run_tk_gui()
            elif backend == "qt":
                try:
                    self._run_qt_gui()
                except Exception as e:
                    self.logger.warning(f"Qt启动失败，回退到Tk: {e}")
                    self._run_tk_gui()
            else:  # auto
                try:
                    self._run_qt_gui()
                except Exception as e:
                    self.logger.warning(f"Qt不可用，使用Tk: {e}")
                    self._run_tk_gui()

        except KeyboardInterrupt:
            pass
        finally:
            # 🟢 退出时触发清理
            self.cleanup()

    def _run_tk_gui(self):
        """运行Tk GUI"""
        from modules.gui.gui import ChatWindow

        window = ChatWindow(
            on_send_callback=self.on_gui_send,
            on_tts_toggle_callback=self.set_tts_enabled,
            on_think_toggle_callback=self.set_think_motion_enabled,
        )
        window.run()

    def _run_qt_gui(self):
        """运行Qt GUI"""
        global qt_ui
        import config
        from modules.qt_gui import QtChatTrayApp, QtGuiConfig

        self.qt_ui = QtChatTrayApp(
            on_send_callback=self.on_gui_send,
            on_tts_toggle_callback=self.set_tts_enabled,
            on_voice_toggle_callback=self.set_voice_sensor_enabled,  # 🟢 传递语音控制回调
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
        self.qt_ui.set_status("Idle")
        try:
            self.logger.info("[RoleSync] 启动时同步当前角色形象")
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
                "未启用 MQTT_DISPLAY"
                if not MQTT_DISPLAY_ENABLED
                else "缺少 paho-mqtt 依赖"
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
    """事件展示器"""

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
        # 容错清理：防止模型输出非标准情绪标签被 TTS 读出来
        self._emo_tag_any_re = re.compile(
            r"<\s*/?\s*(?:emo(?:tion)?|happy|sad|angry|flustered|confused|neutral|think|idle)\b[^>]*>",
            flags=re.IGNORECASE,
        )

    def set_tts_enabled(self, enabled: bool):
        """设置TTS开关"""
        self.tts_enabled = bool(enabled)
        if self.verbose:
            # 这里没有 logger，使用 event_bus 发送日志
            print(f"🎚️ [Presenter] TTS: {'开' if self.tts_enabled else '关'}")

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
        """展示文本"""
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
            interrupt=interrupt,  # <--- 使用传入的参数，而不是写死 True
            speak=want_speak,
            show_bubble=bool(show_bubble),
        )

    async def present_direct(self, text: str, emotion: Optional[str] = None):
        """直接展示（工具结果）"""
        await self.present(text, emotion=emotion, speak=self.speak_direct_result)
