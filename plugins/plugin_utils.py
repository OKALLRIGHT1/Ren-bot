"""
插件通用工具模块
提供统一的错误处理装饰器和辅助函数
"""
import asyncio
import functools
import time
from typing import Callable, Any, Optional
from core.logger import get_logger

# 不在导入时获取logger，而是在使用时获取
# logger = get_logger()

def _get_logger():
    """安全获取logger，如果未初始化则返回None"""
    try:
        return get_logger()
    except:
        return None


def handle_plugin_errors(plugin_name: str, log_errors: bool = True):
    """
    插件错误处理装饰器
    
    Args:
        plugin_name: 插件名称，用于日志记录
        log_errors: 是否记录错误日志（默认 True）
    
    使用示例:
        @handle_plugin_errors("音乐播放器")
        async def run(self, args, ctx):
            # 插件逻辑
            pass
    """
    def decorator(func: Callable):
        @functools.wraps(func)
        async def async_wrapper(self, *args, **kwargs):
            start_time = time.time()
            print(f"🔧 [插件] [{plugin_name}] 开始执行，参数: {args}")
            try:
                result = await func(self, *args, **kwargs)
                
                # 记录执行时间（仅当超过阈值时）
                elapsed = time.time() - start_time
                log = _get_logger()
                
                print(f"🔧 [插件] [{plugin_name}] ✅ 执行成功，耗时: {elapsed:.2f}秒")
                print(f"🔧 [插件] [{plugin_name}] 结果: {str(result)[:200]}..." if len(str(result)) > 200 else f"🔧 [插件] [{plugin_name}] 结果: {result}")
                
                if log:
                    if elapsed > 1.0:
                        log.warning(f"[{plugin_name}] 执行耗时: {elapsed:.2f}秒")
                    elif log_errors:
                        log.debug(f"[{plugin_name}] 执行成功，耗时: {elapsed:.2f}秒")
                
                return result
                
            except ValueError as e:
                # 参数错误
                print(f"🔧 [插件] [{plugin_name}] ❌ 参数错误: {e}")
                log = _get_logger()
                if log_errors and log:
                    log.warning(f"[{plugin_name}] 参数错误: {e}")
                return f"❌ 参数错误: {e}"
                
            except (FileNotFoundError, PermissionError) as e:
                # 文件/权限错误
                print(f"🔧 [插件] [{plugin_name}] ❌ 文件/权限错误: {e}")
                log = _get_logger()
                if log_errors and log:
                    log.error(f"[{plugin_name}] 文件/权限错误: {e}")
                return f"❌ 文件或权限错误: {e}"
                
            except ConnectionError as e:
                # 网络连接错误
                print(f"🔧 [插件] [{plugin_name}] ❌ 网络连接错误: {e}")
                log = _get_logger()
                if log_errors and log:
                    log.error(f"[{plugin_name}] 网络连接错误: {e}")
                return f"❌ 网络连接失败: {e}"
                
            except asyncio.TimeoutError as e:
                # 超时错误
                elapsed = time.time() - start_time
                print(f"🔧 [插件] [{plugin_name}] ❌ 操作超时（{elapsed:.2f}秒）")
                log = _get_logger()
                if log_errors and log:
                    log.error(f"[{plugin_name}] 操作超时: {e}")
                return f"❌ 操作超时，请稍后重试"
                
            except Exception as e:
                # 其他未捕获的异常
                elapsed = time.time() - start_time
                print(f"🔧 [插件] [{plugin_name}] ❌ 未捕获异常: {type(e).__name__}: {e}")
                print(f"🔧 [插件] [{plugin_name}] 执行时间: {elapsed:.2f}秒")
                import traceback
                traceback.print_exc()
                
                log = _get_logger()
                if log_errors and log:
                    log.error(f"[{plugin_name}] 未捕获异常: {type(e).__name__}: {e}", exc_info=True)
                return f"⚠️ {plugin_name}执行出错: {e}"
        
        # 同步函数的包装器
        @functools.wraps(func)
        def sync_wrapper(self, *args, **kwargs):
            start_time = time.time()
            print(f"🔧 [插件] [{plugin_name}] 开始执行（同步），参数: {args}")
            try:
                result = func(self, *args, **kwargs)
                
                elapsed = time.time() - start_time
                log = _get_logger()
                
                print(f"🔧 [插件] [{plugin_name}] ✅ 执行成功，耗时: {elapsed:.2f}秒")
                print(f"🔧 [插件] [{plugin_name}] 结果: {str(result)[:200]}..." if len(str(result)) > 200 else f"🔧 [插件] [{plugin_name}] 结果: {result}")
                
                if log:
                    if elapsed > 1.0:
                        log.warning(f"[{plugin_name}] 执行耗时: {elapsed:.2f}秒")
                    elif log_errors:
                        log.debug(f"[{plugin_name}] 执行成功，耗时: {elapsed:.2f}秒")
                
                return result
                
            except ValueError as e:
                print(f"🔧 [插件] [{plugin_name}] ❌ 参数错误: {e}")
                log = _get_logger()
                if log_errors and log:
                    log.warning(f"[{plugin_name}] 参数错误: {e}")
                return f"❌ 参数错误: {e}"
                
            except (FileNotFoundError, PermissionError) as e:
                print(f"🔧 [插件] [{plugin_name}] ❌ 文件/权限错误: {e}")
                log = _get_logger()
                if log_errors and log:
                    log.error(f"[{plugin_name}] 文件/权限错误: {e}")
                return f"❌ 文件或权限错误: {e}"
                
            except ConnectionError as e:
                print(f"🔧 [插件] [{plugin_name}] ❌ 网络连接错误: {e}")
                log = _get_logger()
                if log_errors and log:
                    log.error(f"[{plugin_name}] 网络连接错误: {e}")
                return f"❌ 网络连接失败: {e}"
                
            except Exception as e:
                elapsed = time.time() - start_time
                print(f"🔧 [插件] [{plugin_name}] ❌ 未捕获异常: {type(e).__name__}: {e}")
                print(f"🔧 [插件] [{plugin_name}] 执行时间: {elapsed:.2f}秒")
                import traceback
                traceback.print_exc()
                
                log = _get_logger()
                if log_errors and log:
                    log.error(f"[{plugin_name}] 未捕获异常: {type(e).__name__}: {e}", exc_info=True)
                return f"⚠️ {plugin_name}执行出错: {e}"
        
        # 根据函数是否是协程函数返回对应的包装器
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper
    
    return decorator


def async_io_operation(func: Callable) -> Callable:
    """
    异步 I/O 操作装饰器
    自动将同步 I/O 操作放入线程池执行
    
    使用示例:
        @async_io_operation
        def scan_files(self, dirs):
            # 同步文件扫描
            return files_list
    """
    @functools.wraps(func)
    async def wrapper(self, *args, **kwargs):
        try:
            return await asyncio.to_thread(func, self, *args, **kwargs)
        except Exception as e:
            logger.error(f"异步I/O操作失败: {func.__name__}: {e}")
            raise
    return wrapper


def safe_get_context(ctx: dict, key: str, default: Any = None) -> Any:
    """
    安全地从上下文字典中获取值
    
    Args:
        ctx: 上下文字典
        key: 键名
        default: 默认值
    
    Returns:
        值或默认值
    """
    if ctx is None:
        return default
    return ctx.get(key, default)


class PluginPerformanceMonitor:
    """
    插件性能监控器
    跟踪插件的执行次数、成功/失败率、平均执行时间
    """
    
    def __init__(self):
        self.stats = {}
    
    def record(self, plugin_name: str, success: bool, duration: float):
        """
        记录插件执行数据
        
        Args:
            plugin_name: 插件名称
            success: 是否成功
            duration: 执行时长（秒）
        """
        if plugin_name not in self.stats:
            self.stats[plugin_name] = {
                'total': 0,
                'success': 0,
                'failed': 0,
                'total_duration': 0.0,
                'max_duration': 0.0
            }
        
        stats = self.stats[plugin_name]
        stats['total'] += 1
        stats['total_duration'] += duration
        stats['max_duration'] = max(stats['max_duration'], duration)
        
        if success:
            stats['success'] += 1
        else:
            stats['failed'] += 1
    
    def get_stats(self, plugin_name: str) -> Optional[dict]:
        """
        获取指定插件的统计信息
        
        Args:
            plugin_name: 插件名称
        
        Returns:
            统计信息字典或 None
        """
        if plugin_name not in self.stats:
            return None
        
        stats = self.stats[plugin_name]
        avg_duration = stats['total_duration'] / stats['total'] if stats['total'] > 0 else 0
        success_rate = (stats['success'] / stats['total'] * 100) if stats['total'] > 0 else 0
        
        return {
            'total': stats['total'],
            'success': stats['success'],
            'failed': stats['failed'],
            'success_rate': success_rate,
            'avg_duration': avg_duration,
            'max_duration': stats['max_duration']
        }
    
    def get_all_stats(self) -> dict:
        """获取所有插件的统计信息"""
        return {
            name: self.get_stats(name)
            for name in self.stats.keys()
        }


# 全局性能监控器实例
performance_monitor = PluginPerformanceMonitor()
