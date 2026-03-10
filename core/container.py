"""
依赖注入容器
用于管理服务生命周期和依赖关系
"""
import asyncio
from typing import Any, Dict, Callable, Optional


class ServiceContainer:
    """简单的依赖注入容器"""
    
    def __init__(self):
        self._factories: Dict[str, Callable] = {}
        self._singletons: Dict[str, Any] = {}
        self._transients: Dict[str, Any] = {}
        self._lock = asyncio.Lock()

    # 在 ServiceContainer 类中添加以下方法

    async def dispose(self):
        """清理所有资源"""
        # 清理单例实例
        for name, instance in list(self._singletons.items()):
            try:
                if hasattr(instance, 'dispose') and callable(instance.dispose):
                    if asyncio.iscoroutinefunction(instance.dispose):
                        await instance.dispose()
                    else:
                        instance.dispose()
                elif hasattr(instance, 'close') and callable(instance.close):
                    if asyncio.iscoroutinefunction(instance.close):
                        await instance.close()
                    else:
                        instance.close()
            except Exception as e:
                print(f"⚠️ [Container] 清理 {name} 失败: {e}")

        self._singletons.clear()
        self._transients.clear()
        print("✅ [Container] 资源已清理")

    def register(self, name: str, factory: Callable, singleton: bool = True):
        """
        注册服务
        
        Args:
            name: 服务名称
            factory: 服务工厂函数
            singleton: 是否为单例
        """
        self._factories[name] = (factory, singleton)
    
    def get(self, name: str) -> Any:
        """
        获取服务实例
        
        Args:
            name: 服务名称
            
        Returns:
            服务实例
        """
        # 检查单例缓存
        if name in self._singletons:
            return self._singletons[name]
        
        if name not in self._factories:
            raise ValueError(f"Service '{name}' not registered")
        
        factory, singleton = self._factories[name]
        
        # 创建实例
        instance = factory(self)
        
        # 单例则缓存
        if singleton:
            self._singletons[name] = instance
        else:
            self._transients[name] = instance
        
        return instance
    
    async def get_async(self, name: str) -> Any:
        """
        异步获取服务实例
        
        Args:
            name: 服务名称
            
        Returns:
            服务实例
        """
        async with self._lock:
            # 检查单例缓存
            if name in self._singletons:
                return self._singletons[name]
            
            if name not in self._factories:
                raise ValueError(f"Service '{name}' not registered")
            
            factory, singleton = self._factories[name]
            
            # 创建实例（支持异步工厂）
            if asyncio.iscoroutinefunction(factory):
                instance = await factory(self)
            else:
                instance = factory(self)
            
            # 单例则缓存
            if singleton:
                self._singletons[name] = instance
            else:
                self._transients[name] = instance
            
            return instance
    
    def has(self, name: str) -> bool:
        """检查服务是否已注册"""
        return name in self._factories
    
    def clear(self):
        """清除所有缓存的实例"""
        self._singletons.clear()
        self._transients.clear()
