"""
服务模块初始化
Services Module Initialization
"""

__all__ = [
    "ChatService",
]


def __getattr__(name):
    if name == "ChatService":
        from .chat_service import ChatService

        return ChatService
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
