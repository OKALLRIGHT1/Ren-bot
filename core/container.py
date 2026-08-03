"""Optional service registry (not a full DI framework).

Historically this class was registered from Live2DApplication but never
resolved via get(). Callers should keep using explicit attributes on the
application object. The registry remains for dispose()/future wiring only;
do not assume services are available through the container at runtime.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Dict


class ServiceContainer:
    """Lightweight named-instance registry with optional dispose hooks."""

    def __init__(self):
        self._factories: Dict[str, tuple] = {}
        self._singletons: Dict[str, Any] = {}
        self._transients: Dict[str, Any] = {}
        self._lock = asyncio.Lock()

    async def dispose(self):
        """Dispose cached instances that expose dispose()/close()."""
        for name, instance in list(self._singletons.items()):
            try:
                if hasattr(instance, "dispose") and callable(instance.dispose):
                    if asyncio.iscoroutinefunction(instance.dispose):
                        await instance.dispose()
                    else:
                        instance.dispose()
                elif hasattr(instance, "close") and callable(instance.close):
                    if asyncio.iscoroutinefunction(instance.close):
                        await instance.close()
                    else:
                        instance.close()
            except Exception as e:
                print(f"⚠️ [Container] 清理 {name} 失败: {e}")

        self._singletons.clear()
        self._transients.clear()

    def register(self, name: str, factory: Callable, singleton: bool = True):
        """Register a factory. Prefer application attributes for real deps."""
        self._factories[name] = (factory, singleton)

    def get(self, name: str) -> Any:
        """Resolve a registered service. Unused by the main boot path today."""
        if name in self._singletons:
            return self._singletons[name]

        if name not in self._factories:
            raise ValueError(f"Service '{name}' not registered")

        factory, singleton = self._factories[name]
        instance = factory(self)

        if singleton:
            self._singletons[name] = instance
        else:
            self._transients[name] = instance

        return instance

    async def get_async(self, name: str) -> Any:
        async with self._lock:
            if name in self._singletons:
                return self._singletons[name]

            if name not in self._factories:
                raise ValueError(f"Service '{name}' not registered")

            factory, singleton = self._factories[name]
            if asyncio.iscoroutinefunction(factory):
                instance = await factory(self)
            else:
                instance = factory(self)

            if singleton:
                self._singletons[name] = instance
            else:
                self._transients[name] = instance

            return instance

    def has(self, name: str) -> bool:
        return name in self._factories

    def clear(self):
        self._singletons.clear()
        self._transients.clear()
