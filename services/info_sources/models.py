from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass
class InfoSourceResult:
    ok: bool
    capability: str
    provider: str = ""
    data: Any = None
    summary: str = ""
    error: str = ""
    cached: bool = False
    raw: Any = None
    meta: Dict[str, Any] = field(default_factory=dict)
