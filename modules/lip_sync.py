"""
Rhubarb 口型同步模块
使用 Rhubarb Lip Sync 工具从音频生成口型同步数据
"""
import asyncio
import json
import math
import os
import subprocess
import tempfile
from typing import Optional, List, Dict
from core.logger import get_logger
from config import RHUBARB_TIMEOUT_SEC

logger = get_logger()

try:
    import miniaudio
except Exception:
    miniaudio = None


class RhubarbLipSync:
    """Rhubarb 口型同步工具包装类"""
    
    def __init__(self, rhubarb_path: str = "./tools/rhubarb/rhubarb.exe", timeout_sec: Optional[float] = None):
        """
        初始化 Rhubarb 口型同步
        
        Args:
            rhubarb_path: Rhubarb 可执行文件路径
        """
        self.rhubarb_path = os.path.abspath(rhubarb_path)
        self.timeout_sec = float(timeout_sec) if timeout_sec is not None else float(RHUBARB_TIMEOUT_SEC)
        self._validate_installation()
        
        # 音素到嘴部参数的映射（可根据模型调整）
        self.phoneme_to_mouth = {
            'X': 0.0,      # 静音/闭口
            'AI': 0.5,     # 'a' 'i' 等开口音
            'E': 0.6,      # 'e' 音
            'O': 0.4,      # 'o' 音
            'U': 0.3,      # 'u' 音
            'MBP': 0.1,    # 'm' 'b' 'p' 闭口音
            'L': 0.25,     # 'l' 音
            'WQ': 0.15,    # 'w' 'q' 音
            'FV': 0.2,     # 'f' 'v' 音
            'etc': 0.1,    # 其他音
        }
        
        logger.info(f"RhubarbLipSync 初始化完成: {self.rhubarb_path}")
    
    def _validate_installation(self):
        """验证 Rhubarb 是否已正确安装"""
        if not os.path.exists(self.rhubarb_path):
            logger.warning(f"Rhubarb 未找到: {self.rhubarb_path}，口型同步功能将被禁用")
            return False
        
        logger.info(f"Rhubarb 路径确认: {self.rhubarb_path}")
        return True
    
    def is_available(self) -> bool:
        """检查 Rhubarb 是否可用"""
        return os.path.exists(self.rhubarb_path)
    
    async def analyze_audio(
        self,
        audio_path: str,
        output_json: Optional[str] = None
    ) -> List[Dict]:
        """
        分析音频文件，生成口型数据
        
        Args:
            audio_path: 音频文件路径
            output_json: 输出 JSON 文件路径（可选，不指定则使用临时文件）
        
        Returns:
            口型数据列表，格式：[{"time": 0.0, "mouth": 0.3}, ...]
            如果失败返回空列表
        """
        # 检查 Rhubarb 是否可用
        if not self.is_available():
            logger.debug("Rhubarb 不可用，跳过口型分析")
            return []
        
        audio_path = os.path.abspath(audio_path)
        
        if not os.path.exists(audio_path):
            logger.error(f"音频文件不存在: {audio_path}")
            return []
        
        # 如果没有指定输出路径，生成临时文件
        temp_file = False
        if output_json is None:
            fd, output_json = tempfile.mkstemp(suffix=".json", prefix="lip_")
            os.close(fd)
            temp_file = True
        
        output_json = os.path.abspath(output_json)
        
        # 构建 Rhubarb 命令
        cmd = [
            self.rhubarb_path,
            audio_path,
            "-o", output_json,
            "-f", "json",
        ]
        
        logger.debug(f"Rhubarb 命令: {' '.join(cmd)}")
        
        try:
            # 运行 Rhubarb（使用 asyncio 包装同步调用）
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=self.timeout_sec
            )
            
            if process.returncode != 0:
                logger.error(f"Rhubarb 执行失败 (返回码 {process.returncode}): {stderr.decode('utf-8', errors='ignore')}")
                if temp_file and os.path.exists(output_json):
                    try:
                        os.remove(output_json)
                    except Exception:
                        pass
                return []
            
            # 读取生成的 JSON
            try:
                with open(output_json, 'r', encoding='utf-8') as f:
                    data = json.load(f)
            except Exception as e:
                logger.error(f"读取 Rhubarb 输出失败: {e}")
                if temp_file and os.path.exists(output_json):
                    try:
                        os.remove(output_json)
                    except Exception:
                        pass
                return []
            
            # 转换为简化的口型数据
            lip_data = self._convert_to_lip_data(data)

            if not lip_data:
                duration = self._estimate_audio_duration(audio_path)
                fallback_data = self._build_fallback_lip_data(duration)
                if fallback_data:
                    logger.info(f"口型数据为空，已启用回退口型（时长 {duration:.2f}s, 点数 {len(fallback_data)}）")
                    lip_data = fallback_data
            
            logger.info(f"口型数据生成成功: {len(lip_data)} 个时间点，时长 {lip_data[-1]['time']:.2f}s" if lip_data else "口型数据为空")
            
            # 删除临时文件
            if temp_file and os.path.exists(output_json):
                try:
                    os.remove(output_json)
                except Exception:
                    pass
            
            return lip_data
            
        except asyncio.TimeoutError:
            logger.warning(f"Rhubarb 执行超时（{self.timeout_sec:.1f}秒）")
            if temp_file and os.path.exists(output_json):
                try:
                    os.remove(output_json)
                except Exception:
                    pass
            return []
        except Exception as e:
            logger.error(f"Rhubarb 分析失败: {e}", exc_info=True)
            if temp_file and os.path.exists(output_json):
                try:
                    os.remove(output_json)
                except Exception:
                    pass
            return []
    
    def _convert_to_lip_data(self, rhubarb_data: dict) -> List[Dict]:
        """
        将 Rhubarb 输出转换为简化的口型数据
        
        Args:
            rhubarb_data: Rhubarb 生成的 JSON 数据
        
        Returns:
            格式：[{"time": 0.0, "mouth": 0.3}, ...]
        """
        lip_data = []
        phonemes = rhubarb_data.get("phonemes", [])
        
        for phoneme in phonemes:
            time = phoneme.get("time", 0.0)
            phoneme_name = phoneme.get("value", "X")
            
            # 映射到嘴部参数值
            mouth_value = self.phoneme_to_mouth.get(phoneme_name, 0.1)
            
            lip_data.append({
                "time": float(time),
                "mouth": float(mouth_value)
            })
        
        return lip_data

    def _estimate_audio_duration(self, audio_path: str) -> float:
        """估算音频时长（秒），用于口型回退。"""
        try:
            if miniaudio is not None:
                decoded = miniaudio.decode_file(
                    audio_path,
                    output_format=miniaudio.SampleFormat.FLOAT32
                )
                dur = len(decoded.samples) / 4.0 / max(1, decoded.nchannels) / max(1, decoded.sample_rate)
                if dur > 0:
                    return float(dur)
        except Exception:
            pass

        try:
            import wave
            with wave.open(audio_path, "rb") as wf:
                frames = wf.getnframes()
                rate = wf.getframerate()
                if rate > 0:
                    return float(frames / rate)
        except Exception:
            pass

        try:
            size = os.path.getsize(audio_path)
            return max(0.8, min(40.0, size / 16000.0))
        except Exception:
            return 0.0

    def _build_fallback_lip_data(self, duration_sec: float) -> List[Dict]:
        """无音素时按时长生成平滑口型曲线。"""
        duration = float(duration_sec or 0.0)
        if duration <= 0:
            return []

        step = 0.12
        t = 0.0
        points = [{"time": 0.0, "mouth": 0.0}]
        i = 0
        while t < duration:
            mouth = 0.12 + 0.43 * abs(math.sin(i * 0.9))
            points.append({"time": round(t, 3), "mouth": float(mouth)})
            t += step
            i += 1
        points.append({"time": round(duration + 0.06, 3), "mouth": 0.0})
        return points
    
    def smooth_lip_data(self, lip_data: List[Dict], window_size: int = 3) -> List[Dict]:
        """
        平滑口型数据，避免嘴部动作过于突变
        
        Args:
            lip_data: 原始口型数据
            window_size: 平滑窗口大小（奇数）
        
        Returns:
            平滑后的口型数据
        """
        if not lip_data or len(lip_data) <= 1:
            return lip_data
        
        if window_size < 3:
            window_size = 3
        if window_size % 2 == 0:
            window_size += 1
        
        half_window = window_size // 2
        
        smoothed = []
        for i in range(len(lip_data)):
            start = max(0, i - half_window)
            end = min(len(lip_data), i + half_window + 1)
            
            # 计算窗口内的加权平均（中间权重更高）
            total_weight = 0
            weighted_sum = 0
            
            for j in range(start, end):
                # 距离越远权重越低
                distance = abs(j - i)
                weight = window_size - distance
                weighted_sum += lip_data[j]["mouth"] * weight
                total_weight += weight
            
            avg = weighted_sum / total_weight if total_weight > 0 else lip_data[i]["mouth"]
            
            smoothed.append({
                "time": lip_data[i]["time"],
                "mouth": avg
            })
        
        return smoothed
    
    def fade_lip_data(self, lip_data: List[Dict], fade_duration: float = 0.1) -> List[Dict]:
        """
        添加淡入淡出效果
        
        Args:
            lip_data: 原始口型数据
            fade_duration: 淡入淡出持续时间（秒）
        
        Returns:
            添加了淡入淡出的口型数据
        """
        if not lip_data:
            return lip_data
        
        result = lip_data.copy()
        
        # 淡入 - 在开始前添加闭口
        if result[0]["time"] > fade_duration:
            result.insert(0, {
                "time": result[0]["time"] - fade_duration,
                "mouth": 0.0
            })
        
        # 淡出 - 在结束后添加闭口
        result.append({
            "time": result[-1]["time"] + fade_duration,
            "mouth": 0.0
        })
        
        return result
