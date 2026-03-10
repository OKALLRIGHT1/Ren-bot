"""
核心模块
包含应用的核心基础设施
"""
from .container import ServiceContainer
from .event_bus import EventBus, Events
from .logger import AppLogger, setup_logging, get_logger, set_logger


def __getattr__(name):
    if name in {"Live2DApplication", "EventPresenter"}:
        from .application import Live2DApplication, EventPresenter
        return {"Live2DApplication": Live2DApplication, "EventPresenter": EventPresenter}[name]
    if name == "EventLogger":
        from modules.event_logger import EventLogger
        return EventLogger
    raise AttributeError(name)

__all__ = [
    "ServiceContainer",
    "EventBus",
    "Events",
    "AppLogger",
    "setup_logging",
    "get_logger",
    "set_logger",
    "Live2DApplication",
    "EventLogger",
    "EventPresenter",
]
