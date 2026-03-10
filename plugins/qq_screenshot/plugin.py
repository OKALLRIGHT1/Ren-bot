import asyncio
import json
import re
from pathlib import Path

from modules.vision.capture import get_active_window_title, get_display_regions, take_screenshot_file


QQ_SCREENSHOT_HINTS = (
    "截图发我",
    "截图发给我",
    "截个图发我",
    "截个图发给我",
    "发个截图",
    "发张截图",
    "发截图给我",
    "把截图发我",
    "把截图发给我",
    "把屏幕发我",
    "发屏幕给我",
    "把屏幕截图发我",
    "把屏幕截图发给我",
    "给我看下屏幕",
    "给我看看屏幕",
    "截屏给我",
    "截屏发我",
    "截屏发给我",
    "屏幕截图",
)

ZH_NUMBERS = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}


class Plugin:
    def __init__(self):
        self._config_path = Path(__file__).with_name("config.json")
        self._config = {}
        self._settings = {}
        self.reload_config()

    def reload_config(self):
        try:
            self._config = json.loads(self._config_path.read_text(encoding="utf-8"))
        except Exception:
            self._config = {}
        settings = self._config.get("settings") or {}
        self._settings = {
            "capture_target": self._read_setting(settings, "capture_target", "primary"),
            "monitor_index": int(self._read_setting(settings, "monitor_index", 1) or 1),
            "include_window_title": bool(self._read_setting(settings, "include_window_title", True)),
        }

    def _read_setting(self, settings: dict, key: str, default):
        value = settings.get(key, default)
        if isinstance(value, dict):
            return value.get("default", default)
        return value

    def _resolve_capture_options(self, args: str):
        text = str(args or "").strip()
        target = str(self._settings.get("capture_target") or "primary").strip().lower()
        monitor_index = max(1, int(self._settings.get("monitor_index") or 1))
        include_window_title = bool(self._settings.get("include_window_title", True))

        if any(keyword in text for keyword in ("全部屏", "所有屏", "全屏幕", "全部显示器", "所有显示器")):
            target = "all"
        elif any(keyword in text for keyword in ("主屏", "主屏幕", "主显示器")):
            target = "primary"

        digit_match = re.search(r"第\s*(\d+)\s*[块个]?屏", text)
        if not digit_match:
            digit_match = re.search(r"(\d+)\s*号屏", text)
        if digit_match:
            target = "monitor"
            monitor_index = max(1, int(digit_match.group(1)))
        else:
            zh_match = re.search(r"第\s*([一二三四五六七八九])\s*[块个]?屏", text)
            if zh_match:
                target = "monitor"
                monitor_index = ZH_NUMBERS.get(zh_match.group(1), monitor_index)

        if any(keyword in text for keyword in ("不要标题", "别带标题", "不带标题")):
            include_window_title = False
        elif any(keyword in text for keyword in ("带标题", "加标题", "窗口标题")):
            include_window_title = True

        return target, monitor_index, include_window_title

    def _build_target_label(self, target: str, monitor_index: int, display_count: int) -> str:
        if target == "all":
            return f"全部屏幕（共 {display_count} 块）"
        if target == "monitor":
            return f"第 {monitor_index} 块屏幕"
        return "主屏"

    async def run(self, args: str, context: dict):
        source = str((context or {}).get("source") or "").strip().lower()
        if source not in {"qq_gateway", "napcat_qq"}:
            return "?? 远程截图功能目前只支持 QQ 里调用。"

        text = str(args or "").strip()
        lowered = text.lower()
        if text and not any(keyword in text for keyword in QQ_SCREENSHOT_HINTS) and "screenshot" not in lowered:
            return "?? 如果你想让我把当前屏幕发到 QQ，请直接说“截图发我”或“给我看看屏幕”。"

        target, monitor_index, include_window_title = self._resolve_capture_options(text)
        displays = get_display_regions()
        display_count = len(displays)
        if target == "monitor" and display_count and monitor_index > display_count:
            return f"?? 当前只检测到 {display_count} 块屏，没找到第 {monitor_index} 块。"

        image_path = await asyncio.to_thread(
            take_screenshot_file,
            1600,
            "JPEG",
            target,
            monitor_index,
        )
        if not image_path:
            return "?? 截图失败了，可能是当前系统环境不允许截图。"

        target_label = self._build_target_label(target, monitor_index, display_count or 1)
        active_title = get_active_window_title() if include_window_title else ""
        caption = f"当前活跃窗口：{active_title}" if active_title else ""
        success_text = f"🖼️ 已把{target_label}截图发给你了。"

        return {
            "__type__": "gateway_image",
            "image_path": image_path,
            "caption": caption,
            "success_text": success_text,
            "fallback_text": f"?? {target_label}已经截好了，但回发到 QQ 失败了。",
        }
