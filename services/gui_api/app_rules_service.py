from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _patterns(value: Any) -> List[str]:
    if isinstance(value, list):
        items = value
    elif isinstance(value, tuple):
        items = list(value)
    else:
        text = str(value or "")
        parts = []
        for chunk in text.replace("\n", ";").replace(",", ";").replace("，", ";").split(";"):
            parts.append(chunk.strip())
        items = parts
    out: List[str] = []
    for item in items:
        text = str(item or "").strip()
        if text and text not in out:
            out.append(text)
    return out


def _client_rule(row: Any) -> Dict[str, Any]:
    if isinstance(row, dict):
        data = row
    else:
        data = {
            "name": getattr(row, "name", ""),
            "category": getattr(row, "category", "other"),
            "display_name": getattr(row, "display_name", ""),
            "app_contains": getattr(row, "app_contains", ()),
            "title_contains": getattr(row, "title_contains", ()),
            "domain_contains": getattr(row, "domain_contains", ()),
            "note": getattr(row, "note", ""),
        }
    app_patterns = data.get("app_patterns", data.get("app_contains"))
    title_patterns = data.get("title_patterns", data.get("title_contains"))
    domain_patterns = data.get("domain_patterns", data.get("domain_contains"))
    return {
        "name": str(data.get("name") or ""),
        "category": str(data.get("category") or "other"),
        "display_name": str(data.get("display_name") or data.get("name") or ""),
        "app_patterns": _patterns(app_patterns),
        "title_patterns": _patterns(title_patterns),
        "domain_patterns": _patterns(domain_patterns),
        "note": str(data.get("note") or ""),
    }


class AppRulesGuiService:
    def __init__(
        self,
        *,
        rules_path: str | Path | None = None,
        registry: Any = None,
    ) -> None:
        self.rules_path = Path(rules_path) if rules_path is not None else None
        self._registry = registry

    def _get_registry(self) -> Any:
        if self._registry is not None:
            return self._registry
        from modules.screen_app_registry import APP_RULES_PATH, ScreenAppRegistry

        path = self.rules_path or APP_RULES_PATH
        self._registry = ScreenAppRegistry(path=path)
        return self._registry

    def list_rules(self) -> Dict[str, Any]:
        try:
            registry = self._get_registry()
            registry.reload(force=True)
            raw_rules = registry.rules() if callable(getattr(registry, "rules", None)) else registry.rules
            rules = [_client_rule(rule) for rule in list(raw_rules or [])]
            path = str(getattr(registry, "path", self.rules_path or ""))
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        return {
            "ok": True,
            "data": {
                "rules": rules,
                "count": len(rules),
                "path": path,
                "categories": [
                    "coding",
                    "gaming",
                    "video",
                    "social",
                    "work",
                    "design",
                    "browser",
                    "other",
                    "self",
                ],
            },
        }

    def save_rules(self, rules: Any) -> Dict[str, Any]:
        rows = rules if isinstance(rules, list) else []
        normalized = [_client_rule(row) for row in rows if _as_dict(row) or hasattr(row, "name")]
        try:
            from modules.screen_app_registry import AppCategoryRule, save_rules

            objects: List[AppCategoryRule] = []
            for row in normalized:
                objects.append(
                    AppCategoryRule(
                        name=row["name"],
                        category=row["category"],
                        display_name=row["display_name"],
                        app_contains=tuple(row["app_patterns"]),
                        title_contains=tuple(row["title_patterns"]),
                        domain_contains=tuple(row["domain_patterns"]),
                        note=row["note"],
                    )
                )
            registry = self._get_registry()
            path = Path(getattr(registry, "path", self.rules_path or "data/app_category_rules.json"))
            save_rules(objects, path=path)
            registry.reload(force=True)
        except Exception as exc:
            return {"ok": False, "error": str(exc) or "save_failed"}
        return self.list_rules()

    def test_match(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        body = _as_dict(payload)
        try:
            registry = self._get_registry()
            match = registry.match(
                app=str(body.get("app") or ""),
                title=str(body.get("title") or ""),
                domain=str(body.get("domain") or ""),
            )
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        if match is None:
            return {
                "ok": True,
                "data": {"matched": False, "category": "", "display_name": "", "rule": None},
            }
        rule = getattr(match, "rule", None)
        return {
            "ok": True,
            "data": {
                "matched": True,
                "category": str(getattr(match, "category", "") or getattr(rule, "category", "") or ""),
                "display_name": str(
                    getattr(match, "display_name", "") or getattr(rule, "display_name", "") or ""
                ),
                "rule": _client_rule(rule) if rule is not None else None,
            },
        }
