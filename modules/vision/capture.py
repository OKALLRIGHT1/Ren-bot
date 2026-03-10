# modules/vision/capture.py
import base64
import ctypes
import io
import os
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from PIL import Image

try:
    from PIL import ImageGrab
except Exception:
    ImageGrab = None

try:
    import pyautogui
except Exception:
    pyautogui = None

try:
    import cv2
except Exception:
    cv2 = None

try:
    import pygetwindow as gw
except Exception:
    gw = None


def encode_image_to_base64(image: Image.Image, format="JPEG") -> str:
    """辅助函数：将 PIL 图片转为 Base64 字符串"""
    buffered = io.BytesIO()
    # 85% 质量压缩，平衡清晰度与 Token 消耗
    image.save(buffered, format=format, quality=85)
    return base64.b64encode(buffered.getvalue()).decode('utf-8')


def save_image_to_temp_file(image: Image.Image, format="JPEG", prefix="live2d_capture_") -> str:
    suffix = ".jpg" if str(format).upper() == "JPEG" else f".{str(format).lower()}"
    temp_dir = Path(tempfile.gettempdir()) / "live2d_llm_media"
    temp_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix=prefix, suffix=suffix, delete=False, dir=temp_dir) as tmp:
        save_kwargs = {"format": format}
        if str(format).upper() == "JPEG":
            save_kwargs["quality"] = 90
        image.save(tmp, **save_kwargs)
        return tmp.name


def _resize_if_needed(image: Image.Image, max_size=1024) -> Image.Image:
    w, h = image.size
    if max(w, h) > max_size:
        scale = max_size / max(w, h)
        new_w = int(w * scale)
        new_h = int(h * scale)
        return image.resize((new_w, new_h), Image.Resampling.LANCZOS)
    return image


def get_active_window_title() -> str:
    if gw is None:
        return ""
    try:
        win = gw.getActiveWindow()
        if not win:
            return ""
        return str(win.title or "").strip()
    except Exception:
        return ""


def get_display_regions() -> List[Dict[str, int]]:
    regions: List[Dict[str, int]] = []

    if os.name == 'nt':
        try:
            from ctypes import wintypes

            class RECT(ctypes.Structure):
                _fields_ = [
                    ("left", ctypes.c_long),
                    ("top", ctypes.c_long),
                    ("right", ctypes.c_long),
                    ("bottom", ctypes.c_long),
                ]

            class MONITORINFO(ctypes.Structure):
                _fields_ = [
                    ("cbSize", wintypes.DWORD),
                    ("rcMonitor", RECT),
                    ("rcWork", RECT),
                    ("dwFlags", wintypes.DWORD),
                ]

            monitor_enum_proc = ctypes.WINFUNCTYPE(
                ctypes.c_int,
                wintypes.HMONITOR,
                wintypes.HDC,
                ctypes.POINTER(RECT),
                wintypes.LPARAM,
            )

            MONITORINFOF_PRIMARY = 1

            def _callback(h_monitor, _hdc, _rect, _lparam):
                info = MONITORINFO()
                info.cbSize = ctypes.sizeof(MONITORINFO)
                if ctypes.windll.user32.GetMonitorInfoW(h_monitor, ctypes.byref(info)):
                    left = int(info.rcMonitor.left)
                    top = int(info.rcMonitor.top)
                    right = int(info.rcMonitor.right)
                    bottom = int(info.rcMonitor.bottom)
                    regions.append({
                        "index": len(regions) + 1,
                        "left": left,
                        "top": top,
                        "right": right,
                        "bottom": bottom,
                        "width": right - left,
                        "height": bottom - top,
                        "is_primary": bool(info.dwFlags & MONITORINFOF_PRIMARY),
                    })
                return 1

            ctypes.windll.user32.EnumDisplayMonitors(0, 0, monitor_enum_proc(_callback), 0)
        except Exception:
            regions = []

    if not regions:
        try:
            if ImageGrab is not None:
                img = ImageGrab.grab()
            elif pyautogui is not None:
                img = pyautogui.screenshot()
            else:
                return []
            width, height = img.size
            regions = [{
                "index": 1,
                "left": 0,
                "top": 0,
                "right": int(width),
                "bottom": int(height),
                "width": int(width),
                "height": int(height),
                "is_primary": True,
            }]
        except Exception:
            return []

    return regions


def _grab_screenshot_image(target: str = "primary", monitor_index: int = 1) -> Tuple[Optional[Image.Image], Optional[Dict[str, int]], List[Dict[str, int]]]:
    displays = get_display_regions()
    if not displays:
        return None, None, []

    normalized_target = str(target or "primary").strip().lower()
    if normalized_target in {"all", "all_screens", "allscreens"}:
        selected = None
    elif normalized_target in {"monitor", "display", "screen"}:
        selected = next((item for item in displays if int(item.get("index", 0)) == int(monitor_index or 1)), None)
        if selected is None:
            return None, None, displays
    else:
        selected = next((item for item in displays if bool(item.get("is_primary"))), displays[0])

    try:
        if ImageGrab is not None:
            if selected is None:
                image = ImageGrab.grab(all_screens=True)
            else:
                bbox = (
                    int(selected["left"]),
                    int(selected["top"]),
                    int(selected["right"]),
                    int(selected["bottom"]),
                )
                image = ImageGrab.grab(bbox=bbox, all_screens=(os.name == 'nt'))
            return image, selected, displays

        if pyautogui is not None:
            image = pyautogui.screenshot()
            return image, selected, displays
    except Exception as e:
        print(f"❌ [Vision] 截图失败: {e}")
        return None, selected, displays

    return None, selected, displays


def take_screenshot_base64(max_size=1024) -> str:
    """
    截取屏幕，缩放至长边不超过 max_size，并转为 base64。
    """
    try:
        screenshot, _selected, _displays = _grab_screenshot_image(target="primary", monitor_index=1)
        if screenshot is None:
            print("❌ [Vision] 截图依赖不可用")
            return None

        # 2. 智能缩放 (保持长宽比，防止图片过大消耗太多流量/Token)
        screenshot = _resize_if_needed(screenshot, max_size=max_size)

        # 3. 转 Base64
        return encode_image_to_base64(screenshot)
    except Exception as e:
        print(f"❌ [Vision] 截图失败: {e}")
        return None


def take_screenshot_file(max_size=1600, format="JPEG", target="primary", monitor_index=1) -> str:
    """
    截取屏幕并保存为临时图片文件，返回文件路径。
    """
    try:
        screenshot, _selected, _displays = _grab_screenshot_image(target=target, monitor_index=monitor_index)
        if screenshot is None:
            print("❌ [Vision] 截图依赖不可用")
            return None
        screenshot = _resize_if_needed(screenshot, max_size=max_size)
        return save_image_to_temp_file(screenshot, format=format, prefix="live2d_screen_")
    except Exception as e:
        print(f"❌ [Vision] 截图文件保存失败: {e}")
        return None


def take_camera_photo_base64(camera_index=0) -> str:
    """
    调用摄像头拍照并转为 base64。
    """
    cap = None
    try:
        if cv2 is None:
            print("❌ [Vision] 摄像头依赖 cv2 未安装")
            return None
        # 0 通常是默认摄像头
        cap = cv2.VideoCapture(camera_index)
        if not cap.isOpened():
            print("❌ [Vision] 无法打开摄像头")
            return None

        # 预热几帧，防止画面全黑或白平衡未准
        for _ in range(5):
            cap.read()

        ret, frame = cap.read()
        if not ret:
            print("❌ [Vision] 无法读取摄像头画面")
            return None

        # OpenCV 是 BGR 格式，需要转为 RGB 供 PIL 处理
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        pil_img = Image.fromarray(rgb_frame)

        return encode_image_to_base64(pil_img)
    except Exception as e:
        print(f"❌ [Vision] 拍照异常: {e}")
        return None
    finally:
        if cap:
            cap.release()
