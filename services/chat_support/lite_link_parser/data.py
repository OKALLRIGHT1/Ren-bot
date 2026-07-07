from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


@dataclass(slots=True)
class LiteLinkResult:
    platform: str
    url: str
    title: str = ""
    text: str = ""
    image_urls: List[str] = field(default_factory=list)
