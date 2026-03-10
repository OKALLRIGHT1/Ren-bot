"""
系统硬件监控插件
监控系统资源使用情况，在占用过高或温度过高时主动提醒

功能：
- CPU使用率监控
- 内存使用情况监控
- 磁盘使用情况监控
- GPU使用率监控（需要nvidia-ml-py3）
- CPU/GPU温度监控
- 后台定期检查并提醒
"""
import os
import asyncio
import platform
import json
import ctypes
from typing import Dict, List, Optional

from core.logger import get_logger
from plugins.plugin_utils import handle_plugin_errors

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

try:
    import pynvml
    HAS_NVIDIA = True
except ImportError:
    HAS_NVIDIA = False

try:
    import screen_brightness_control as sbc
    HAS_BRIGHTNESS = True
except ImportError:
    HAS_BRIGHTNESS = False

try:
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
    from comtypes import CLSCTX_ALL
    from ctypes import cast, POINTER
    HAS_AUDIO_CONTROL = True
except ImportError:
    HAS_AUDIO_CONTROL = False

logger = get_logger()


class Plugin:
    def __init__(self):
        self._running = False
        self._check_interval = 60
        self._check_task = None
        self._callback = None
        
        # 阈值配置（从配置文件读取）
        self.cpu_threshold = 80
        self.memory_threshold = 85
        self.disk_threshold = 90
        self.cpu_temp_threshold = 75
        self.gpu_threshold = 85
        self.gpu_temp_threshold = 80
        
        # 配置文件路径
        self.config_path = os.path.join(os.path.dirname(__file__), "config.json")
        
        # 上次告警状态，避免重复提醒
        self._last_alerts = {}
        
        # 从配置文件加载配置
        self._load_config()

    def _load_config(self):
        """从配置文件加载阈值设置"""
        try:
            if os.path.exists(self.config_path):
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    
                settings = config.get("settings", {})
                
                # 读取阈值设置
                self.cpu_threshold = settings.get("cpu_threshold", {}).get("default", 80)
                self.memory_threshold = settings.get("memory_threshold", {}).get("default", 85)
                self.disk_threshold = settings.get("disk_threshold", {}).get("default", 90)
                self.cpu_temp_threshold = settings.get("cpu_temp_threshold", {}).get("default", 75)
                self.gpu_threshold = settings.get("gpu_threshold", {}).get("default", 85)
                self.gpu_temp_threshold = settings.get("gpu_temp_threshold", {}).get("default", 80)
                self._check_interval = settings.get("check_interval", {}).get("default", 60)
                
                logger.info(f"已加载系统监控配置：CPU阈值={self.cpu_threshold}%, 内存阈值={self.memory_threshold}%, "
                          f"磁盘阈值={self.disk_threshold}%, CPU温度阈值={self.cpu_temp_threshold}℃, "
                          f"GPU阈值={self.gpu_threshold}%, GPU温度阈值={self.gpu_temp_threshold}℃")
        except Exception as e:
            logger.error(f"加载系统监控配置失败: {e}，使用默认值")
    
    def reload_config(self):
        """重新加载配置（当GUI修改配置后调用）"""
        logger.info("重新加载系统监控配置...")
        self._load_config()
    
    def _get_logger(self):
        """安全获取logger"""
        try:
            return get_logger()
        except:
            return None

    @handle_plugin_errors("系统监控")
    async def run(self, args, ctx):
        if not HAS_PSUTIL:
            return "⚠️ 需要安装 psutil 库：pip install psutil"

        raw_args = (args or "").strip()
        if not raw_args:
            return await self._check_all()

        if "|||" in raw_args:
            action_key, action_param = raw_args.split("|||", 1)
        else:
            parts = raw_args.split(maxsplit=1)
            action_key = parts[0] if parts else ""
            action_param = parts[1] if len(parts) > 1 else ""

        args = (action_key or "").strip().lower()
        action_param = (action_param or "").strip()

        action_map = {
            "check": self._check_all,
            "all": self._check_all,
            "cpu": self._check_cpu,
            "memory": self._check_memory,
            "disk": self._check_disk,
            "temp": self._check_temperature,
            "temperature": self._check_temperature,
            "gpu": self._check_gpu,
            "start": self._start_monitoring,
            "stop": self._stop_monitoring,
            "status": self._get_status,
            "set_volume": lambda: self._set_volume_command(action_param),
            "mute": self._mute_command,
            "set_brightness": lambda: self._set_brightness_command(action_param),
            "lock_screen": self._lock_screen_command,
        }

        action = action_map.get(args, self._check_all)
        return await action()

    def _get_volume_interface(self):
        devices = AudioUtilities.GetSpeakers()
        interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        return cast(interface, POINTER(IAudioEndpointVolume))

    def _set_volume(self, val_str: str):
        if not HAS_AUDIO_CONTROL:
            return None
        try:
            volume = self._get_volume_interface()
            current_vol = volume.GetMasterVolumeLevelScalar() * 100
            val_str = (val_str or "").replace("%", "").strip()
            if not val_str:
                return None
            if val_str.startswith("+") or val_str.startswith("-"):
                target_vol = current_vol + int(val_str)
            else:
                target_vol = int(val_str)
            target_vol = max(0.0, min(100.0, target_vol))
            volume.SetMasterVolumeLevelScalar(target_vol / 100.0, None)
            return int(target_vol)
        except Exception as e:
            logger.error(f"音量设置失败: {e}")
            return None

    def _toggle_mute(self):
        if not HAS_AUDIO_CONTROL:
            return None
        try:
            volume = self._get_volume_interface()
            current = volume.GetMute()
            volume.SetMute(not current, None)
            return not current
        except Exception as e:
            logger.error(f"静音切换失败: {e}")
            return None

    async def _set_volume_command(self, value: str) -> str:
        if not HAS_AUDIO_CONTROL:
            return "⚠️ 当前环境缺少音量控制依赖（pycaw/comtypes）"
        if not value:
            return "❌ 未提供音量数值"
        final_vol = self._set_volume(value)
        if final_vol is None:
            return "❌ 音量调整失败"
        return f"✅ 音量已调整至 {final_vol}%"

    async def _mute_command(self) -> str:
        if not HAS_AUDIO_CONTROL:
            return "⚠️ 当前环境缺少音量控制依赖（pycaw/comtypes）"
        is_muted = self._toggle_mute()
        if is_muted is None:
            return "❌ 静音切换失败"
        return f"✅ 已{'静音' if is_muted else '取消静音'}"

    async def _set_brightness_command(self, value: str) -> str:
        if not HAS_BRIGHTNESS:
            return "⚠️ 当前环境缺少亮度控制依赖（screen_brightness_control）"
        try:
            val = (value or "").replace("%", "").strip()
            if not val:
                return "❌ 未提供亮度数值"
            current_bright = sbc.get_brightness()[0]
            if val.startswith("+") or val.startswith("-"):
                target_bright = current_bright + int(val)
            else:
                target_bright = int(val)
            target_bright = max(0, min(100, target_bright))
            sbc.set_brightness(target_bright)
            return f"✅ 屏幕亮度已调整为 {target_bright}%"
        except Exception as e:
            return f"❌ 亮度调整失败: {e}"

    async def _lock_screen_command(self) -> str:
        try:
            ctypes.windll.user32.LockWorkStation()
            return "✅ 已锁定屏幕。"
        except Exception as e:
            return f"❌ 锁屏失败: {e}"

    async def _check_all(self) -> str:
        """检查所有硬件状态"""
        result_lines = []
        result_lines.append("## 💻 系统硬件状态")
        result_lines.append("")
        
        # CPU
        cpu_info = await self._get_cpu_info()
        result_lines.append("### CPU")
        result_lines.append(f"使用率: {cpu_info['percent']}%")
        result_lines.append(f"核心数: {cpu_info['cores']}")
        if cpu_info['temperature'] is not None:
            result_lines.append(f"温度: {cpu_info['temperature']}℃")
        result_lines.append("")
        
        # 内存
        memory_info = await self._get_memory_info()
        result_lines.append("### 内存")
        result_lines.append(f"使用: {memory_info['used_gb']:.2f}GB / {memory_info['total_gb']:.2f}GB")
        result_lines.append(f"使用率: {memory_info['percent']}%")
        result_lines.append("")
        
        # 磁盘
        disk_info = await self._get_disk_info()
        result_lines.append("### 磁盘")
        for disk in disk_info:
            result_lines.append(f"{disk['mount']}: {disk['used_gb']:.1f}GB / {disk['total_gb']:.1f}GB ({disk['percent']}%)")
        result_lines.append("")
        
        # GPU
        if HAS_NVIDIA:
            gpu_info = await self._get_gpu_info()
            result_lines.append("### GPU")
            for i, gpu in enumerate(gpu_info):
                result_lines.append(f"GPU {i}: {gpu['name']}")
                result_lines.append(f"  使用率: {gpu['percent']}%")
                result_lines.append(f"  显存: {gpu['memory_used_mb']:.0f}MB / {gpu['memory_total_mb']:.0f}MB")
                if gpu['temperature'] is not None:
                    result_lines.append(f"  温度: {gpu['temperature']}℃")
        
        # 整体评估
        result_lines.append("")
        result_lines.append("---")
        overall_status = await self._get_overall_status()
        result_lines.append(f"### 整体状态: {overall_status['status']}")
        result_lines.append(overall_status['message'])
        
        return "\n".join(result_lines)

    async def _check_cpu(self) -> str:
        """检查CPU状态"""
        cpu_info = await self._get_cpu_info()
        
        result_lines = []
        result_lines.append("## 🖥️ CPU状态")
        result_lines.append("")
        result_lines.append(f"**使用率**: {cpu_info['percent']}%")
        result_lines.append(f"**核心数**: {cpu_info['cores']}")
        result_lines.append(f"**频率**: {cpu_info['freq_mhz']:.0f} MHz")
        
        if cpu_info['temperature'] is not None:
            temp_status = "正常"
            if cpu_info['temperature'] >= self.cpu_temp_threshold:
                temp_status = "⚠️ 过高"
            elif cpu_info['temperature'] >= self.cpu_temp_threshold - 10:
                temp_status = "⚡ 偏高"
            result_lines.append(f"**温度**: {cpu_info['temperature']}℃ ({temp_status})")
        
        result_lines.append("")
        
        # 使用率警告
        if cpu_info['percent'] >= self.cpu_threshold:
            result_lines.append(f"⚠️ CPU使用率超过阈值（{self.cpu_threshold}%）！")
            result_lines.append("建议：[CMD: search | 查看占用CPU的进程]")
        elif cpu_info['percent'] >= self.cpu_threshold - 10:
            result_lines.append(f"⚡ CPU使用率较高（{self.cpu_threshold-10}%-{self.cpu_threshold}%）")
        
        # 热量警告
        if cpu_info['temperature'] is not None and cpu_info['temperature'] >= self.cpu_temp_threshold:
            result_lines.append(f"⚠️ CPU温度过高（{cpu_info['temperature']}℃）！")
            result_lines.append("建议：检查散热、关闭高负载程序")
        
        return "\n".join(result_lines)

    async def _check_memory(self) -> str:
        """检查内存状态"""
        memory_info = await self._get_memory_info()
        
        result_lines = []
        result_lines.append("## 💾 内存状态")
        result_lines.append("")
        result_lines.append(f"**已使用**: {memory_info['used_gb']:.2f}GB")
        result_lines.append(f"**总计**: {memory_info['total_gb']:.2f}GB")
        result_lines.append(f"**使用率**: {memory_info['percent']}%")
        result_lines.append("")
        
        # 使用率警告
        if memory_info['percent'] >= self.memory_threshold:
            result_lines.append(f"⚠️ 内存使用率超过阈值（{self.memory_threshold}%）！")
            result_lines.append("建议：关闭一些不必要的程序释放内存")
        elif memory_info['percent'] >= self.memory_threshold - 10:
            result_lines.append(f"⚡ 内存使用率较高（{self.memory_threshold-10}%-{self.memory_threshold}%）")
        
        return "\n".join(result_lines)

    async def _check_disk(self) -> str:
        """检查磁盘状态"""
        disk_info = await self._get_disk_info()
        
        result_lines = []
        result_lines.append("## 💿 磁盘状态")
        result_lines.append("")
        
        has_warning = False
        for disk in disk_info:
            result_lines.append(f"**{disk['mount']}**")
            result_lines.append(f"  已用: {disk['used_gb']:.1f}GB / 总计: {disk['total_gb']:.1f}GB")
            result_lines.append(f"  使用率: {disk['percent']}%")
            
            if disk['percent'] >= self.disk_threshold:
                has_warning = True
                result_lines.append(f"  ⚠️ 磁盘空间不足！")
            elif disk['percent'] >= self.disk_threshold - 10:
                result_lines.append(f"  ⚡ 磁盘空间较紧张")
            result_lines.append("")
        
        if has_warning:
            result_lines.append("💡 建议：清理临时文件、卸载不用的程序")
        
        return "\n".join(result_lines)

    async def _check_temperature(self) -> str:
        """检查温度状态"""
        result_lines = []
        result_lines.append("## 🌡️ 温度状态")
        result_lines.append("")
        
        # CPU温度
        cpu_temp = self._get_cpu_temperature()
        if cpu_temp is not None:
            status = "正常"
            if cpu_temp >= self.cpu_temp_threshold:
                status = "⚠️ 过高"
            elif cpu_temp >= self.cpu_temp_threshold - 10:
                status = "⚡ 偏高"
            result_lines.append(f"**CPU温度**: {cpu_temp}℃ ({status})")
        else:
            result_lines.append("**CPU温度**: 不可用")
        
        result_lines.append("")
        
        # GPU温度
        if HAS_NVIDIA:
            try:
                pynvml.nvmlInit()
                device_count = pynvml.nvmlDeviceGetCount()
                
                for i in range(device_count):
                    handle = pynvml.nvmlDeviceGetHandleByIndex(i)
                    temp = pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)
                    
                    status = "正常"
                    if temp >= self.gpu_temp_threshold:
                        status = "⚠️ 过高"
                    elif temp >= self.gpu_temp_threshold - 10:
                        status = "⚡ 偏高"
                    
                    name = pynvml.nvmlDeviceGetName(handle).decode('utf-8')
                    result_lines.append(f"**GPU {i} ({name})**: {temp}℃ ({status})")
                
                pynvml.nvmlShutdown()
            except Exception as e:
                log = self._get_logger()
                if log:
                    log.error(f"获取GPU温度失败: {e}")
                result_lines.append("**GPU温度**: 不可用")
        
        result_lines.append("")
        
        # 建议
        if cpu_temp is not None and cpu_temp >= self.cpu_temp_threshold:
            result_lines.append("⚠️ CPU温度过高，建议检查散热或降低负载")
        
        return "\n".join(result_lines)

    async def _check_gpu(self) -> str:
        """检查GPU状态"""
        if not HAS_NVIDIA:
            return "⚠️ 需要安装 nvidia-ml-py3 库：pip install nvidia-ml-py3"
        
        gpu_info = await self._get_gpu_info()
        
        result_lines = []
        result_lines.append("## 🎮 GPU状态")
        result_lines.append("")
        
        if not gpu_info:
            result_lines.append("未检测到NVIDIA GPU")
            return "\n".join(result_lines)
        
        for i, gpu in enumerate(gpu_info):
            result_lines.append(f"### GPU {i}")
            result_lines.append(f"**型号**: {gpu['name']}")
            result_lines.append(f"**使用率**: {gpu['percent']}%")
            result_lines.append(f"**显存**: {gpu['memory_used_mb']:.0f}MB / {gpu['memory_total_mb']:.0f}MB")
            if gpu['temperature'] is not None:
                result_lines.append(f"**温度**: {gpu['temperature']}℃")
            result_lines.append("")
            
            # 警告
            if gpu['percent'] >= self.gpu_threshold:
                result_lines.append(f"⚠️ GPU使用率超过阈值（{self.gpu_threshold}%）！")
            if gpu['temperature'] is not None and gpu['temperature'] >= self.gpu_temp_threshold:
                result_lines.append(f"⚠️ GPU温度过高（{gpu['temperature']}℃）！")
        
        return "\n".join(result_lines)

    async def _start_monitoring(self) -> str:
        """启动后台监控"""
        if self._running:
            return "✅ 后台监控已在运行中"
        
        self._running = True
        self._check_task = asyncio.create_task(self._monitor_loop())
        
        log = self._get_logger()
        if log:
            log.info(f"系统监控已启动，检查间隔：{self._check_interval}秒")
        
        return f"✅ 已启动后台监控\n每{self._check_interval}秒检查一次，超过阈值时会提醒你……对"

    async def _stop_monitoring(self) -> str:
        """停止后台监控"""
        if not self._running:
            return "后台监控未运行"
        
        self._running = False
        if self._check_task:
            self._check_task.cancel()
            try:
                await self._check_task
            except asyncio.CancelledError:
                pass
        
        log = self._get_logger()
        if log:
            log.info("系统监控已停止")
        
        return "已停止后台监控……对"

    async def _get_status(self) -> str:
        """获取监控状态"""
        status = "运行中" if self._running else "未运行"
        return f"后台监控状态：{status}\n检查间隔：{self._check_interval}秒"

    def set_callback(self, callback):
        """设置提醒回调函数"""
        self._callback = callback

    async def _monitor_loop(self):
        """后台监控循环"""
        while self._running:
            try:
                await asyncio.sleep(self._check_interval)
                
                if not self._running:
                    break
                
                # 检查各项指标
                alerts = await self._check_thresholds()
                
                # 发送提醒
                for alert in alerts:
                    # 避免重复提醒（同类型告警间隔至少5分钟）
                    alert_key = f"{alert['type']}"
                    last_time = self._last_alerts.get(alert_key, 0)
                    
                    import time
                    current_time = time.time()
                    if current_time - last_time >= 300:  # 5分钟
                        self._last_alerts[alert_key] = current_time
                        
                        message = f"⚠️ {alert['message']}\n{alert['suggestion']}"
                        
                        # 通过回调发送提醒
                        if self._callback:
                            self._callback(message)
                        
                        # 记录日志
                        log = self._get_logger()
                        if log:
                            log.warning(f"硬件监控提醒: {alert['message']}")
            
            except asyncio.CancelledError:
                break
            except Exception as e:
                log = self._get_logger()
                if log:
                    log.error(f"监控循环错误: {e}", exc_info=True)

    async def _check_thresholds(self) -> List[Dict]:
        """检查各项指标是否超过阈值"""
        alerts = []
        
        # 检查CPU
        cpu_info = await self._get_cpu_info()
        if cpu_info['percent'] >= self.cpu_threshold:
            alerts.append({
                'type': 'cpu_usage',
                'message': f"CPU使用率达到{cpu_info['percent']}%",
                'suggestion': f"建议查看占用CPU的进程……对"
            })
        
        if cpu_info['temperature'] is not None and cpu_info['temperature'] >= self.cpu_temp_threshold:
            alerts.append({
                'type': 'cpu_temp',
                'message': f"CPU温度达到{cpu_info['temperature']}℃",
                'suggestion': f"可能需要检查散热或降低负载……对"
            })
        
        # 检查内存
        memory_info = await self._get_memory_info()
        if memory_info['percent'] >= self.memory_threshold:
            alerts.append({
                'type': 'memory',
                'message': f"内存使用率达到{memory_info['percent']}%",
                'suggestion': f"建议关闭一些不必要的程序……对"
            })
        
        # 检查GPU
        if HAS_NVIDIA:
            gpu_info = await self._get_gpu_info()
            for gpu in gpu_info:
                if gpu['percent'] >= self.gpu_threshold:
                    alerts.append({
                        'type': f'gpu_{gpu["index"]}_usage',
                        'message': f"GPU使用率达到{gpu['percent']}%",
                        'suggestion': f"可能有程序在大量使用GPU……对"
                    })
                
                if gpu['temperature'] is not None and gpu['temperature'] >= self.gpu_temp_threshold:
                    alerts.append({
                        'type': f'gpu_{gpu["index"]}_temp',
                        'message': f"GPU温度达到{gpu['temperature']}℃",
                        'suggestion': f"建议检查散热或降低GPU负载……对"
                    })
        
        return alerts

    async def _get_cpu_info(self) -> Dict:
        """获取CPU信息"""
        cpu_percent = psutil.cpu_percent(interval=1)
        cpu_cores = psutil.cpu_count(logical=False)
        cpu_freq = psutil.cpu_freq().current if psutil.cpu_freq() else 0
        
        return {
            'percent': cpu_percent,
            'cores': cpu_cores,
            'freq_mhz': cpu_freq,
            'temperature': self._get_cpu_temperature()
        }

    async def _get_memory_info(self) -> Dict:
        """获取内存信息"""
        memory = psutil.virtual_memory()
        
        return {
            'total_gb': memory.total / (1024**3),
            'used_gb': memory.used / (1024**3),
            'available_gb': memory.available / (1024**3),
            'percent': memory.percent
        }

    async def _get_disk_info(self) -> List[Dict]:
        """获取磁盘信息"""
        disks = []
        
        # Windows: 获取所有逻辑驱动器
        if platform.system() == 'Windows':
            partitions = psutil.disk_partitions()
            for partition in partitions:
                try:
                    usage = psutil.disk_usage(partition.mountpoint)
                    disks.append({
                        'mount': partition.mountpoint,
                        'total_gb': usage.total / (1024**3),
                        'used_gb': usage.used / (1024**3),
                        'free_gb': usage.free / (1024**3),
                        'percent': usage.percent
                    })
                except:
                    pass
        else:
            # Linux/Mac: 获取根目录
            try:
                usage = psutil.disk_usage('/')
                disks.append({
                    'mount': '/',
                    'total_gb': usage.total / (1024**3),
                    'used_gb': usage.used / (1024**3),
                    'free_gb': usage.free / (1024**3),
                    'percent': usage.percent
                })
            except:
                pass
        
        return disks

    async def _get_gpu_info(self) -> List[Dict]:
        """获取GPU信息"""
        if not HAS_NVIDIA:
            return []
        
        gpu_info = []
        
        try:
            pynvml.nvmlInit()
            device_count = pynvml.nvmlDeviceGetCount()
            
            for i in range(device_count):
                handle = pynvml.nvmlDeviceGetHandleByIndex(i)
                
                # GPU使用率
                utilization = pynvml.nvmlDeviceGetUtilizationRates(handle)
                gpu_percent = utilization.gpu
                
                # 显存信息
                memory_info = pynvml.nvmlDeviceGetMemoryInfo(handle)
                memory_total_mb = memory_info.total / (1024**2)
                memory_used_mb = memory_info.used / (1024**2)
                
                # 温度
                try:
                    temperature = pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)
                except:
                    temperature = None
                
                # 名称
                name = pynvml.nvmlDeviceGetName(handle).decode('utf-8')
                
                gpu_info.append({
                    'index': i,
                    'name': name,
                    'percent': gpu_percent,
                    'memory_total_mb': memory_total_mb,
                    'memory_used_mb': memory_used_mb,
                    'temperature': temperature
                })
            
            pynvml.nvmlShutdown()
        except Exception as e:
            log = self._get_logger()
            if log:
                log.error(f"获取GPU信息失败: {e}")
        
        return gpu_info

    def _get_cpu_temperature(self) -> Optional[float]:
        """获取CPU温度"""
        try:
            if hasattr(psutil, "sensors_temperatures"):
                temps = psutil.sensors_temperatures()
                if temps:
                    # 尝试获取不同平台的温度传感器
                    if 'coretemp' in temps:  # Linux
                        return temps['coretemp'][0].current
                    elif 'cpu_thermal' in temps:  # Android/Raspberry Pi
                        return temps['cpu_thermal'][0].current
                    elif 'cpu' in temps:  # 某些系统
                        return temps['cpu'][0].current
                    elif 'acpitz' in temps:  # 某些系统
                        return temps['acpitz'][0].current
                    else:
                        # 尝试第一个可用的温度传感器
                        for sensor_name, sensor_list in temps.items():
                            if sensor_list:
                                return sensor_list[0].current
            return None
        except:
            return None

    async def _get_overall_status(self) -> Dict:
        """获取整体状态评估"""
        cpu_info = await self._get_cpu_info()
        memory_info = await self._get_memory_info()
        disk_info = await self._get_disk_info()
        
        issues = []
        
        if cpu_info['percent'] >= self.cpu_threshold:
            issues.append(f"CPU使用率{cpu_info['percent']}%")
        if cpu_info['temperature'] and cpu_info['temperature'] >= self.cpu_temp_threshold:
            issues.append(f"CPU温度{cpu_info['temperature']}℃")
        if memory_info['percent'] >= self.memory_threshold:
            issues.append(f"内存使用率{memory_info['percent']}%")
        
        for disk in disk_info:
            if disk['percent'] >= self.disk_threshold:
                issues.append(f"{disk['mount']}磁盘{disk['percent']}%")
        
        if issues:
            return {
                'status': '⚠️ 需要关注',
                'message': f"发现以下问题：{', '.join(issues)}\n建议及时处理……对"
            }
        else:
            return {
                'status': '✅ 正常',
                'message': '所有硬件指标都在正常范围内……对'
            }
