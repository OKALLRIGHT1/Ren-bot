import asyncio
import re
from typing import Optional

from modules.vision.capture import (
    get_active_window_title,
    get_display_regions,
    take_camera_photo_base64,
    take_screenshot_base64,
    take_screenshot_file,
)
from plugins.composite_plugin import setting_value
from services.capability_manager import ToolCapability, ToolCapabilityMatch

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

SCREENSHOT_ALIASES = QQ_SCREENSHOT_HINTS + ("/截图发我",)

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

SCREEN_HINTS = (
    "截图",
    "截屏",
    "屏幕",
    "看屏幕",
    "看看屏幕",
    "屏幕分析",
    "分析屏幕",
)
CAMERA_HINTS = (
    "拍照",
    "拍一张",
    "拍一下",
    "摄像头",
    "看摄像头",
    "看看摄像头",
    "自拍",
    "照相",
    "脸",
)
REMOTE_SEND_HINTS = (
    "发我",
    "发给我",
    "回发",
    "传给我",
    "给我发",
)


class RemoteScreenshotHandler:
    def __init__(self, parent_plugin=None):
        self._parent = parent_plugin
        self._settings = {
            "capture_target": "primary",
            "monitor_index": 1,
            "include_window_title": True,
        }

    def reload_config(self):
        settings = getattr(self._parent, "settings", None) or {}
        self._settings = {
            "capture_target": str(setting_value(settings, "capture_target", "primary") or "primary"),
            "monitor_index": int(setting_value(settings, "monitor_index", 1) or 1),
            "include_window_title": bool(setting_value(settings, "include_window_title", True)),
        }

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
            return "远程截图功能目前只支持 QQ 里调用。"

        text = str(args or "").strip()
        lowered = text.lower()
        if text and not any(keyword in text for keyword in QQ_SCREENSHOT_HINTS) and "screenshot" not in lowered:
            return "如果你想让我把当前屏幕发到 QQ，请直接说“截图发我”或“给我看看屏幕”。"

        target, monitor_index, include_window_title = self._resolve_capture_options(text)
        displays = get_display_regions()
        display_count = len(displays)
        if target == "monitor" and display_count and monitor_index > display_count:
            return f"当前只检测到 {display_count} 块屏，没找到第 {monitor_index} 块。"

        image_path = await asyncio.to_thread(
            take_screenshot_file,
            1600,
            "JPEG",
            target,
            monitor_index,
        )
        if not image_path:
            return "截图失败了，可能是当前系统环境不允许截图。"

        target_label = self._build_target_label(target, monitor_index, display_count or 1)
        active_title = get_active_window_title() if include_window_title else ""
        caption = f"当前活跃窗口：{active_title}" if active_title else ""
        success_text = f"🖼️ 已把{target_label}截图发给你了。"

        return {
            "__type__": "gateway_image",
            "image_path": image_path,
            "caption": caption,
            "success_text": success_text,
            "fallback_text": f"{target_label}已经截好了，但回发到 QQ 失败了。",
        }


class Plugin:
    allow_natural_language_direct = True
    llm_commands = ["qq_screenshot"]

    def __init__(self):
        self._screenshot = RemoteScreenshotHandler(self)
        self.reload_config()

    def reload_config(self):
        self._screenshot.reload_config()

    def resolve_access_control(
        self,
        context=None,
        *,
        invoked_tool: str = "",
        invoked_alias: str = "",
        args: str = "",
    ):
        return getattr(self, "access_control", None)

    def should_handle_direct(self, text: str, context: dict, key: str) -> bool:
        raw = str(text or "").strip()
        if str(key or "") in SCREENSHOT_ALIASES:
            return any(hint in raw for hint in SCREENSHOT_ALIASES)
        return False

    def get_capabilities(self):
        return [
            ToolCapability(
                id="vision.screen",
                plugin="vision",
                trigger_mode="natural",
                match=lambda text, ctx: self._match_mode(text, "screen"),
                description="Capture and inspect the current screen.",
                examples=["帮我看看屏幕", "分析一下当前截图"],
            ),
            ToolCapability(
                id="vision.camera",
                plugin="vision",
                trigger_mode="natural",
                match=lambda text, ctx: self._match_mode(text, "camera"),
                description="Capture and inspect the camera image.",
                examples=["看看摄像头", "拍一张照片"],
            ),
        ]

    def _match_mode(self, text: str, expected_mode: str) -> Optional[ToolCapabilityMatch]:
        raw = str(text or "").strip()
        if self._detect_mode(raw) != expected_mode:
            return None
        return ToolCapabilityMatch(
            capability_id=f"vision.{expected_mode}",
            plugin="vision",
            score=0.9,
            args={"mode": expected_mode},
            raw_text=raw,
            reason=f"vision_{expected_mode}_intent",
        )

    def _detect_mode(self, args: str) -> str:
        text = str(args or "").strip()
        lower = text.lower()

        if any(keyword in text for keyword in CAMERA_HINTS) or any(keyword in lower for keyword in ("camera", "photo", "selfie")):
            return "camera"
        if any(keyword in text for keyword in SCREEN_HINTS) or any(keyword in lower for keyword in ("screenshot", "capture", "screen")):
            return "screen"
        return ""

    def _looks_like_remote_send_request(self, args: str, context: dict) -> bool:
        source = str((context or {}).get("source") or "").strip().lower()
        if source not in {"qq_gateway", "napcat_qq"}:
            return False
        text = str(args or "").strip()
        return any(keyword in text for keyword in REMOTE_SEND_HINTS) and any(keyword in text for keyword in ("截图", "截屏", "屏幕"))

    async def run(self, args: str, context: dict):
        try:
            tool = str((context or {}).get("invoked_tool") or "")
            alias = str((context or {}).get("invoked_alias") or "")
            if tool == "qq_screenshot" or alias in SCREENSHOT_ALIASES:
                return await self._screenshot.run(args, context)
            if self._looks_like_remote_send_request(args, context):
                return await self._screenshot.run(args, context)

            mode = self._detect_mode(args)
            if not mode:
                return None

            print(f"[Vision] 开始获取图像 (模式: {mode})...")

            if mode == "camera":
                img_b64 = await asyncio.to_thread(take_camera_photo_base64)
                prompt_text = "用户让你看摄像头拍到的画面（可能是用户本人）。"
            else:
                img_b64 = await asyncio.to_thread(take_screenshot_base64)
                prompt_text = "用户让你看当前的屏幕截图。"

            if not img_b64:
                return "图像获取失败（可能是摄像头未连接或截图权限受限）。"

            return {
                "__type__": "image_payload",
                "image_base64": img_b64,
                "text": prompt_text,
                "mode": mode,
            }

        except Exception as e:
            return f"视觉模块异常: {e}"
