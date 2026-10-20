"""User-facing information architecture.

Settings groups, capability buckets, and first-run readiness live here so
the shell UI does not invent a second vocabulary.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Iterable, List, Optional

try:
    from modules.runtime_settings import load_runtime_settings, update_runtime_settings
except ImportError:
    # External-brain packaging: runtime_settings lives in the brain, which may
    # not be on sys.path at first boot. Provide passthrough stubs so this
    # module still imports; the real functions are used once the user picks a
    # brain directory and restarts.
    def load_runtime_settings(*_args, **_kwargs):
        return {}

    def update_runtime_settings(*_args, **_kwargs):
        return {}

DEVELOPER_MODE_KEY = "developer_mode"
TRAY_HIDE_HINT_KEY = "tray_hide_hint_shown"

SETTINGS_GROUPS = [
    ("daily", ""),
    ("character", "角色"),
    ("chat", "对话"),
    ("memory", "记忆与资料"),
    ("companion", "桌面陪伴"),
    ("connect", ""),
    ("developer", "开发者"),
]

SETTINGS_GROUP_LABELS = {key: label for key, label in SETTINGS_GROUPS}

# nav = sidebar label; title/desc = content header.
SETTINGS_PAGES: List[Dict[str, Any]] = [
    {
        "id": "start",
        "group": "daily",
        "nav": "开始使用",
        "title": "开始使用",
        "desc": "先确认角色和聊天模型能用。QQ、能力、开发者选项都放在后面，用到再打开。",
    },
    {
        "id": "character",
        "group": "character",
        "nav": "形象与人设",
        "title": "形象与人设",
        "desc": "她是谁、长什么样、穿哪套衣服、说话用什么声音。",
    },
    {
        "id": "color",
        "group": "character",
        "nav": "颜色主题",
        "title": "颜色主题",
        "desc": "设置页的配色，改完立刻生效。",
    },
    {
        "id": "llm",
        "group": "chat",
        "nav": "聊天模型",
        "title": "聊天模型",
        "desc": "选一个用来聊天的模型。提供商和 Key 可以在添加模型时一起填。",
        "init_label": "模型与路由",
    },
    {
        "id": "asr",
        "group": "chat",
        "nav": "语音",
        "title": "语音",
        "desc": "麦克风怎么听：唤醒词、连续对话、按住说话。",
        "init_label": "语音输入",
    },
    {
        "id": "memory",
        "group": "memory",
        "nav": "记忆",
        "title": "记忆",
        "desc": "她记得的事和对话。这和知识库不是一回事。",
        "init_label": "记忆数据",
    },
    {
        "id": "diary",
        "group": "memory",
        "nav": "日记",
        "title": "日记",
        "desc": "查看、编辑或导出她每天写下的日记。",
    },
    {
        "id": "knowledge",
        "group": "memory",
        "nav": "知识库（资料）",
        "title": "知识库（资料）",
        "desc": "这是参考资料，不是记忆。闲聊默认不查；你明确问设定或资料时才会用。",
        "init_label": "知识库",
    },
    {
        "id": "expression",
        "group": "memory",
        "nav": "表达习惯",
        "title": "表达习惯",
        "desc": "她常用的说法、反应和动作映射。",
        "init_label": "表达学习库",
    },
    {
        "id": "meme",
        "group": "memory",
        "nav": "表情包",
        "title": "表情包",
        "desc": "久坐提醒和聊天里可能用到的图片。",
        "init_label": "表情包库",
    },
    {
        "id": "sedentary",
        "group": "companion",
        "nav": "久坐提醒",
        "title": "久坐提醒",
        "desc": "连续工作多久提醒一次、稍后多久、弹窗怎么写。",
    },
    {
        "id": "sensor_prompts",
        "group": "companion",
        "nav": "屏幕插话",
        "title": "屏幕插话",
        "desc": "她看见窗口标题时会不会说话、用什么口气。需要先连上桌面形象。",
        "init_label": "屏幕吐槽提示词",
    },
    {
        "id": "screen_apps",
        "group": "companion",
        "nav": "应用识别",
        "title": "应用识别",
        "desc": "把窗口归到工作 / 娱乐等类别，优先于自动判断。",
    },
    {
        "id": "external_services",
        "group": "companion",
        "nav": "外部服务",
        "title": "外部服务",
        "desc": "大脑依赖的本机服务（Ollama / GPT-SoVITS / NapCat）的状态查看与启动停止。操作通过大脑完成。",
        "init_label": "外部服务",
    },
    {
        "id": "qq",
        "group": "connect",
        "nav": "QQ",
        "title": "QQ",
        "desc": "让同一角色出现在 QQ 里。桌面上的她和 QQ 里的她是一个人。",
        "init_label": "QQ 接入",
    },
    {
        "id": "plugins",
        "group": "connect",
        "nav": "能力",
        "title": "能力",
        "desc": "她被允许做什么。按陪伴 / 桌面 / QQ 分组；开发类能力要打开开发者选项才显示。",
        "init_label": "插件工具",
    },
    {
        "id": "mcp",
        "group": "connect",
        "nav": "MCP",
        "title": "MCP",
        "desc": "外接 MCP 服务器。大多数人用不到，需要接外部工具时再填。",
    },
    {
        "id": "providers",
        "group": "developer",
        "nav": "提供商",
        "title": "提供商",
        "desc": "集中管理 Base URL 和 API Key。添加模型时也可以当场新建。",
        "developer": True,
    },
    {
        "id": "info_sources",
        "group": "developer",
        "nav": "信息源 API",
        "title": "信息源 API",
        "desc": "第三方信息接口草稿和测试。",
        "developer": True,
    },
    {
        "id": "status_screen",
        "group": "developer",
        "nav": "状态屏",
        "title": "状态屏",
        "desc": "外置状态屏或墨水屏。",
        "developer": True,
    },
    {
        "id": "runtime_health",
        "group": "developer",
        "nav": "运行健康",
        "title": "运行健康中心",
        "desc": "查看大脑、桌面形象、语音、活动感知等各组件最近上报的运行状态。",
        "developer": True,
        "init_label": "运行健康",
    },
    {
        "id": "dependency",
        "group": "developer",
        "nav": "依赖体检",
        "title": "依赖体检",
        "desc": "扫描能力缺失的 Python 包。",
        "developer": True,
        "init_label": "依赖体检",
    },
]

SETTINGS_PAGE_BY_ID = {page["id"]: page for page in SETTINGS_PAGES}

MODE_PRESETS = [
    ("companion", "陪伴", "会说话、必要时会主动提醒"),
    ("quiet", "安静", "你问她才回，不主动插话"),
]

MODE_PRESET_LABELS = {key: label for key, label, _tip in MODE_PRESETS}

CAPABILITY_GROUP_LABELS = {
    "companion": "陪伴",
    "desktop": "桌面",
    "qq": "QQ",
    "developer": "开发",
}

# Primary group per plugin trigger. Unknown triggers fall back to companion.
CAPABILITY_GROUPS: Dict[str, str] = {
    "timer": "companion",
    "life_hub": "companion",
    "diary_manager": "companion",
    "memory_tools": "companion",
    "database": "companion",
    "meme_pack": "companion",
    "dice_roll": "companion",
    "egg_search": "companion",
    "xinsanguo_voice": "companion",
    "播放音乐": "companion",
    "magic_daily": "companion",
    "moegirl_wiki": "companion",
    "knowledge_base": "companion",
    "search_web": "companion",
    "web_reader": "companion",
    "info_gateway": "companion",
    "vision": "companion",
    "打开": "desktop",
    "desktop_hub": "desktop",
    "owner_console": "desktop",
    "app_control": "desktop",
    "user_files": "desktop",
    "qq截图": "desktop",
    "qq文件": "desktop",
    "monitor": "desktop",
    "backup_manager": "desktop",
    "task": "desktop",
    "agently_mail": "desktop",
    "qq帮助": "qq",
    "qq生图": "qq",
    "qq_music": "qq",
    "qq提醒": "qq",
    "qq角色": "qq",
    "code_agent": "developer",
    "workspace_ops": "developer",
    "mcp_tools": "developer",
    "agent_runtime": "developer",
    "llm_monitor": "developer",
    "delegate_status": "developer",
    "owner_channel_bridge": "developer",
}

_PLACEHOLDER_KEYS = {
    "",
    "sk-xxxxxx",
    "sk-xxx",
    "changeme",
    "your-api-key",
    "your_api_key",
    "none",
    "null",
}


def is_developer_mode(settings: Optional[Dict[str, Any]] = None) -> bool:
    data = settings if isinstance(settings, dict) else load_runtime_settings()
    return bool(data.get(DEVELOPER_MODE_KEY))


def set_developer_mode(enabled: bool) -> Dict[str, Any]:
    return update_runtime_settings({DEVELOPER_MODE_KEY: bool(enabled)})


def visible_settings_pages(developer: Optional[bool] = None) -> List[Dict[str, Any]]:
    show_dev = is_developer_mode() if developer is None else bool(developer)
    pages = []
    for spec in SETTINGS_PAGES:
        if spec.get("developer") and not show_dev:
            continue
        pages.append(spec)
    return pages


def capability_group(trigger: str) -> str:
    key = str(trigger or "").strip()
    return CAPABILITY_GROUPS.get(key, "companion")


def capability_group_label(group: str) -> str:
    return CAPABILITY_GROUP_LABELS.get(str(group or ""), CAPABILITY_GROUP_LABELS["companion"])


def capability_visible(trigger: str, *, developer: Optional[bool] = None) -> bool:
    group = capability_group(trigger)
    if group != "developer":
        return True
    show_dev = is_developer_mode() if developer is None else bool(developer)
    return show_dev


def model_has_credentials(
    cfg: Optional[Dict[str, Any]],
    providers: Optional[Dict[str, Any]] = None,
) -> bool:
    if not isinstance(cfg, dict):
        return False
    key = str(cfg.get("api_key") or "").strip()
    if key.lower() in _PLACEHOLDER_KEYS:
        key = ""
    if key:
        return True
    env_names = str(cfg.get("api_key_env") or "").strip()
    if env_names:
        for part in env_names.replace("，", ",").split(","):
            name = part.strip()
            if name and str(os.getenv(name) or "").strip():
                return True
    provider_name = str(cfg.get("provider") or "").strip()
    pool = providers if isinstance(providers, dict) else {}
    provider_cfg = pool.get(provider_name) if provider_name else None
    if isinstance(provider_cfg, dict):
        provider_key = str(provider_cfg.get("api_key") or "").strip()
        if provider_key and provider_key.lower() not in _PLACEHOLDER_KEYS:
            return True
    url = str(cfg.get("base_url") or "").strip().lower()
    if "127.0.0.1" in url or "localhost" in url:
        return True
    return False


def model_looks_local(cfg: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(cfg, dict):
        return False
    url = str(cfg.get("base_url") or "").strip().lower()
    key = str(cfg.get("api_key") or "").strip().lower()
    if "127.0.0.1" in url or "localhost" in url:
        return True
    if key == "sk-no-key-needed":
        return True
    return False


def _iter_chat_model_ids(
    models: Dict[str, Any],
    router: Dict[str, Any],
) -> Iterable[str]:
    seen = set()
    chain = router.get("default") if isinstance(router, dict) else None
    if isinstance(chain, str):
        chain = [chain]
    for item in chain or []:
        key = str(item or "").strip()
        if key and key not in seen:
            seen.add(key)
            yield key
    for key in models.keys():
        text = str(key or "").strip()
        if text and text not in seen:
            seen.add(text)
            yield text


def chat_setup_status(*, runtime_health: Any = None) -> Dict[str, Any]:
    character_ok = False
    character_name = ""
    try:
        from modules.character_manager import character_manager

        active = character_manager.get_active_character() or {}
        character_name = str(active.get("name") or "").strip()
        character_ok = bool(character_name or active.get("prompt") or active.get("costumes"))
    except Exception:
        character_ok = False

    model_ok = False
    model_label = "还没有可用的聊天模型"
    model_local_only = False
    try:
        from config import LLM_ROUTER, MODELS, PROVIDERS
    except Exception:
        LLM_ROUTER, MODELS, PROVIDERS = {}, {}, {}

    models = MODELS if isinstance(MODELS, dict) else {}
    router = LLM_ROUTER if isinstance(LLM_ROUTER, dict) else {}
    providers = PROVIDERS if isinstance(PROVIDERS, dict) else {}
    usable = []
    local_ids = []
    for model_id in _iter_chat_model_ids(models, router):
        cfg = models.get(model_id)
        if not model_has_credentials(cfg, providers):
            continue
        if model_looks_local(cfg):
            local_ids.append(model_id)
        else:
            usable.append(model_id)
    if usable:
        model_ok = True
        model_label = str(usable[0])
        model_local_only = False
    elif local_ids:
        model_ok = True
        model_label = str(local_ids[0])
        model_local_only = True

    live2d_state = "unknown"
    cached = getattr(runtime_health, "last_snapshot", None) if runtime_health is not None else None
    if cached is None and runtime_health is not None and hasattr(runtime_health, "snapshot"):
        try:
            cached = runtime_health.snapshot()
        except Exception:
            cached = None
    if isinstance(cached, dict):
        try:
            from modules.gui.runtime_health_view import component_rows

            for item in component_rows(cached):
                if item.get("component") != "live2d_ws":
                    continue
                item_state = str(item.get("state") or "").strip().lower()
                if item_state == "healthy":
                    live2d_state = "connected"
                elif item_state in {"offline", "degraded", "reconnecting"}:
                    live2d_state = "offline"
                break
        except Exception:
            live2d_state = "unknown"

    ready = bool(character_ok and model_ok)
    return {
        "character_ok": character_ok,
        "character_name": character_name,
        "model_ok": model_ok,
        "model_label": model_label,
        "model_local_only": model_local_only,
        "live2d_state": live2d_state,
        "ready": ready,
    }
