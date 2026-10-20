import asyncio
from typing import Optional

from modules.vision.capture import take_camera_photo_base64, take_screenshot_base64
from plugins.composite_plugin import attach_inner_config, setting_value
from plugins.qq_screenshot.plugin import QQ_SCREENSHOT_HINTS
from plugins.qq_screenshot.plugin import Plugin as ScreenshotPlugin

SCREENSHOT_ALIASES = QQ_SCREENSHOT_HINTS + ("/截图发我",)
from services.capability_manager import ToolCapability, ToolCapabilityMatch


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


class Plugin:
    allow_natural_language_direct = True
    llm_commands = ["qq_screenshot"]

    def __init__(self):
        self._screenshot = ScreenshotPlugin()
        attach_inner_config(self._screenshot, "qq_screenshot")
        self.reload_config()

    def reload_config(self):
        settings = getattr(self, "settings", None) or {}
        if not settings:
            self._screenshot.reload_config()
            return
        self._screenshot._settings = {
            "capture_target": str(setting_value(settings, "capture_target", "primary") or "primary"),
            "monitor_index": int(setting_value(settings, "monitor_index", 1) or 1),
            "include_window_title": bool(setting_value(settings, "include_window_title", True)),
        }

    def resolve_access_control(
        self,
        context=None,
        *,
        invoked_tool: str = "",
        invoked_alias: str = "",
        args: str = "",
    ):
        if str(invoked_tool or "") == "qq_screenshot" or str(invoked_alias or "") in SCREENSHOT_ALIASES:
            return getattr(self._screenshot, "access_control", None)
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
