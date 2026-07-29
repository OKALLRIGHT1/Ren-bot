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
    import pygetwindow as gw
except Exception:
    gw = None


def _load_cv2():
    try:
        import cv2

        return cv2
    except Exception:
        return None


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
    info = get_active_window_info()
    return str(info.get("title") or "").strip()


def get_active_window_info() -> Dict[str, object]:
    """Return current foreground window title and bounds when available."""
    info: Dict[str, object] = {
        "title": "",
        "left": None,
        "top": None,
        "right": None,
        "bottom": None,
    }

    if gw is not None:
        try:
            win = gw.getActiveWindow()
            if win is not None:
                title = str(getattr(win, "title", "") or "").strip()
                left = getattr(win, "left", None)
                top = getattr(win, "top", None)
                width = getattr(win, "width", None)
                height = getattr(win, "height", None)
                if left is not None and top is not None and width is not None and height is not None:
                    left_i = int(left)
                    top_i = int(top)
                    right_i = left_i + int(width)
                    bottom_i = top_i + int(height)
                    info.update(
                        {
                            "title": title,
                            "left": left_i,
                            "top": top_i,
                            "right": right_i,
                            "bottom": bottom_i,
                        }
                    )
                    return info
                if title:
                    info["title"] = title
        except Exception:
            pass

    if os.name == "nt":
        try:
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()
            if not hwnd:
                return info

            length = int(user32.GetWindowTextLengthW(hwnd) or 0)
            title = ""
            if length > 0:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                title = str(buf.value or "").strip()

            class RECT(ctypes.Structure):
                _fields_ = [
                    ("left", ctypes.c_long),
                    ("top", ctypes.c_long),
                    ("right", ctypes.c_long),
                    ("bottom", ctypes.c_long),
                ]

            rect = RECT()
            if user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                info.update(
                    {
                        "title": title or str(info.get("title") or ""),
                        "left": int(rect.left),
                        "top": int(rect.top),
                        "right": int(rect.right),
                        "bottom": int(rect.bottom),
                    }
                )
            elif title:
                info["title"] = title
        except Exception:
            return info

    return info


def find_monitor_for_point(
    x: int, y: int, displays: Optional[List[Dict[str, int]]] = None
) -> Optional[Dict[str, int]]:
    regions = list(displays or get_display_regions())
    if not regions:
        return None
    for item in regions:
        try:
            left = int(item.get("left", 0))
            top = int(item.get("top", 0))
            right = int(item.get("right", 0))
            bottom = int(item.get("bottom", 0))
        except Exception:
            continue
        if left <= int(x) < right and top <= int(y) < bottom:
            return item
    return next((item for item in regions if bool(item.get("is_primary"))), regions[0])


def find_monitor_for_rect(
    left: int,
    top: int,
    right: int,
    bottom: int,
    displays: Optional[List[Dict[str, int]]] = None,
) -> Optional[Dict[str, int]]:
    regions = list(displays or get_display_regions())
    if not regions:
        return None

    best = None
    best_area = -1
    for item in regions:
        try:
            m_left = int(item.get("left", 0))
            m_top = int(item.get("top", 0))
            m_right = int(item.get("right", 0))
            m_bottom = int(item.get("bottom", 0))
        except Exception:
            continue
        inter_left = max(int(left), m_left)
        inter_top = max(int(top), m_top)
        inter_right = min(int(right), m_right)
        inter_bottom = min(int(bottom), m_bottom)
        width = inter_right - inter_left
        height = inter_bottom - inter_top
        if width <= 0 or height <= 0:
            continue
        area = width * height
        if area > best_area:
            best = item
            best_area = area
    if best is not None:
        return best

    cx = int((int(left) + int(right)) / 2)
    cy = int((int(top) + int(bottom)) / 2)
    return find_monitor_for_point(cx, cy, displays=regions)


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


def _primary_display(displays: List[Dict[str, int]]) -> Optional[Dict[str, int]]:
    if not displays:
        return None
    return next((item for item in displays if bool(item.get("is_primary"))), displays[0])


def _active_window_bbox(info: Optional[Dict[str, object]] = None) -> Optional[Tuple[int, int, int, int]]:
    window = info if isinstance(info, dict) else get_active_window_info()
    try:
        left = window.get("left")
        top = window.get("top")
        right = window.get("right")
        bottom = window.get("bottom")
        if left is None or top is None or right is None or bottom is None:
            return None
        left_i = int(left)
        top_i = int(top)
        right_i = int(right)
        bottom_i = int(bottom)
    except Exception:
        return None
    if right_i <= left_i or bottom_i <= top_i:
        return None
    return left_i, top_i, right_i, bottom_i


def _resolve_screenshot_selection(
    *,
    target: str = "primary",
    monitor_index: int = 1,
    displays: Optional[List[Dict[str, int]]] = None,
    active_info: Optional[Dict[str, object]] = None,
) -> Tuple[Optional[Dict[str, int]], Optional[Tuple[int, int, int, int]], List[Dict[str, int]]]:
    regions = list(displays or get_display_regions())
    if not regions:
        return None, None, []

    normalized_target = str(target or "primary").strip().lower()
    window_bbox: Optional[Tuple[int, int, int, int]] = None
    selected: Optional[Dict[str, int]] = None

    if normalized_target in {"all", "all_screens", "allscreens"}:
        selected = None
    elif normalized_target in {"monitor", "display", "screen"}:
        selected = next(
            (
                item
                for item in regions
                if int(item.get("index", 0)) == int(monitor_index or 1)
            ),
            None,
        )
        if selected is None:
            return None, None, regions
    elif normalized_target in {"active_window", "window", "foreground_window"}:
        window_bbox = _active_window_bbox(active_info)
        if window_bbox is None:
            selected = _primary_display(regions)
        else:
            selected = find_monitor_for_rect(*window_bbox, displays=regions) or _primary_display(
                regions
            )
    elif normalized_target in {"active_monitor", "foreground_monitor", "focus_monitor"}:
        window_bbox = _active_window_bbox(active_info)
        if window_bbox is not None:
            selected = find_monitor_for_rect(*window_bbox, displays=regions)
        if selected is None:
            selected = _primary_display(regions)
        window_bbox = None
    else:
        selected = _primary_display(regions)

    return selected, window_bbox, regions


def _grab_screenshot_image(
    target: str = "primary", monitor_index: int = 1
) -> Tuple[Optional[Image.Image], Optional[Dict[str, int]], List[Dict[str, int]]]:
    active_info = None
    normalized_target = str(target or "primary").strip().lower()
    if normalized_target in {
        "active_monitor",
        "foreground_monitor",
        "focus_monitor",
        "active_window",
        "window",
        "foreground_window",
    }:
        active_info = get_active_window_info()

    selected, window_bbox, displays = _resolve_screenshot_selection(
        target=target,
        monitor_index=monitor_index,
        active_info=active_info,
    )
    if not displays:
        return None, None, []
    if (
        selected is None
        and window_bbox is None
        and normalized_target
        not in {"all", "all_screens", "allscreens"}
    ):
        return None, None, displays

    try:
        if ImageGrab is not None:
            if window_bbox is not None:
                image = ImageGrab.grab(bbox=window_bbox, all_screens=(os.name == "nt"))
                return image, selected, displays
            if selected is None:
                image = ImageGrab.grab(all_screens=True)
            else:
                bbox = (
                    int(selected["left"]),
                    int(selected["top"]),
                    int(selected["right"]),
                    int(selected["bottom"]),
                )
                image = ImageGrab.grab(bbox=bbox, all_screens=(os.name == "nt"))
            return image, selected, displays

        if pyautogui is not None:
            image = pyautogui.screenshot()
            return image, selected, displays
    except Exception as e:
        print(f"❌ [Vision] 截图失败: {e}")
        return None, selected, displays

    return None, selected, displays


def take_screenshot_base64(max_size=1024, target: str = "primary", monitor_index: int = 1) -> str:
    """
    截取屏幕，缩放至长边不超过 max_size，并转为 base64。
    target 默认 primary，保持旧行为；sensor 可传 active_monitor。
    """
    try:
        screenshot, _selected, _displays = _grab_screenshot_image(
            target=target, monitor_index=monitor_index
        )
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


def take_screenshot_base64_with_meta(
    max_size=1024, target: str = "primary", monitor_index: int = 1
) -> Dict[str, object]:
    """Capture screenshot and return lightweight focus metadata."""
    active_info = get_active_window_info()
    active_title = str(active_info.get("title") or "").strip()
    selected, _window_bbox, _displays = _resolve_screenshot_selection(
        target=target,
        monitor_index=monitor_index,
        active_info=active_info,
    )
    image_b64 = take_screenshot_base64(
        max_size=max_size, target=target, monitor_index=monitor_index
    )
    return {
        "image_b64": image_b64,
        "active_title": active_title,
        "target": str(target or "primary").strip().lower() or "primary",
        "monitor_index": int((selected or {}).get("index") or monitor_index or 1),
    }


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
        cv2 = _load_cv2()
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
