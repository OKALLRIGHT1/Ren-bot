# modules/live2d.py
import asyncio
import json
import os
import websockets
from typing import Optional

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

_CURRENT_COSTUME_CONFIG = {}
_CURRENT_COSTUME_EMOTION_MAP = {}

def _get_logger():
    """延迟获取 logger 实例"""
    return get_logger()


async def go_idle():
    if not TTS_RETURN_IDLE:
        return
    try:
        used = await trigger_emotion(TTS_IDLE_EMO)
        if not used:
            await clear_expression()
    except Exception as e:
        _get_logger().warning(f"go_idle 失败: {e}")


CONNECT_TIMEOUT = 1.0
SEND_TIMEOUT = 1.5
CONNECTION_POOL_MAX_AGE = 300  # 5分钟后重新建立连接

_RESOLVED_HOST = None


# ==========================================
# WebSocket 连接池实现
# ==========================================


from typing import Optional
import websockets

class WebSocketConnectionPool:
    """WebSocket 连接池：复用连接避免频繁创建/关闭，并串行化发送。"""
    def __init__(self):
        self._connection: Optional[websockets.WebSocketClientProtocol] = None
        self._lock = asyncio.Lock()
        self._send_lock = asyncio.Lock()  # ✅ 串行化 ws.send
        self._host: Optional[str] = None
        self._created_at: Optional[float] = None
        self._is_connected: bool = False
        self._last_ping_at: float = 0.0  # ✅ 可选：降低 ping 频率

    async def get_connection(self) -> websockets.WebSocketClientProtocol:
        async with self._lock:
            if await self._should_reconnect():
                await self._create_connection()
            return self._connection

    async def mark_broken(self) -> None:
        """标记连接不可用（带锁，避免竞态）"""
        async with self._lock:
            self._is_connected = False

    async def _should_reconnect(self) -> bool:
        if self._connection is None or not self._is_connected:
            return True

        loop = asyncio.get_running_loop()
        if self._created_at is not None:
            age = loop.time() - self._created_at
            if age > CONNECTION_POOL_MAX_AGE:
                _get_logger().info(f"连接池连接已使用 {age:.1f} 秒，重新建立连接")
                return True

        # ✅ 降低 ping 频率：最多每 5 秒 ping 一次（避免高频 get_connection 导致卡顿）
        now = loop.time()
        if now - self._last_ping_at < 5.0:
            return False
        self._last_ping_at = now

        try:
            await asyncio.wait_for(self._connection.ping(), timeout=1.0)
            return False
        except Exception as e:
            _get_logger().warning(f"连接健康检查失败: {e}，将重新连接")
            return True

    async def _create_connection(self) -> None:
        if self._connection is not None:
            try:
                await self._connection.close()
            except Exception as e:
                _get_logger().debug(f"关闭旧连接时出错: {e}")

        host = await _resolve_host()
        self._host = host

        self._connection = await _ws_connect(host)
        self._is_connected = True
        self._created_at = asyncio.get_running_loop().time()
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



# 全局连接池实例
_connection_pool = WebSocketConnectionPool()


async def _ws_connect(host: str):
    return await asyncio.wait_for(
        websockets.connect(host, ping_interval=None),
        timeout=CONNECT_TIMEOUT
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

    _get_logger().info("正在并发扫描端口 10086-10100 ...")

    ports = range(10086, 10101)
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
    无 ACK 模式下的稳定发送：
    - 同一连接 send 串行化（send_lock）
    - 短超时 + 小重试
    - 失败标记 broken，下一次自动重连
    """
    retry_count = 0
    while retry_count <= max_retries:
        try:
            ws = await _connection_pool.get_connection()

            # ✅ 串行化 send：避免并发写一个 ws
            async with _connection_pool._send_lock:
                for mid in LIVE2D_MODEL_IDS:
                    payload = {"msg": msg, "msgId": msg_id, "data": data_builder(mid)}
                    await asyncio.wait_for(ws.send(json.dumps(payload)), timeout=SEND_TIMEOUT)

            return  # 成功发送，结束

        except Exception as e:
            retry_count += 1
            await _connection_pool.mark_broken()

            if retry_count <= max_retries:
                _get_logger().warning(f"发送失败(尝试 {retry_count}/{max_retries}): {e}")
                await asyncio.sleep(0.1)
                continue

            _get_logger().error(f"发送失败，已达最大重试次数: {e}")
            raise



# ==========================================
# 核心控制函数
# ==========================================

async def play_motion(mtn: str, motion_type: int = 0):
    await _send_to_models(
        msg=13200,
        msg_id=2,
        data_builder=lambda mid: {"id": mid, "type": int(motion_type), "mtn": str(mtn)},
    )


async def set_expression(exp_id: int):
    await _send_to_models(
        msg=13300,
        msg_id=1,
        data_builder=lambda mid: {"id": mid, "expId": int(exp_id)},
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


async def play_sound_file(path: str, channel: int = 0, volume: float = 1.0, delay_ms: int = 0, loop: bool = False):
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
    global _CURRENT_COSTUME_CONFIG, _CURRENT_COSTUME_EMOTION_MAP
    _CURRENT_COSTUME_CONFIG = safe_cfg
    _CURRENT_COSTUME_EMOTION_MAP = safe_cfg.get("emotion_map", {}) if isinstance(safe_cfg.get("emotion_map", {}), dict) else {}

    _get_logger().info(f"切换服装: {abs_path} | Config: {safe_cfg}")

    await _send_to_models(
        msg=12000,
        msg_id=10,
        data_builder=lambda mid: {
            "id": mid,
            "path": abs_path,
            "config": safe_cfg
        },
    )


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
    cfg = resolve_emotion_config(emo, EMO_TO_LIVE2D)
    if not cfg:
        return False
    exp = cfg.get("exp", None)
    mtn = cfg.get("mtn", None)
    mtype = int(cfg.get("type", 0) or 0)
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
    **kwargs
):
    text = (text or "").strip()

    try:
        used = await trigger_emotion(emotion)
        if not used:
            await trigger_motion(text)
    except Exception as e:
        _get_logger().warning(f"动作/表情触发失败: {e}")

    min_ms = 3000 + len(text) * 200
    if duration_ms is None or duration_ms <= 0:
        duration_ms = min_ms
    else:
        duration_ms = max(int(duration_ms), int(min_ms))
    duration_ms += 80

    await _send_to_models(
        msg=11000,
        msg_id=3,
        data_builder=lambda mid: {"id": mid, "text": text, "duration": int(duration_ms)},
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
    
    _get_logger().info(f"[Live2D msg=13600] 发送口型同步数据: {len(lip_data)} 个时间点, 总时长 {duration:.2f}s")
    
    try:
        await _send_to_models(
            msg=13600,
            msg_id=7,
            data_builder=lambda mid: {
                "id": mid,
                "lipSync": lip_data,
                "duration": float(duration)
            },
        )
    except Exception as e:
        _get_logger().error(f"口型同步数据发送失败: {e}")

# ==========================================
