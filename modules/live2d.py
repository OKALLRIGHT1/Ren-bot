# modules/live2d.py
import asyncio
import inspect
import json
import os
import random
import time
import websockets
from typing import Optional
from urllib.parse import urlparse

from config import LIVE2D_HOST

try:
    from config import LIVE2D_MODEL_IDS
except Exception:
    LIVE2D_MODEL_IDS = [0]

try:
    from config import EMO_TO_LIVE2D
except Exception:
    EMO_TO_LIVE2D = {}

try:
    from config import MOTION_MAPPING
except Exception:
    MOTION_MAPPING = {}

from config import TTS_RETURN_IDLE, TTS_IDLE_EMO
from core.logger import get_logger
from services.runtime_health import get_runtime_health

_CURRENT_COSTUME_CONFIG = {}
_CURRENT_COSTUME_EMOTION_MAP = {}
_CURRENT_COSTUME_MODEL_PATH = ""
MODEL_DEFAULT_MOTION = "__model_default__"
STOP_MOTION = "__stop_motion__"


def _get_logger():
    """延迟获取 logger 实例"""
    return get_logger()


def estimate_bubble_display_ms(text: str, *, minimum: int = 3200, maximum: int = 12000) -> int:
    clean = str(text or "").strip()
    read_ms = 2600 + len(clean) * 160
    return max(int(minimum), min(int(read_ms), int(maximum)))


def normalize_costume_model_path(model_path: Optional[str]) -> str:
    """Normalize a model path for costume identity comparison."""
    raw = str(model_path or "").strip().replace("\\", "/")
    if not raw:
        return ""
    try:
        abs_path = os.path.abspath(raw).replace("\\", "/")
    except Exception:
        abs_path = raw
    if os.name == "nt":
        return abs_path.lower()
    return abs_path


def is_same_costume_model_path(left: Optional[str], right: Optional[str]) -> bool:
    left_norm = normalize_costume_model_path(left)
    right_norm = normalize_costume_model_path(right)
    return bool(left_norm and right_norm and left_norm == right_norm)


def get_current_costume_model_path() -> str:
    return str(_CURRENT_COSTUME_MODEL_PATH or "").strip()


def update_current_costume_config(config: Optional[dict], model_path: Optional[str] = None) -> None:
    """Update the active costume runtime config without reloading the model."""
    safe_cfg = config if isinstance(config, dict) else {}
    global _CURRENT_COSTUME_CONFIG, _CURRENT_COSTUME_EMOTION_MAP, _CURRENT_COSTUME_MODEL_PATH
    _CURRENT_COSTUME_CONFIG = safe_cfg
    if model_path is not None:
        _CURRENT_COSTUME_MODEL_PATH = str(model_path or "").strip()
    emotion_map = safe_cfg.get("emotion_map", {})
    _CURRENT_COSTUME_EMOTION_MAP = emotion_map if isinstance(emotion_map, dict) else {}


async def go_idle():
    if not TTS_RETURN_IDLE:
        return
    try:
        used = await trigger_emotion(TTS_IDLE_EMO)
        if not used:
            await clear_expression()
    except Exception as e:
        _get_logger().warning(f"go_idle 失败: {e}")


CONNECT_TIMEOUT = 5.0
PING_TIMEOUT = 5.0
SEND_TIMEOUT = 1.5
CONNECTION_POOL_MAX_AGE = 300  # 5分钟后重新建立连接
MAX_RECONNECT_DELAY = 15.0

_RESOLVED_HOST = None


# ==========================================
# WebSocket 连接池实现
# ==========================================


class Live2DConnectionBackoffError(ConnectionError):
    pass


def _safe_host_label(host: str) -> str:
    parsed = urlparse(str(host or ""))
    if not parsed.hostname:
        return "unknown"
    return f"{parsed.hostname}:{parsed.port}" if parsed.port else parsed.hostname


class WebSocketConnectionPool:
    """WebSocket 连接池：复用连接避免频繁创建/关闭，并串行化发送。"""

    def __init__(
        self,
        *,
        health=None,
        clock=None,
        wall_clock=None,
        jitter=None,
    ):
        self._connection: Optional[websockets.WebSocketClientProtocol] = None
        self._lock = asyncio.Lock()
        self._send_lock = asyncio.Lock()  # ✅ 串行化 ws.send
        self._host: Optional[str] = None
        self._created_at: Optional[float] = None
        self._is_connected: bool = False
        self._last_ping_at: float = 0.0  # ✅ 可选：降低 ping 频率
        self._failure_count = 0
        self._next_retry_at = 0.0
        self._last_success_at = 0.0
        self._health = health or get_runtime_health()
        self._clock = clock or (lambda: asyncio.get_running_loop().time())
        self._wall_clock = wall_clock or time.time
        self._jitter = jitter or (
            lambda delay: random.uniform(0.0, min(0.25, delay * 0.1))
        )

    def _backoff_delay(self, failure_count: int) -> float:
        base = min(
            MAX_RECONNECT_DELAY,
            float(2 ** max(0, int(failure_count) - 1)),
        )
        jitter = max(0.0, float(self._jitter(base)))
        return min(MAX_RECONNECT_DELAY, base + jitter)

    def _report(self, state: str, summary: str, *, error: str = "") -> None:
        retry_remaining = max(0.0, self._next_retry_at - self._clock())
        try:
            self._health.report(
                "live2d_ws",
                state,
                summary,
                details={
                    "host": _safe_host_label(self._host or LIVE2D_HOST),
                    "consecutive_failures": self._failure_count,
                    "next_retry_at": (
                        self._wall_clock() + retry_remaining
                        if retry_remaining > 0
                        else None
                    ),
                    "last_success_at": self._last_success_at or None,
                    "error_category": error,
                },
            )
        except Exception:
            pass

    def _record_failure(self, exc: BaseException, now: float) -> None:
        self._failure_count += 1
        self._next_retry_at = now + self._backoff_delay(self._failure_count)
        self._is_connected = False
        self._report(
            "reconnecting",
            "Live2D WebSocket 正在退避重连",
            error=type(exc).__name__,
        )

    def _record_success(self, now: float) -> None:
        self._failure_count = 0
        self._next_retry_at = 0.0
        self._last_success_at = self._wall_clock()
        self._report("healthy", "Live2D WebSocket 已连接")

    def _raise_if_backing_off(self, now: float) -> None:
        remaining = self._next_retry_at - now
        if remaining > 0:
            raise Live2DConnectionBackoffError(
                f"Live2D connection unavailable for {remaining:.1f}s"
            )

    async def get_connection(self) -> websockets.WebSocketClientProtocol:
        async with self._lock:
            self._raise_if_backing_off(self._clock())
            if await self._should_reconnect():
                await self._create_connection()
            return self._connection

    async def mark_broken(self) -> None:
        """标记连接不可用（带锁，避免竞态）"""
        async with self._lock:
            self._is_connected = False
            now = self._clock()
            if self._next_retry_at <= now:
                self._record_failure(ConnectionError("connection marked broken"), now)

    async def _should_reconnect(self) -> bool:
        if self._connection is None or not self._is_connected:
            return True

        if self._created_at is not None:
            age = self._clock() - self._created_at
            if age > CONNECTION_POOL_MAX_AGE:
                _get_logger().info(f"连接池连接已使用 {age:.1f} 秒，重新建立连接")
                return True

        # ✅ 降低 ping 频率：最多每 5 秒 ping 一次（避免高频 get_connection 导致卡顿）
        now = self._clock()
        if now - self._last_ping_at < 5.0:
            return False
        self._last_ping_at = now

        try:
            async def _ping() -> None:
                pong = await self._connection.ping()
                if inspect.isawaitable(pong):
                    await pong

            await asyncio.wait_for(_ping(), timeout=PING_TIMEOUT)
            self._record_success(now)
            return False
        except Exception as e:
            _get_logger().warning(f"连接健康检查失败: {e}，进入退避重连")
            self._record_failure(e, now)
            remaining = max(0.0, self._next_retry_at - now)
            raise Live2DConnectionBackoffError(
                f"Live2D connection unavailable for {remaining:.1f}s"
            ) from e

    async def _create_connection(self) -> None:
        if self._connection is not None:
            try:
                await self._connection.close()
            except Exception as e:
                _get_logger().debug(f"关闭旧连接时出错: {e}")

        host = await _resolve_host()
        self._host = host

        try:
            self._connection = await _ws_connect(host)
        except Exception as exc:
            self._connection = None
            self._record_failure(exc, self._clock())
            raise
        self._is_connected = True
        self._created_at = self._clock()
        self._record_success(self._created_at)
        _get_logger().debug(f"WebSocket 连接池已创建: {host}")

    async def close(self) -> None:
        async with self._lock:
            if self._connection is not None:
                try:
                    await self._connection.close()
                    _get_logger().info("WebSocket 连接池已关闭")
                except Exception as e:
                    _get_logger().warning(f"关闭连接池时出错: {e}")
                finally:
                    self._connection = None
                    self._is_connected = False
            self._report("offline", "Live2D WebSocket 已关闭")


# 全局连接池实例
_connection_pool = WebSocketConnectionPool()


async def _ws_connect(host: str):
    return await asyncio.wait_for(
        websockets.connect(host, ping_interval=None), timeout=CONNECT_TIMEOUT
    )


async def _try_host(host: str) -> bool:
    try:
        ws = await _ws_connect(host)
        await ws.close()
        return True
    except Exception:
        return False


async def _resolve_host() -> str:
    global _RESOLVED_HOST
    if _RESOLVED_HOST:
        return _RESOLVED_HOST

    _get_logger().info("正在并发扫描端口 10086-10100, 20000-20020 ...")

    ports = list(range(10086, 10101)) + list(range(20000, 20021))
    tasks = []
    for p in ports:
        host = f"ws://127.0.0.1:{p}/api"
        tasks.append(_try_host(host))

    results = await asyncio.gather(*tasks)

    for i, success in enumerate(results):
        if success:
            found = f"ws://127.0.0.1:{ports[i]}/api"
            _get_logger().info(f"发现端口: {found}")
            _RESOLVED_HOST = found
            return found

    _get_logger().warning(f"未找到 ExAPI，将使用默认: {LIVE2D_HOST}")
    _RESOLVED_HOST = LIVE2D_HOST
    return _RESOLVED_HOST


async def _send_to_models(msg: int, msg_id: int, data_builder, max_retries: int = 2):
    """
    构造 Live2D 指令并通过可插拔传输总线输出。
    默认总线仅包含本地旧 WebSocket；应用层可注入 GUI 组合总线。
    """
    del max_retries  # retries are owned by individual transports
    from modules.live2d_transport import send_live2d_message

    last_error: Exception | None = None
    for mid in LIVE2D_MODEL_IDS:
        payload = {"msg": msg, "msgId": msg_id, "data": data_builder(mid)}
        try:
            await send_live2d_message(payload)
        except Exception as exc:
            last_error = exc
            _get_logger().error(f"发送失败: {exc}")
    if last_error is not None:
        raise last_error


# ==========================================
# 核心控制函数
# ==========================================


def _normalize_motion_name(raw_motion_name: str) -> str:
    name = str(raw_motion_name or "").strip()
    if not name:
        return ""
    if ":" in name:
        return name
    return f"Motion:{name}"


def _motion_name_from_file(file_name: str) -> str:
    raw = str(file_name or "").replace("\\", "/").strip()
    if not raw:
        return ""
    name = raw.rsplit("/", 1)[-1]
    lowered = name.lower()
    for suffix in [".motion3.json", ".mtn"]:
        if lowered.endswith(suffix):
            return name[: -len(suffix)].strip()
    if "." in name:
        name = name.rsplit(".", 1)[0]
    return name.strip()


def _is_generic_motion_group(group_name: str) -> bool:
    group = str(group_name or "").strip().lower()
    return group in {"", "motion", "motions", "idle", "tapbody"}


def _iter_motion_groups(raw_motion_refs):
    if isinstance(raw_motion_refs, dict):
        for group_name, items in raw_motion_refs.items():
            if isinstance(items, list):
                yield str(group_name), items
        return
    if isinstance(raw_motion_refs, list):
        yield "Motion", raw_motion_refs


def resolve_model_default_motion(model_path: Optional[str] = None) -> Optional[str]:
    path = str(model_path or _CURRENT_COSTUME_MODEL_PATH or "").strip()
    if not path:
        return None
    abs_path = os.path.abspath(path)
    try:
        with open(abs_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None

    refs = data.get("FileReferences", {}) if isinstance(data, dict) else {}
    motion_refs = refs.get("Motions", {}) if isinstance(refs, dict) else {}
    if motion_refs:
        groups = list(_iter_motion_groups(motion_refs))
        group_names = [name for name, _ in groups]
        selected_name = (
            next((name for name in group_names if name.lower() == "idle"), "")
            or next((name for name in group_names if name.lower() == "motion"), "")
            or (group_names[0] if group_names else "")
        )
        for group_name, motion_items in groups:
            if group_name != selected_name or not motion_items:
                continue
            first = motion_items[0]
            if isinstance(first, dict):
                file_name = str(first.get("File") or first.get("file") or "").strip()
                raw_name = (
                    first.get("Name")
                    or first.get("name")
                    or first.get("mtn")
                    or _motion_name_from_file(file_name)
                )
            else:
                raw_name = _motion_name_from_file(str(first)) or str(first)
            motion_name = str(raw_name or "").strip()
            return _normalize_motion_name(motion_name or f"{group_name}:0")

    legacy_motions = data.get("motions", {}) if isinstance(data, dict) else {}
    groups = list(_iter_motion_groups(legacy_motions))
    if not groups:
        return None
    group_names = [name for name, _ in groups]
    selected_name = (
        next((name for name in group_names if name.lower() == "idle"), "")
        or next((name for name in group_names if name.lower() == "motion"), "")
        or group_names[0]
    )
    for group_name, motion_items in groups:
        if group_name != selected_name or not motion_items:
            continue
        first = motion_items[0]
        if isinstance(first, dict):
            file_name = str(first.get("file") or first.get("File") or "").strip()
            raw_name = first.get("name") or first.get("Name") or first.get("mtn")
            if not raw_name and not _is_generic_motion_group(group_name):
                raw_name = group_name
            if not raw_name:
                raw_name = _motion_name_from_file(file_name)
        else:
            raw_name = (
                group_name
                if not _is_generic_motion_group(group_name)
                else _motion_name_from_file(str(first)) or str(first)
            )
        motion_name = str(raw_name or "").strip()
        if not motion_name:
            return None
        return (
            motion_name
            if ":" in motion_name
            else f"{str(group_name or '').strip()}:{motion_name}"
        )
    return None


async def play_motion(mtn: str, motion_type: int = 0):
    motion_name = str(mtn or "").strip()
    if motion_name == MODEL_DEFAULT_MOTION:
        motion_name = resolve_model_default_motion() or ""
        if not motion_name:
            return
    await _send_to_models(
        msg=13200,
        msg_id=2,
        data_builder=lambda mid: {"id": mid, "type": int(motion_type), "mtn": motion_name},
    )


def pick_motion_candidate(cfg, rng=None):
    if not isinstance(cfg, dict):
        if isinstance(cfg, str) and cfg.strip():
            return {"mtn": cfg.strip(), "type": 0}
        return None

    choices = cfg.get("motions")
    valid = []
    if isinstance(choices, list):
        for item in choices:
            if isinstance(item, dict):
                mtn = str(item.get("mtn") or "").strip()
                if not mtn:
                    continue
                try:
                    motion_type = int(item.get("type", cfg.get("type", 0)) or 0)
                except Exception:
                    motion_type = 0
                valid.append({"mtn": mtn, "type": motion_type})
            elif isinstance(item, str) and item.strip():
                valid.append({"mtn": item.strip(), "type": int(cfg.get("type", 0) or 0)})

    if valid:
        picker = rng if rng is not None else random
        return picker.choice(valid)

    mtn = str(cfg.get("mtn") or "").strip()
    if not mtn:
        return None
    try:
        motion_type = int(cfg.get("type", 0) or 0)
    except Exception:
        motion_type = 0
    return {"mtn": mtn, "type": motion_type}


async def set_expression(exp_value):
    await _send_to_models(
        msg=13300,
        msg_id=1,
        data_builder=lambda mid: {"id": mid, "expId": int(exp_value)},
    )


async def set_position(pos_x: int, pos_y: int):
    await _send_to_models(
        msg=13400,
        msg_id=1,
        data_builder=lambda mid: {"id": mid, "posX": int(pos_x), "posY": int(pos_y)},
    )


async def clear_expression():
    await _send_to_models(
        msg=13302,
        msg_id=1,
        data_builder=lambda mid: mid,
    )


async def play_sound_file(
    path: str,
    channel: int = 0,
    volume: float = 1.0,
    delay_ms: int = 0,
    loop: bool = False,
):
    abs_path = os.path.abspath(path)
    await _send_to_models(
        msg=13500,
        msg_id=4,
        data_builder=lambda mid: {
            "id": mid,
            "channel": int(channel),
            "volume": float(volume),
            "delay": int(delay_ms),
            "loop": bool(loop),
            "type": 0,
            "sound": abs_path,
        },
    )


async def stop_sound(channel: int = 0):
    await _send_to_models(
        msg=13501,
        msg_id=5,
        data_builder=lambda mid: {"id": mid, "channel": int(channel)},
    )


# ========== 🔴 新增：换装指令 ==========
async def change_costume(model_path: str, config: dict = None):
    """
    发送换装指令 [msg: 12000]
    自动将相对路径转换为绝对路径，并修复 Windows 反斜杠问题
    """
    # 1. 获取绝对路径
    abs_path = os.path.abspath(model_path)

    # 🔴【核心修复】强制将 Windows 反斜杠替换为 Web 标准正斜杠
    # Live2D 库在 Web 环境下必须使用 "/" 才能正确解析相对路径
    abs_path = abs_path.replace("\\", "/")

    safe_cfg = config if isinstance(config, dict) else {}
    update_current_costume_config(safe_cfg, model_path=abs_path)
    derived_keys = safe_cfg.get("derived_emotion_keys")
    if derived_keys:
        _get_logger().info(
            f"切换服装: {abs_path} | 自动补齐情绪动作: {derived_keys}"
        )
    else:
        _get_logger().info(f"切换服装: {abs_path} | Config: {safe_cfg}")

    await _send_to_models(
        msg=12000,
        msg_id=10,
        data_builder=lambda mid: {"id": mid, "path": abs_path, "config": safe_cfg},
    )

    async def _settle_to_idle():
        await asyncio.sleep(0.6)
        cfg = resolve_emotion_config("idle", EMO_TO_LIVE2D)
        if not cfg:
            cfg = resolve_emotion_config("neutral", EMO_TO_LIVE2D)
        if not cfg:
            return
        exp = cfg.get("exp")
        mtn = cfg.get("mtn")
        if exp is not None:
            await set_expression(int(exp))
        if mtn:
            await play_motion(str(mtn), motion_type=int(cfg.get("type", 0) or 0))

    if not safe_cfg.get("suppress_auto_idle"):
        try:
            asyncio.create_task(_settle_to_idle())
        except Exception:
            pass


def resolve_emotion_config(emotion: str, default_mapping: Optional[dict] = None):
    emo = (emotion or "").strip().lower()
    if not emo:
        return None

    if isinstance(_CURRENT_COSTUME_EMOTION_MAP, dict):
        override = _CURRENT_COSTUME_EMOTION_MAP.get(emo)
        if isinstance(override, dict):
            return override

    mapping = default_mapping if isinstance(default_mapping, dict) else EMO_TO_LIVE2D
    if not isinstance(mapping, dict):
        return None
    return mapping.get(emo)


# =====================================


def _pick_keyword_mapping(text: str):
    t = text or ""
    for k, v in (MOTION_MAPPING or {}).items():
        if k == "默认":
            continue
        if k and (k in t):
            return v
    return (MOTION_MAPPING or {}).get("默认")


async def trigger_emotion(emotion: Optional[str]) -> bool:
    if not emotion:
        return False
    emo = emotion.strip().lower()
    source = "current_costume" if isinstance(_CURRENT_COSTUME_EMOTION_MAP, dict) and emo in _CURRENT_COSTUME_EMOTION_MAP else "global"
    cfg = resolve_emotion_config(emo, EMO_TO_LIVE2D)
    if not cfg and emo == "music":
        source = "fallback:happy"
        cfg = resolve_emotion_config("happy", EMO_TO_LIVE2D)
    if not cfg:
        source = "fallback:neutral"
        cfg = resolve_emotion_config("neutral", EMO_TO_LIVE2D)
    if not cfg:
        source = "fallback:idle"
        cfg = resolve_emotion_config("idle", EMO_TO_LIVE2D)
    if not cfg:
        _get_logger().warning(f"[Live2D Emotion] 未找到情绪配置: emotion={emo}")
        return False
    exp = cfg.get("exp", None)
    motion = pick_motion_candidate(cfg)
    mtn = motion.get("mtn") if motion else None
    mtype = motion.get("type", 0) if motion else 0
    _get_logger().info(
        f"[Live2D Emotion] emotion={emo} source={source} exp={exp} mtn={mtn} type={mtype}"
    )
    if exp is not None:
        await set_expression(int(exp))
    if mtn:
        await play_motion(str(mtn), motion_type=mtype)
    return True


async def trigger_motion(text: str):
    cfg = _pick_keyword_mapping(text)
    if not cfg:
        return

    exp = None
    mtn = None
    mtype = 0

    if isinstance(cfg, dict):
        exp = cfg.get("exp", None)
        mtn = cfg.get("mtn", None) or cfg.get("file") or cfg.get("path")
        mtype = int(cfg.get("type", 0))
    elif isinstance(cfg, str):
        mtn = cfg

    if exp is not None:
        await set_expression(int(exp))
    if mtn:
        await play_motion(str(mtn), motion_type=mtype)


async def send_bubble(
    text: str,
    emotion: Optional[str] = None,
    duration_ms: Optional[int] = None,
    **kwargs,
):
    text = (text or "").strip()

    try:
        used = await trigger_emotion(emotion)
        if not used:
            await trigger_motion(text)
    except Exception as e:
        _get_logger().warning(f"动作/表情触发失败: {e}")

    # 语音时长经常比阅读速度短；气泡显示按阅读下限对齐。
    read_ms = estimate_bubble_display_ms(text)
    if duration_ms is None or duration_ms <= 0:
        duration_ms = read_ms
    else:
        duration_ms = max(int(duration_ms), int(read_ms))
    duration_ms += 80

    await _send_to_models(
        msg=11000,
        msg_id=3,
        data_builder=lambda mid: {
            "id": mid,
            "text": text,
            "duration": int(duration_ms),
        },
    )


# ========== 🎤 新增：口型同步指令 ==========
async def send_lip_sync(lip_data: list):
    """
    发送口型同步数据到 Live2D 前端 [msg: 13600]

    Args:
        lip_data: 口型数据列表，格式：[{"time": 0.0, "mouth": 0.3}, ...]
                 - time: 时间点（秒）
                 - mouth: 嘴部张开程度（0.0-1.0）

    前端接收到的消息格式：
    {
        "msg": 13600,
        "msgId": 7,
        "data": {
            "id": model_id,
            "lipSync": [
                {"time": 0.0, "mouth": 0.3},
                {"time": 0.1, "mouth": 0.5},
                ...
            ],
            "duration": 2.5  # 总时长（秒）
        }
    }
    """
    if not lip_data:
        _get_logger().debug("口型数据为空，跳过发送")
        return

    # 计算总时长
    duration = lip_data[-1]["time"] if lip_data else 0

    _get_logger().info(
        f"[Live2D msg=13600] 发送口型同步数据: {len(lip_data)} 个时间点, 总时长 {duration:.2f}s"
    )

    try:
        await _send_to_models(
            msg=13600,
            msg_id=7,
            data_builder=lambda mid: {
                "id": mid,
                "lipSync": lip_data,
                "duration": float(duration),
            },
        )
    except Exception as e:
        _get_logger().error(f"口型同步数据发送失败: {e}")


# ==========================================
