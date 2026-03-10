import asyncio

from modules.vision.capture import take_camera_photo_base64, take_screenshot_base64


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
            if self._looks_like_remote_send_request(args, context):
                return None

            mode = self._detect_mode(args)
            if not mode:
                return None

            print(f"?? [Vision] ?????? (??: {mode})...")

            if mode == "camera":
                img_b64 = await asyncio.to_thread(take_camera_photo_base64)
                prompt_text = "用户让你看摄像头拍到的画面（可能是用户本人）。"
            else:
                img_b64 = await asyncio.to_thread(take_screenshot_base64)
                prompt_text = "用户让你看当前的屏幕截图。"

            if not img_b64:
                return "?? 图像获取失败（可能是摄像头未连接或截图权限受限）。"

            return {
                "__type__": "image_payload",
                "image_base64": img_b64,
                "text": prompt_text,
                "mode": mode,
            }

        except Exception as e:
            return f"?? 视觉模块异常: {e}"
