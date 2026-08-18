from __future__ import annotations

import re
import time
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


def ingest_knowledge_paths(
    paths: Any,
    *,
    import_file: Callable[..., Any],
    get_stats: Optional[Callable[[], Dict[str, Any]]] = None,
    on_progress: Optional[Callable[[Dict[str, Any]], None]] = None,
    slow: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Shared file ingest used by GUI, plugin learn, and one-click learn."""
    files = [str(item or "").strip() for item in (paths or []) if str(item or "").strip()]
    results: List[str] = []
    added = 0
    skipped = 0
    failed = 0
    adaptive_hits = 0
    slow_cfg = slow if isinstance(slow, dict) else {}
    slow_enabled = bool(slow_cfg.get("enabled", False))
    slow_batch = max(1, int(slow_cfg.get("batch") or 20))
    slow_sleep_ms = max(0, int(slow_cfg.get("sleep_ms") or 0))
    adaptive = bool(slow_cfg.get("adaptive", False))
    dynamic_sleep_ms = slow_sleep_ms
    stats_before = dict(get_stats() or {}) if callable(get_stats) else {}
    last_rate_limit_hits = int(stats_before.get("rate_limit_hits", 0) or 0)
    total = len(files)

    for index, path in enumerate(files, 1):
        def _progress(info: Any, *, current=path, current_index=index) -> None:
            payload = dict(info or {})
            payload.setdefault("file_index", current_index)
            payload.setdefault("file_count", total)
            payload.setdefault("file_path", current)
            if on_progress is not None:
                on_progress(payload)

        try:
            result = import_file(path, progress_callback=_progress)
            if isinstance(result, dict):
                item_added = int(result.get("added", 0) or 0)
                item_skipped = int(result.get("skipped", 0) or 0)
                if result.get("ok") is False:
                    failed += 1
                    results.append(
                        f"{Path(path).name}: 导入失败 - "
                        f"{result.get('status') or result.get('error') or 'unknown'}"
                    )
                    continue
            else:
                item_added = int(result or 0)
                item_skipped = 0
            added += item_added
            skipped += item_skipped
            results.append(
                f"{Path(path).name}: 新增 {item_added} 条，跳过 {item_skipped} 条。"
            )
        except Exception as exc:
            failed += 1
            results.append(f"{Path(path).name}: 导入失败 - {exc}")

        if adaptive and callable(get_stats):
            current_stats = dict(get_stats() or {})
            current_rate_limit_hits = int(current_stats.get("rate_limit_hits", 0) or 0)
            if current_rate_limit_hits > last_rate_limit_hits:
                adaptive_hits += current_rate_limit_hits - last_rate_limit_hits
                dynamic_sleep_ms = min(max(dynamic_sleep_ms * 2, slow_sleep_ms), 10000)
                last_rate_limit_hits = current_rate_limit_hits
            elif dynamic_sleep_ms > slow_sleep_ms:
                dynamic_sleep_ms = max(slow_sleep_ms, dynamic_sleep_ms - 300)
        if slow_enabled and dynamic_sleep_ms > 0 and index % slow_batch == 0:
            time.sleep(dynamic_sleep_ms / 1000.0)

    stats_after = dict(get_stats() or {}) if callable(get_stats) else {}
    rate_limit_hits = int(stats_after.get("rate_limit_hits", 0) or 0) - int(
        stats_before.get("rate_limit_hits", 0) or 0
    )
    fallback_uses = int(stats_after.get("fallback_uses", 0) or 0) - int(
        stats_before.get("fallback_uses", 0) or 0
    )
    return {
        "file_count": total,
        "added": added,
        "skipped": skipped,
        "failed": failed,
        "results": results,
        "rate_limit_hits": max(0, rate_limit_hits),
        "fallback_uses": max(0, fallback_uses),
        "adaptive_hits": adaptive_hits,
    }


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
            from modules.memory.retrieval import (
                KnowledgeHit,
                format_knowledge_hits_for_display,
            )

            rows = brain.search_knowledge(query, int(limit or 5)) or []
            results = format_knowledge_hits_for_display(rows)
            structured = []
            for item in rows:
                if isinstance(item, KnowledgeHit):
                    structured.append(
                        {
                            "id": item.id,
                            "content": item.content,
                            "source": item.source,
                            "source_path": item.source_path,
                            "score": item.score,
                        }
                    )
            return {
                "ok": True,
                "data": {
                    "results": results,
                    "count": len(results),
                    "hits": structured,
                },
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def import_file(
        self, path: str, progress_callback: Optional[Callable[..., Any]] = None
    ) -> Dict[str, Any]:
        path = str(path or "").strip()
        if not path:
            return {"ok": False, "error": "invalid_path"}
        brain = self.brain
        if brain is None or not hasattr(brain, "import_knowledge_from_file"):
            return {"ok": False, "error": "brain_unavailable"}
        try:
            result = brain.import_knowledge_from_file(
                path, progress_callback=progress_callback
            )
            ok = True
            if isinstance(result, dict) and result.get("ok") is False:
                ok = False
            return {"ok": ok, "data": {"result": result, "path": path}}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def ingest_files(
        self,
        paths: Any,
        *,
        progress: Optional[Callable[[Dict[str, Any]], None]] = None,
        slow: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if self.brain is None or not hasattr(self.brain, "import_knowledge_from_file"):
            return {"ok": False, "error": "brain_unavailable"}

        def _import(path: str, progress_callback=None):
            wrapped = self.import_file(path, progress_callback=progress_callback)
            raw = (wrapped.get("data") or {}).get("result")
            if not wrapped.get("ok"):
                if isinstance(raw, dict):
                    return raw
                return {"ok": False, "error": wrapped.get("error") or "import_failed"}
            return raw

        data = ingest_knowledge_paths(
            paths,
            import_file=_import,
            get_stats=lambda: (self.stats().get("data") or {}),
            on_progress=progress,
            slow=slow,
        )
        return {"ok": True, "data": data}

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

    @staticmethod
    def _run_maybe_async(func, *args, **kwargs):
        """Run sync/async plugin helpers without nesting asyncio.run()."""
        import asyncio
        import inspect

        result = func(*args, **kwargs)
        if not inspect.isawaitable(result):
            return result
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(result)

        # Already inside GUI/aiohttp loop: execute coroutine on a worker thread
        # with its own event loop so we never call asyncio.run() in-loop.
        import concurrent.futures

        def _runner():
            return asyncio.run(result)

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(_runner).result()

    def learn_configured_dirs(self) -> Dict[str, Any]:
        manager = self.plugin_manager
        plugin = None
        if manager is not None:
            plugins = getattr(manager, "plugins", {}) or {}
            plugin = plugins.get(self.plugin_trigger)
        if self.brain is None:
            return {"ok": False, "error": "brain_unavailable"}
        list_files = getattr(plugin, "list_configured_learn_files", None) if plugin else None
        if not callable(list_files):
            ingest = getattr(plugin, "gui_ingest_configured_dirs", None) if plugin else None
            if not callable(ingest):
                return {"ok": False, "error": "ingest_unavailable"}
            try:
                result = self._run_maybe_async(ingest, {"brain": self.brain})
                return {"ok": True, "data": {"result": str(result)}}
            except Exception as exc:
                return {"ok": False, "error": str(exc)}
        try:
            paths = list(list_files() or [])
            slow = None
            slow_cfg = getattr(plugin, "_slow_ingest_config", None)
            if callable(slow_cfg):
                enabled, batch, sleep_ms, adaptive = slow_cfg()
                slow = {
                    "enabled": bool(enabled),
                    "batch": batch,
                    "sleep_ms": sleep_ms,
                    "adaptive": bool(adaptive),
                }
            ingested = self.ingest_files(paths, slow=slow)
            if not ingested.get("ok"):
                return ingested
            data = ingested.get("data") or {}
            failed = int(data.get("failed") or 0)
            prefix = "⚠️ GUI 学习完成，但有文件失败" if failed else "✅ GUI 学习完成"
            summary = (
                f"{prefix}！共扫描 {int(data.get('file_count') or 0)} 个文件，"
                f"新增 {int(data.get('added') or 0)} 条知识片段，"
                f"跳过 {int(data.get('skipped') or 0)} 条已存在片段，"
                f"失败 {failed} 个文件。"
                f"限频 {int(data.get('rate_limit_hits') or 0)} 次，"
                f"fallback {int(data.get('fallback_uses') or 0)} 次，"
                f"自适应触发 {int(data.get('adaptive_hits') or 0)} 次。"
            )
            return {"ok": True, "data": {"result": summary, **data}}
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
