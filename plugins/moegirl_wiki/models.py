from dataclasses import dataclass, field


@dataclass(slots=True)
class MoegirlCandidate:
    title: str
    url: str
    description: str = ""


@dataclass(slots=True)
class MoegirlPageSummary:
    title: str
    summary: str
    url: str
    categories: list[str] = field(default_factory=list)
    thumbnail_url: str | None = None
    page_id: int | None = None


@dataclass(slots=True)
class MoegirlLookupResult:
    status: str
    page: MoegirlPageSummary | None = None
    candidates: list[MoegirlCandidate] = field(default_factory=list)
    message: str = ""
