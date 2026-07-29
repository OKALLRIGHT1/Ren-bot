from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional


APP_RULES_PATH = Path("./data/app_category_rules.json")

# Always-on base rules. File rules with the same name can override these.
BASE_RULES = [
    {
        "name": "base:browser",
        "category": "browser",
        "display_name": "Browser",
        "app_contains": [
            "chrome.exe",
            "msedge.exe",
            "firefox.exe",
            "brave.exe",
            "tabbit browser.exe",
            "quark.exe",
        ],
        "title_contains": ["Google Chrome", "Microsoft Edge", "Mozilla Firefox"],
        "domain_contains": [],
        "note": "Browser processes.",
    },
    {
        "name": "base:coding",
        "category": "coding",
        "display_name": "Coding",
        "app_contains": [
            "code.exe",
            "code - insiders.exe",
            "devenv.exe",
            "pycharm64.exe",
            "pycharm.exe",
            "idea64.exe",
            "webstorm64.exe",
            "cursor.exe",
            "zcode.exe",
            "codex.exe",
            "opencode.exe",
            "antigravity.exe",
            "windowsterminal.exe",
            "powershell.exe",
            "cmd.exe",
        ],
        "title_contains": [
            "Visual Studio Code",
            "Visual Studio",
            "PyCharm",
            "IntelliJ",
            "Sublime Text",
        ],
        "domain_contains": ["github.com", "gitlab.com", "gitee.com"],
        "note": "Editors/IDEs/terminals and common code hosts.",
    },
    {
        "name": "base:social",
        "category": "social",
        "display_name": "Social",
        "app_contains": [
            "qq.exe",
            "weixin.exe",
            "wechat.exe",
            "discord.exe",
            "telegram.exe",
            "feishu.exe",
            "dingtalk.exe",
        ],
        "title_contains": ["微信", "Discord", "Telegram", "钉钉", "飞书"],
        "domain_contains": [],
        "note": "Chat clients.",
    },
    {
        "name": "base:work",
        "category": "work",
        "display_name": "Work",
        "app_contains": [
            "winword.exe",
            "excel.exe",
            "powerpnt.exe",
            "wps.exe",
            "et.exe",
            "wpp.exe",
            "acrobat.exe",
            "notepad.exe",
            "notepad++.exe",
        ],
        "title_contains": [
            "Microsoft Word",
            "Microsoft Excel",
            "Microsoft PowerPoint",
            "WPS",
        ],
        "domain_contains": ["docs.google.com", "notion.so", "office.com"],
        "note": "Office and document tools.",
    },
    {
        "name": "base:gaming",
        "category": "gaming",
        "display_name": "Gaming",
        "app_contains": [
            "steam.exe",
            "steamwebhelper.exe",
            "endfield.exe",
            "arknightsendfield.exe",
            "mumu",
            "gamesviewer.exe",
            "eadesktop.exe",
        ],
        "title_contains": ["Genshin", "StarRail", "Minecraft", "崩坏", "原神", "终末地"],
        "domain_contains": [],
        "note": "Games and launchers.",
    },
    {
        "name": "base:video",
        "category": "video",
        "display_name": "Video",
        "app_contains": ["potplayer", "vlc.exe", "mpv.exe"],
        "title_contains": ["Bilibili", "YouTube", "爱奇艺", "PotPlayer", "VLC"],
        "domain_contains": [
            "bilibili.com",
            "youtube.com",
            "youtu.be",
            "iqiyi.com",
            "youku.com",
        ],
        "note": "Players and video sites.",
    },
    {
        "name": "base:self",
        "category": "self",
        "display_name": "Live2D-Suzu",
        "app_contains": [
            "live2d-enhanced.exe",
            "live2d-only.exe",
            "live2d-suzu.exe",
            "live2d agent",
        ],
        "title_contains": [],
        "domain_contains": [],
        "note": "Own desktop client processes.",
    },
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
    rules = list(BASE_RULES)
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
        if isinstance(data, dict):
            raw_rules = data.get("rules") or []
        elif isinstance(data, list):
            raw_rules = data

        # Always start from base/default rules, then let file rules override by name.
        merged: dict[str, AppCategoryRule] = {}
        for item in (_load_rule(raw) for raw in _build_default_rules()):
            if item is not None:
                merged[item.name] = item
        for item in (_load_rule(raw) for raw in raw_rules):
            if item is not None:
                merged[item.name] = item
        self._rules = [item for item in merged.values() if item is not None]

    def match(self, *, app: str = "", title: str = "", domain: str = "") -> Optional[AppCategoryMatch]:
        self.reload()
        app_lower = str(app or "").lower()
        title_lower = str(title or "").lower()
        domain_lower = str(domain or "").lower()

        best: Optional[AppCategoryMatch] = None
        for rule in self._rules:
            score = 0
            # Process name is usually strongest; explicit domain can refine browser tabs
            # (e.g. bilibili.com -> video over generic chrome.exe -> browser).
            score += self._score_field(rule.app_contains, app_lower, 80)
            # Domain is more specific than browser chrome title branding.
            score += self._score_field(rule.domain_contains, domain_lower, 160)
            score += self._score_field(rule.title_contains, title_lower, 30)
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
            needle = pattern.lower().strip()
            if not needle:
                continue
            # Ignore extremely short needles that cause false positives.
            if len(needle) < 3 and weight < 80:
                continue
            if needle in value_lower:
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
