from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _normalize_dirs(raw: Any) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    seen = set()
    items = raw if isinstance(raw, list) else []
    for item in items:
        if isinstance(item, dict):
            path = str(item.get("path") or "").strip()
            enabled = bool(item.get("enabled", True))
        else:
            path = str(item or "").strip()
            enabled = True
        if not path or path in seen:
            continue
        seen.add(path)
        rows.append({"path": path, "enabled": enabled})
    return rows


def _safe_filename(title: str) -> str:
    base = re.sub(r'[\\/:*?"<>|\s]+', "_", str(title or "").strip())
    base = re.sub(r"_+", "_", base).strip("._")
    return (base or "knowledge")[:60]


class KnowledgeGuiService:
    def __init__(
        self,
        *,
        plugin_manager: Any = None,
        brain: Any = None,
        plugin_trigger: str = "knowledge_base",
        write_root: Optional[Path] = None,
    ) -> None:
        self.plugin_manager = plugin_manager
        self.brain = brain
        self.plugin_trigger = plugin_trigger
        self.write_root = Path(write_root or "knowledge_docs")

    def _plugin_config(self) -> Dict[str, Any]:
        manager = self.plugin_manager
        if manager is None:
            return {}
        configs = getattr(manager, "plugin_configs", {}) or {}
        return dict(configs.get(self.plugin_trigger) or {})

    def list_dirs(self) -> Dict[str, Any]:
        config = self._plugin_config()
        settings = _as_dict(config.get("settings"))
        field = _as_dict(settings.get("knowledge_source_dirs"))
        dirs = _normalize_dirs(field.get("default", []))
        stats = self.stats().get("data") if self.stats().get("ok") else {}
        return {
            "ok": True,
            "data": {
                "dirs": dirs,
                "stats": stats,
            },
        }

    def save_dirs(self, dirs: Any) -> Dict[str, Any]:
        manager = self.plugin_manager
        if manager is None or not hasattr(manager, "save_plugin_config"):
            return {"ok": False, "error": "plugin_manager_unavailable"}
        config = self._plugin_config()
        settings = _as_dict(config.get("settings"))
        field = _as_dict(settings.get("knowledge_source_dirs"))
        field["default"] = _normalize_dirs(dirs)
        settings["knowledge_source_dirs"] = field
        config["settings"] = settings
        try:
            ok = bool(manager.save_plugin_config(self.plugin_trigger, config))
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        if not ok:
            return {"ok": False, "error": "save_failed"}
        return self.list_dirs()

    def stats(self) -> Dict[str, Any]:
        brain = self.brain
        if brain is None or not hasattr(brain, "get_knowledge_stats"):
            return {
                "ok": True,
                "data": {
                    "available": False,
                    "chunk_count": 0,
                    "rebuild_required": False,
                    "embedding": {},
                },
            }
        try:
            data = dict(brain.get_knowledge_stats() or {})
            data["available"] = True
            return {"ok": True, "data": data}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def search(self, query: str, limit: int = 5) -> Dict[str, Any]:
        query = str(query or "").strip()
        if not query:
            return {"ok": False, "error": "empty_query"}
        brain = self.brain
        if brain is None or not hasattr(brain, "search_knowledge"):
            return {"ok": False, "error": "brain_unavailable"}
        try:
            rows = brain.search_knowledge(query, int(limit or 5)) or []
            results = [str(item) for item in rows]
            return {"ok": True, "data": {"results": results, "count": len(results)}}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def import_file(self, path: str) -> Dict[str, Any]:
        path = str(path or "").strip()
        if not path:
            return {"ok": False, "error": "invalid_path"}
        brain = self.brain
        if brain is None or not hasattr(brain, "import_knowledge_from_file"):
            return {"ok": False, "error": "brain_unavailable"}
        try:
            result = brain.import_knowledge_from_file(path)
            return {"ok": True, "data": {"result": result, "path": path}}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def rebuild(self) -> Dict[str, Any]:
        brain = self.brain
        if brain is None or not hasattr(brain, "rebuild_knowledge_collection"):
            return {"ok": False, "error": "brain_unavailable"}
        try:
            ok = bool(brain.rebuild_knowledge_collection())
            if not ok:
                return {"ok": False, "error": "rebuild_failed"}
            return {"ok": True, "data": self.stats().get("data") or {}}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def delete_by_dirs(self, dirs: Any) -> Dict[str, Any]:
        targets = [
            str(item.get("path") if isinstance(item, dict) else item).strip()
            for item in (dirs or [])
        ]
        targets = [item for item in targets if item]
        if not targets:
            return {"ok": False, "error": "empty_dirs"}
        brain = self.brain
        if brain is None or not hasattr(brain, "delete_knowledge_by_dirs"):
            return {"ok": False, "error": "brain_unavailable"}
        try:
            removed = int(brain.delete_knowledge_by_dirs(targets) or 0)
            return {"ok": True, "data": {"removed": removed, "dirs": targets}}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def learn_configured_dirs(self) -> Dict[str, Any]:
        manager = self.plugin_manager
        plugin = None
        if manager is not None:
            plugins = getattr(manager, "plugins", {}) or {}
            plugin = plugins.get(self.plugin_trigger)
        brain = self.brain
        if plugin is None or brain is None:
            return {"ok": False, "error": "plugin_or_brain_unavailable"}
        ingest = getattr(plugin, "gui_ingest_configured_dirs", None)
        if not callable(ingest):
            return {"ok": False, "error": "ingest_unavailable"}
        try:
            import asyncio

            if asyncio.iscoroutinefunction(ingest):
                result = asyncio.run(ingest({"brain": brain}))
            else:
                result = ingest({"brain": brain})
            return {"ok": True, "data": {"result": str(result)}}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def create_doc(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        title = str(payload.get("title") or "").strip()
        lines = [
            str(item).strip()
            for item in (payload.get("lines") or [])
            if str(item).strip()
        ]
        if not title or not lines:
            return {"ok": False, "error": "empty_fields"}
        target_dir = Path(str(payload.get("target_dir") or self.write_root)).expanduser()
        source = str(payload.get("source") or "manual").strip() or "manual"
        tags = [str(item).strip() for item in (payload.get("tags") or []) if str(item).strip()]
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        content_lines = [
            f"# {title}",
            f"来源：{source}",
            f"标签：{', '.join(tags) if tags else '未分类'}",
            f"整理时间：{now}",
            "",
            "## 知识条目",
        ]
        for item in lines:
            content_lines.append(f"- {title}：{item}")
        content_lines.append("")
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = target_dir / f"{_safe_filename(title)}_{stamp}.md"
            path.write_text("\n".join(content_lines), encoding="utf-8")
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        data: Dict[str, Any] = {"path": str(path.resolve())}
        if payload.get("ingest_now"):
            imported = self.import_file(str(path))
            data["import"] = imported.get("data") if imported.get("ok") else {"error": imported.get("error")}
        return {"ok": True, "data": data}
