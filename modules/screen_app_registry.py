from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional


APP_RULES_PATH = Path("./data/app_category_rules.json")

DEFAULT_RULES = [
    {
        "name": "Endfield",
        "category": "gaming",
        "display_name": "Endfield",
        "app_contains": ["Endfield", "ArknightsEndfield", "Endfield.exe"],
        "title_contains": ["Endfield", "明日方舟：终末地", "终末地"],
        "domain_contains": [],
        "note": "明日方舟：终末地。",
    },
    {
        "name": "学习喵群聊",
        "category": "social",
        "display_name": "学习喵",
        "app_contains": [],
        "title_contains": ["学习喵"],
        "domain_contains": [],
        "note": "QQ/聊天群窗口，避免误判为网页。",
    },
]


def _build_default_rules() -> list[dict[str, Any]]:
    rules = list(DEFAULT_RULES)
    try:
        import config

        for category, keywords in getattr(config, "WINDOW_CATEGORIES", {}).items():
            rules.append(
                {
                    "name": f"legacy:{category}",
                    "category": str(category),
                    "display_name": str(category),
                    "app_contains": [],
                    "title_contains": list(keywords or []),
                    "domain_contains": [],
                    "note": "从 config.WINDOW_CATEGORIES 迁移的旧窗口分类规则。",
                }
            )

        self_titles = getattr(config, "SELF_WINDOW_TITLES", []) or []
        if self_titles:
            rules.append(
                {
                    "name": "legacy:self_window",
                    "category": "self",
                    "display_name": "Live2D-Suzu",
                    "app_contains": [],
                    "title_contains": list(self_titles),
                    "domain_contains": [],
                    "note": "从 config.SELF_WINDOW_TITLES 迁移的自身窗口识别规则。",
                }
            )
    except Exception:
        pass
    return rules


@dataclass(frozen=True)
class AppCategoryRule:
    name: str
    category: str
    display_name: str
    app_contains: tuple[str, ...] = ()
    title_contains: tuple[str, ...] = ()
    domain_contains: tuple[str, ...] = ()
    note: str = ""


@dataclass(frozen=True)
class AppCategoryMatch:
    rule: AppCategoryRule
    score: int


def _as_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        values: Iterable[Any] = [value]
    elif isinstance(value, list):
        values = value
    else:
        values = []
    return tuple(str(item).strip() for item in values if str(item or "").strip())


def _load_rule(raw: Any) -> Optional[AppCategoryRule]:
    if not isinstance(raw, dict):
        return None
    category = str(raw.get("category") or "").strip()
    if not category:
        return None
    name = str(raw.get("name") or raw.get("display_name") or category).strip()
    display_name = str(raw.get("display_name") or name).strip()
    return AppCategoryRule(
        name=name,
        category=category,
        display_name=display_name,
        app_contains=_as_tuple(raw.get("app_contains")),
        title_contains=_as_tuple(raw.get("title_contains")),
        domain_contains=_as_tuple(raw.get("domain_contains")),
        note=str(raw.get("note") or "").strip(),
    )


def rule_to_dict(rule: AppCategoryRule) -> dict[str, Any]:
    return {
        "name": rule.name,
        "category": rule.category,
        "display_name": rule.display_name,
        "app_contains": list(rule.app_contains),
        "title_contains": list(rule.title_contains),
        "domain_contains": list(rule.domain_contains),
        "note": rule.note,
    }


def save_rules(rules: list[AppCategoryRule], path: Path = APP_RULES_PATH) -> None:
    payload = {
        "version": 1,
        "migrated_legacy_rules": True,
        "rules": [rule_to_dict(rule) for rule in rules],
        "_comment": "category 可用值: coding/gaming/video/social/work/design/browser/other/self",
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


class ScreenAppRegistry:
    def __init__(self, path: Path = APP_RULES_PATH):
        self.path = path
        self._mtime = 0.0
        self._last_check = 0.0
        self._rules: list[AppCategoryRule] = []
        self.reload(force=True)

    @property
    def rules(self) -> list[AppCategoryRule]:
        self.reload()
        return list(self._rules)

    def reload(self, *, force: bool = False) -> None:
        now = time.time()
        if not force and now - self._last_check < 2.0:
            return
        self._last_check = now

        try:
            if not self.path.exists():
                self._write_example_file()
            mtime = self.path.stat().st_mtime
            if not force and mtime == self._mtime:
                return
            self._mtime = mtime
            with self.path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}

        raw_rules = []
        migrated_legacy_rules = False
        if isinstance(data, dict):
            raw_rules = data.get("rules") or []
            migrated_legacy_rules = bool(data.get("migrated_legacy_rules"))
        elif isinstance(data, list):
            raw_rules = data

        default_rules = [] if migrated_legacy_rules else [_load_rule(item) for item in _build_default_rules()]
        merged: dict[str, AppCategoryRule] = {
            (item.name if item else ""): item
            for item in default_rules
            if item is not None
        }
        default_names = set(merged.keys())
        file_names = set()
        for item in (_load_rule(raw) for raw in raw_rules):
            if item is not None:
                file_names.add(item.name)
                merged[item.name] = item
        self._rules = [item for item in merged.values() if item is not None]
        if (default_names and not default_names.issubset(file_names)) or (
            not migrated_legacy_rules and raw_rules
        ):
            try:
                save_rules(self._rules, self.path)
                self._mtime = self.path.stat().st_mtime
            except Exception:
                pass

    def match(self, *, app: str = "", title: str = "", domain: str = "") -> Optional[AppCategoryMatch]:
        self.reload()
        app_lower = str(app or "").lower()
        title_lower = str(title or "").lower()
        domain_lower = str(domain or "").lower()

        best: Optional[AppCategoryMatch] = None
        for rule in self._rules:
            score = 0
            score += self._score_field(rule.app_contains, app_lower, 40)
            score += self._score_field(rule.title_contains, title_lower, 30)
            score += self._score_field(rule.domain_contains, domain_lower, 20)
            if score <= 0:
                continue
            match = AppCategoryMatch(rule=rule, score=score)
            if best is None or match.score > best.score:
                best = match
        return best

    @staticmethod
    def _score_field(patterns: tuple[str, ...], value_lower: str, weight: int) -> int:
        if not patterns or not value_lower:
            return 0
        score = 0
        for pattern in patterns:
            needle = pattern.lower()
            if needle and needle in value_lower:
                score = max(score, weight + min(20, len(needle)))
        return score

    def _write_example_file(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "version": 1,
                "migrated_legacy_rules": True,
                "rules": _build_default_rules(),
                "_comment": "category 可用值: coding/gaming/video/social/work/design/browser/other/self",
            }
            with self.path.open("w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
