from typing import Any, Dict

from modules.runtime_settings import load_runtime_settings, save_runtime_settings


PRESETS = {
    "companion": {
        "label": "陪伴模式",
        "tts_enabled": True,
        "dnd_mode": False,
        "codex_mode_enabled": False,
        "codex_allow_read": False,
        "codex_allow_write": False,
        "codex_allow_exec": False,
    },
    "codex": {
        "label": "代码助手模式",
        "tts_enabled": False,
        "dnd_mode": True,
        "codex_mode_enabled": True,
        "codex_allow_read": True,
        "codex_allow_write": True,
        "codex_allow_exec": False,
    },
    "quiet": {
        "label": "低打扰模式",
        "tts_enabled": False,
        "dnd_mode": True,
        "codex_mode_enabled": False,
        "codex_allow_read": False,
        "codex_allow_write": False,
        "codex_allow_exec": False,
    },
    "eco": {
        "label": "省资源模式",
        "tts_enabled": False,
        "dnd_mode": True,
        "codex_mode_enabled": False,
        "codex_allow_read": False,
        "codex_allow_write": False,
        "codex_allow_exec": False,
    },
}


class Plugin:
    name = "mode_preset"
    type = "react"

    async def run(self, args: str, ctx: Dict[str, Any]) -> str:
        parts = [x.strip() for x in (args or "").split("|||")]
        action = (parts[0].lower() if parts and parts[0] else "list")
        name = (parts[1].lower() if len(parts) > 1 and parts[1] else "")

        if action == "list":
            return self._list_presets()
        if action == "status":
            return self._status()
        if action == "apply":
            if not name:
                return "请提供模式名，例如: apply ||| codex"
            return self._apply(name, ctx)
        return "不支持的 action。可用: list/status/apply"

    def _list_presets(self) -> str:
        lines = ["可用模式："]
        for k, v in PRESETS.items():
            lines.append(f"- {k}: {v['label']}")
        return "\n".join(lines)

    def _status(self) -> str:
        s = load_runtime_settings()
        return (
            "当前运行模式状态：\n"
            f"- tts_enabled={s.get('tts_enabled', 'unknown')}\n"
            f"- dnd_mode={s.get('dnd_mode', 'unknown')}\n"
            f"- codex_mode_enabled={s.get('codex_mode_enabled', 'unknown')}\n"
            f"- codex_allow_read={s.get('codex_allow_read', 'unknown')}\n"
            f"- codex_allow_write={s.get('codex_allow_write', 'unknown')}\n"
            f"- codex_allow_exec={s.get('codex_allow_exec', 'unknown')}"
        )

    def _apply(self, name: str, ctx: Dict[str, Any]) -> str:
        if name not in PRESETS:
            return f"未知模式: {name}\n\n{self._list_presets()}"

        p = PRESETS[name]
        settings = load_runtime_settings()
        settings.update(
            {
                "tts_enabled": bool(p["tts_enabled"]),
                "dnd_mode": bool(p["dnd_mode"]),
                "codex_mode_enabled": bool(p["codex_mode_enabled"]),
                "codex_allow_read": bool(p["codex_allow_read"]),
                "codex_allow_write": bool(p["codex_allow_write"]),
                "codex_allow_exec": bool(p["codex_allow_exec"]),
            }
        )
        save_runtime_settings(settings)

        try:
            import config
            config.TTS_ENABLED = bool(p["tts_enabled"])
            config.DND_MODE = bool(p["dnd_mode"])
        except Exception:
            pass

        chat_service = (ctx or {}).get("chat_service")
        if chat_service and getattr(chat_service, "presenter", None):
            try:
                chat_service.presenter.set_tts_enabled(bool(p["tts_enabled"]))
            except Exception:
                pass

        return (
            f"已切换到模式: {name} ({p['label']})\n"
            f"- TTS={'开' if p['tts_enabled'] else '关'}\n"
            f"- DND={'开' if p['dnd_mode'] else '关'}\n"
            f"- Codex默认={'开' if p['codex_mode_enabled'] else '关'}"
        )
