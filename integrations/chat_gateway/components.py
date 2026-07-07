from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(slots=True)
class MessageComponent:
    type: str
    text: str = ""
    data: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["type"] = str(payload.get("type") or "unknown")
        return payload


def component(
    type_: str, text: str = "", data: Optional[Dict[str, Any]] = None
) -> MessageComponent:
    return MessageComponent(type=str(type_ or "unknown"), text=str(text or ""), data=dict(data or {}))


def components_to_dicts(items: List[MessageComponent]) -> List[Dict[str, Any]]:
    return [item.to_dict() for item in items if isinstance(item, MessageComponent)]


def plain_text_from_components(items: List[MessageComponent]) -> str:
    parts = [str(item.text or "").strip() for item in items if str(item.text or "").strip()]
    return " ".join(" ".join(parts).split())
