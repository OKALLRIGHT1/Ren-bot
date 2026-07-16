from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import sys
import threading
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from aiohttp import web
from integrations.gui_media import GuiMediaRegistry, MediaTicketError
from modules.security_redaction import is_secret_setting


LIVE2D_ACTIVITY_SOURCE = "live2d-tauri"


class GuiHttpServer:
    SECRET_MASK = "__LIVE2D_SECRET_MASK__"
    PORT_FALLBACK_COUNT = 8

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 8097,
        path_prefix: str = "/gui",
        logger: Optional[Any] = None,
        app_ref: Optional[Any] = None,
        access_token: str = "",
        media_registry: Optional[GuiMediaRegistry] = None,
    ):
        self.host = str(host or "127.0.0.1").strip() or "127.0.0.1"
        self.port = int(port)
        self.path_prefix = self._normalize_prefix(path_prefix)
        self.logger = logger
        self.app_ref = app_ref
        self.access_token = str(access_token or "").strip()
        self.media_registry = media_registry or GuiMediaRegistry()

        self._thread: Optional[threading.Thread] = None
        self._server_loop: Optional[asyncio.AbstractEventLoop] = None
        self._runner: Optional[web.AppRunner] = None
        self._site: Optional[web.TCPSite] = None
        self._ready = threading.Event()
        self._start_error: Optional[BaseException] = None

    @staticmethod
    def _normalize_prefix(path_prefix: str) -> str:
        value = str(path_prefix or "/gui").strip() or "/gui"
        if not value.startswith("/"):
            value = "/" + value
        return value.rstrip("/") or "/"

    def _api_path(self, suffix: str) -> str:
        trimmed = str(suffix or "").strip()
        if not trimmed.startswith("/"):
            trimmed = "/" + trimmed
        if self.path_prefix == "/":
            return trimmed
        return f"{self.path_prefix}{trimmed}"

    def _http_url(self) -> str:
        return f"http://{self.host}:{self.port}{self.path_prefix}"

    def activity_ingest_url(self) -> str:
        return f"{self._http_url()}/activity-ingest"

    def _candidate_ports(self) -> list[int]:
        if self.port <= 0:
            return [self.port]
        max_port = 65535
        candidates = [self.port]
        for offset in range(1, self.PORT_FALLBACK_COUNT + 1):
            candidate = self.port + offset
            if candidate <= max_port:
                candidates.append(candidate)
        candidates.append(0)
        return candidates

    def _apply_bound_port(self, requested_port: int) -> None:
        if requested_port != 0 or self._site is None:
            return
        server = getattr(self._site, "_server", None)
        sockets = getattr(server, "sockets", None) or []
        if not sockets:
            return
        try:
            self.port = int(sockets[0].getsockname()[1])
        except Exception:
            self.port = requested_port

    @staticmethod
    def _json_response(payload: Dict[str, Any], status: int = 200) -> web.Response:
        return web.json_response(
            payload, status=status, dumps=lambda x: json.dumps(x, ensure_ascii=False)
        )

    def _cors_headers(self, request: web.Request, response: web.StreamResponse) -> None:
        origin = str(request.headers.get("Origin") or "").strip()
        if self._origin_allowed(origin):
            response.headers["Access-Control-Allow-Origin"] = origin
        elif not origin:
            response.headers["Access-Control-Allow-Origin"] = f"http://{self.host}:{self.port}"
        response.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type,Authorization,X-GUI-Token"

    def _origin_allowed(self, origin: str) -> bool:
        if not origin:
            return False
        try:
            parsed = urlparse(origin)
        except Exception:
            return False
        if parsed.scheme not in {"http", "https"}:
            return False
        if (
            parsed.username
            or parsed.password
            or parsed.params
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            return False
        hostname = (parsed.hostname or "").strip().lower()
        if hostname not in {"127.0.0.1", "localhost", "::1", str(self.host).lower()}:
            return False
        default_port = 443 if parsed.scheme == "https" else 80
        try:
            origin_port = parsed.port
        except ValueError:
            return False
        return int(origin_port or default_port) == int(self.port)

    def _extract_token(self, request: web.Request) -> str:
        header_token = str(request.headers.get("X-GUI-Token") or "").strip()
        if header_token:
            return header_token
        auth = str(request.headers.get("Authorization") or "").strip()
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        return ""

    def _request_authorized(self, request: web.Request) -> bool:
        if request.path == self._api_path("/health"):
            return True
        if not self.access_token:
            return False
        provided = self._extract_token(request)
        return bool(provided) and secrets.compare_digest(provided, self.access_token)

    @web.middleware
    async def _cors_middleware(self, request: web.Request, handler):
        if request.method == "OPTIONS":
            response = web.Response(status=204)
            self._cors_headers(request, response)
            return response
        response = await handler(request)
        self._cors_headers(request, response)
        return response

    @web.middleware
    async def _auth_middleware(self, request: web.Request, handler):
        if request.method == "OPTIONS":
            return await handler(request)
        if not self._request_authorized(request):
            return self._json_response({"ok": False, "error": "unauthorized"}, status=401)
        return await handler(request)

    @staticmethod
    def _load_json(
        path: Path, fallback: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        if fallback is None:
            fallback = {}
        if not path.exists():
            return dict(fallback)
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else dict(fallback)
        except Exception:
            return dict(fallback)

    @staticmethod
    def _save_json(path: Path, data: Dict[str, Any]) -> bool:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return True
        except Exception:
            return False

    @staticmethod
    def _normalize_path_value(value: str) -> str:
        if not value:
            return ""
        return str(value).replace("\\", "/").rstrip("/")

    @staticmethod
    def _join_path(*parts: str) -> str:
        cleaned = [GuiHttpServer._normalize_path_value(p) for p in parts if p]
        return "/".join([p for p in cleaned if p])

    @classmethod
    def _is_secret_key(cls, key: str) -> bool:
        return is_secret_setting(key)

    @classmethod
    def _mask_secret_value(cls, value: Any) -> Any:
        if isinstance(value, str) and value:
            return cls.SECRET_MASK
        return value

    @classmethod
    def _mask_secrets(
        cls,
        value: Any,
        parent_key: str = "",
        parent_is_secret: bool = False,
    ) -> Any:
        if isinstance(value, dict):
            masked: Dict[str, Any] = {}
            for key, item in value.items():
                key_text = str(key)
                secret_setting = is_secret_setting(key_text, item)
                if secret_setting and isinstance(item, dict):
                    masked[key] = cls._mask_secrets(
                        item,
                        key_text,
                        parent_is_secret=True,
                    )
                elif secret_setting:
                    masked[key] = cls._mask_secret_value(item)
                else:
                    child_parent = (
                        parent_key
                        if key_text == "default" and parent_is_secret
                        else key_text
                    )
                    masked[key] = cls._mask_secrets(
                        item,
                        child_parent,
                        parent_is_secret=parent_is_secret and key_text == "default",
                    )
            return masked
        if isinstance(value, list):
            return [
                cls._mask_secrets(item, parent_key, parent_is_secret)
                for item in value
            ]
        if parent_is_secret or cls._is_secret_key(parent_key):
            return cls._mask_secret_value(value)
        return value

    @classmethod
    def _restore_masked_secrets(
        cls,
        incoming: Any,
        current: Any,
        parent_key: str = "",
        parent_is_secret: bool = False,
    ) -> Any:
        if isinstance(incoming, dict):
            existing = current if isinstance(current, dict) else {}
            restored: Dict[str, Any] = {}
            for key, value in incoming.items():
                key_text = str(key)
                secret_setting = is_secret_setting(key_text, value)
                child_parent = (
                    parent_key
                    if key_text == "default" and parent_is_secret
                    else key_text
                )
                restored[key] = cls._restore_masked_secrets(
                    value,
                    existing.get(key),
                    child_parent,
                    parent_is_secret=(
                        secret_setting
                        if isinstance(value, dict)
                        else parent_is_secret and key_text == "default"
                    ),
                )
            return restored
        if isinstance(incoming, list):
            existing_items = current if isinstance(current, list) else []
            restored_list = []
            for index, item in enumerate(incoming):
                existing_item = (
                    existing_items[index] if index < len(existing_items) else None
                )
                restored_list.append(
                    cls._restore_masked_secrets(
                        item,
                        existing_item,
                        parent_key,
                        parent_is_secret,
                    )
                )
            return restored_list
        if (
            isinstance(incoming, str)
            and incoming == cls.SECRET_MASK
            and (parent_is_secret or cls._is_secret_key(parent_key))
            and isinstance(current, str)
        ):
            return current
        return incoming

    @staticmethod
    def _find_backend_root(start_dir: Optional[str] = None) -> Optional[str]:
        candidates: list[str] = []
        if start_dir:
            normalized = GuiHttpServer._normalize_path_value(start_dir)
            if normalized:
                parts = normalized.split("/")
                min_depth = max(2, len(parts) - 6)
                for i in range(len(parts), min_depth - 1, -1):
                    base = "/".join(parts[:i])
                    candidates.append(GuiHttpServer._join_path(base, "live2d-llm"))

        cwd = GuiHttpServer._normalize_path_value(os.getcwd())
        if cwd:
            candidates.append(cwd)
        candidates.append("./live2d-llm")

        for candidate in candidates:
            if not candidate:
                continue
            if os.path.exists(candidate):
                return GuiHttpServer._normalize_path_value(candidate)
        return None

    def _build_paths(self, root: str) -> Dict[str, str]:
        return {
            "customModels": self._join_path(root, "data", "custom_models.json"),
            "runtimeSettings": self._join_path(root, "data", "runtime_settings.json"),
            "characters": self._join_path(root, "data", "characters.json"),
            "mcpConfig": self._join_path(root, "plugins", "mcp_tools", "config.json"),
        }

    def _get_memory_store(self):
        try:
            root = self._find_backend_root(os.getcwd())
            if root:
                memory_db = Path(root) / "memory" / "memory.sqlite"
                os.environ["MEMORY_SQLITE_PATH"] = str(memory_db)
                os.environ.setdefault("MEMORY_DIR", str(memory_db.parent))
            import importlib.util

            module_path = (
                Path(__file__).resolve().parent.parent / "modules" / "memory_sqlite.py"
            )
            spec = importlib.util.spec_from_file_location(
                "gui_http_memory_sqlite", module_path
            )
            if spec is None or spec.loader is None:
                return None
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            return module.get_memory_store()
        except Exception:
            return None

    def _get_brain(self):
        chat_service = (
            getattr(self.app_ref, "chat_service", None) if self.app_ref is not None else None
        )
        brain = getattr(chat_service, "brain", None)
        if brain is not None:
            return brain
        return getattr(self.app_ref, "brain", None) if self.app_ref is not None else None

    def _get_memory_core(self):
        brain = self._get_brain()
        memory_core = getattr(brain, "memory_core", None) if brain is not None else None
        if memory_core is not None:
            return memory_core
        store = self._get_memory_store()
        if store is None:
            return None
        try:
            from config import MEMORY_SETTINGS
            from modules.memory_core import MemoryCoreService

            core = MemoryCoreService(store, settings=MEMORY_SETTINGS)
            core.initialize()
            return core
        except Exception:
            return None

    def _memory_gui_service(self):
        from services.gui_api.memory_service import MemoryGuiService

        return MemoryGuiService(memory_core=self._get_memory_core(), brain=self._get_brain())

    def _diary_gui_service(self):
        from services.gui_api.diary_service import DiaryGuiService

        root = self._find_backend_root(os.getcwd()) or os.getcwd()
        return DiaryGuiService(
            store=self._get_memory_store(),
            export_root=Path(root) / "output",
        )

    def _knowledge_gui_service(self):
        from services.gui_api.knowledge_service import KnowledgeGuiService

        root = self._find_backend_root(os.getcwd()) or os.getcwd()
        return KnowledgeGuiService(
            plugin_manager=self._get_plugin_manager(),
            brain=self._get_brain(),
            write_root=Path(root) / "knowledge_docs",
        )

    def _knowledge_result_status(self, result: Dict[str, Any]) -> int:
        error = str(result.get("error") or "")
        if error in {
            "plugin_manager_unavailable",
            "brain_unavailable",
            "plugin_or_brain_unavailable",
            "ingest_unavailable",
        }:
            return 503
        if error in {"empty_query", "invalid_path", "empty_dirs", "empty_fields"}:
            return 400
        return 400

    def _diary_result_status(self, result: Dict[str, Any]) -> int:
        error = str(result.get("error") or "")
        if error in {"not_found", "no_diaries"}:
            return 404
        if error in {"memory_store_unavailable"}:
            return 503
        if error in {"empty_fields", "invalid_id"}:
            return 400
        return 400

    def _memory_result_status(self, result: Dict[str, Any]) -> int:
        error = str(result.get("error") or "")
        if error in {"not_found"}:
            return 404
        if error in {"memory_core_unavailable", "brain_unavailable"}:
            return 503
        if error in {"empty_content", "invalid_id"}:
            return 400
        return 400

    def _get_plugin_manager(self):
        return getattr(self.app_ref, "plugin_manager", None)

    def _request_work_session_status_refresh(self) -> None:
        app = self.app_ref
        ui = getattr(app, "qt_ui", None) if app is not None else None
        if ui is None:
            return
        refresh_request = getattr(ui, "request_work_session_status_refresh", None)
        if callable(refresh_request):
            refresh_request()
            return
        refresh = getattr(ui, "refresh_work_session_status", None)
        if callable(refresh):
            refresh()

    def _build_runtime_status(self) -> Dict[str, Any]:
        app = self.app_ref
        sensor = getattr(app, "screen_sensor", None) if app is not None else None

        work_session: Dict[str, Any] = {}
        latest_rust_event: Dict[str, Any] = {}
        if sensor is not None and hasattr(sensor, "get_current_work_session"):
            try:
                data = sensor.get_current_work_session()
                work_session = data if isinstance(data, dict) else {}
            except Exception as exc:
                work_session = {"error": str(exc)}
        if sensor is not None and hasattr(sensor, "_recent_rust_events"):
            try:
                events = sensor._recent_rust_events(limit=1)
                if events:
                    latest = events[0]
                    latest_rust_event = latest if isinstance(latest, dict) else {}
            except Exception as exc:
                latest_rust_event = {"error": str(exc)}

        return {
            "screen_sensor": {
                "bound": sensor is not None,
                "use_rust_events_only": bool(
                    getattr(sensor, "use_rust_events_only", False)
                )
                if sensor is not None
                else False,
            },
            "work_session": work_session,
            "latest_rust_event": latest_rust_event,
        }

    def _get_event_logger_path(self, root: str) -> Path:
        return Path(self._join_path(root, "data", "events.sqlite"))

    @staticmethod
    def _extract_expression_id(name: str, file_name: str) -> Optional[int]:
        for text in [name or "", file_name or ""]:
            cleaned = str(text or "").strip().replace("\\", "/")
            cleaned = cleaned.rsplit("/", 1)[-1]
            cleaned = re.sub(r"\.exp\d+\.json$", "", cleaned, flags=re.IGNORECASE)
            cleaned = re.sub(r"\.[a-z0-9]+$", "", cleaned, flags=re.IGNORECASE)
            nums = re.findall(r"\d+", cleaned)
            if nums:
                try:
                    return int(nums[-1])
                except Exception:
                    pass
        return None

    @staticmethod
    def _normalize_motion_name(raw_motion_name: str) -> str:
        name = str(raw_motion_name or "").strip()
        if not name:
            return ""
        if ":" in name:
            return name
        return f"Motion:{name}"

    @staticmethod
    def _motion_name_from_file(file_name: str) -> str:
        raw = str(file_name or "").replace("\\", "/").strip()
        if not raw:
            return ""
        name = raw.rsplit("/", 1)[-1]
        name = re.sub(r"\.motion3\.json$", "", name, flags=re.IGNORECASE)
        name = re.sub(r"\.mtn$", "", name, flags=re.IGNORECASE)
        name = re.sub(r"\.[a-z0-9]+$", "", name, flags=re.IGNORECASE)
        return name.strip()

    @staticmethod
    def _is_generic_motion_group(group_name: str) -> bool:
        group = str(group_name or "").strip().lower()
        return group in {"", "motion", "motions", "idle", "tapbody"}

    @staticmethod
    def _iter_motion_groups(raw_motion_refs: Any):
        if isinstance(raw_motion_refs, dict):
            for group_name, items in raw_motion_refs.items():
                if isinstance(items, list):
                    yield str(group_name), items
            return
        if isinstance(raw_motion_refs, list):
            yield "Motion", raw_motion_refs

    def _parse_model_meta(self, model_path: str) -> Dict[str, Any]:
        motions = []
        expressions = []
        if not model_path:
            return {"motions": motions, "expressions": expressions}

        try:
            path = str(model_path).replace("\\", "/")
            abs_path = Path(path)
            if not abs_path.is_absolute():
                root = self._find_backend_root(os.getcwd())
                if root:
                    abs_path = Path(root) / path
            with abs_path.open("r", encoding="utf-8") as f:
                data = json.load(f)

            refs = data.get("FileReferences", {}) if isinstance(data, dict) else {}
            motion_refs = refs.get("Motions", {}) if isinstance(refs, dict) else {}
            for group_name, motion_items in self._iter_motion_groups(motion_refs):
                for idx, item in enumerate(motion_items):
                    if not isinstance(item, dict):
                        continue
                    file_name = str(item.get("File") or item.get("file") or "").strip()
                    raw_name = (
                        item.get("Name")
                        or item.get("name")
                        or item.get("mtn")
                        or self._motion_name_from_file(file_name)
                    )
                    motion_name = (
                        str(raw_name).strip() if raw_name else f"{group_name}:{idx}"
                    )
                    motion_name = self._normalize_motion_name(motion_name)
                    motions.append(
                        {
                            "name": motion_name,
                            "group": str(group_name),
                            "index": int(idx),
                        }
                    )

            if not motions and isinstance(data, dict):
                legacy_motions = data.get("motions", {})
                for group_name, motion_items in self._iter_motion_groups(legacy_motions):
                    for idx, item in enumerate(motion_items):
                        if isinstance(item, dict):
                            file_name = str(item.get("file") or item.get("File") or "").strip()
                            raw_name = item.get("name") or item.get("Name") or item.get("mtn")
                            if not raw_name and not self._is_generic_motion_group(group_name):
                                raw_name = group_name
                            if not raw_name:
                                raw_name = self._motion_name_from_file(file_name)
                        else:
                            raw_name = (
                                group_name
                                if not self._is_generic_motion_group(group_name)
                                else self._motion_name_from_file(str(item)) or str(item)
                            )
                        motion_name = (
                            str(raw_name).strip() if raw_name else f"{group_name}:{idx}"
                        )
                        motions.append(
                            {
                                "name": motion_name,
                                "group": str(group_name),
                                "index": int(idx),
                            }
                        )

            expr_items = refs.get("Expressions", []) if isinstance(refs, dict) else []
            if isinstance(expr_items, list):
                for idx, item in enumerate(expr_items):
                    if not isinstance(item, dict):
                        continue
                    name = str(item.get("Name") or item.get("name") or "").strip()
                    file_name = str(item.get("File") or item.get("file") or "").strip()
                    exp_id = int(idx)
                    label = name or file_name or f"exp_{idx}"
                    expressions.append(
                        {
                            "label": label,
                            "name": name,
                            "file": file_name,
                            "exp_id": exp_id,
                        }
                    )
        except Exception:
            pass

        return {"motions": motions, "expressions": expressions}

    def _get_character_costume_meta(
        self, root: str, character_id: str, costume_name: str
    ) -> Dict[str, Any]:
        characters = self._load_json(
            Path(self._build_paths(root)["characters"]),
            {"active_id": "", "characters": {}},
        )
        char_map = characters.get("characters") or {}
        active_id = str(characters.get("active_id") or "")
        target_id = str(character_id or active_id or "").strip()
        char = char_map.get(target_id) or {}
        costumes = char.get("costumes") or {}
        target_costume = str(costume_name or char.get("current_costume") or "").strip()
        costume = costumes.get(target_costume) or {}

        path = ""
        if isinstance(costume, dict):
            path = str(costume.get("path") or "")
            emotion_map = costume.get("emotion_map") or {}
        else:
            path = str(costume or "")
            emotion_map = {}

        meta = self._parse_model_meta(path)
        default_emotion_keys = []
        default_emotion_map = {}
        try:
            character_module = self._load_local_module(
                "gui_http_character_manager_meta", ["modules", "character_manager.py"]
            )
            default_emotion_keys = list(
                getattr(character_module, "DEFAULT_EMOTION_KEYS", []) or []
            )
        except Exception:
            default_emotion_keys = []

        try:
            config_module = self._load_local_module(
                "gui_http_config_meta", ["config.py"]
            )
            default_emotion_map = getattr(config_module, "EMO_TO_LIVE2D", {}) or {}
        except Exception:
            default_emotion_map = {}

        return {
            "character_id": target_id,
            "active_id": active_id,
            "costume": target_costume,
            "path": path,
            "emotion_map": emotion_map if isinstance(emotion_map, dict) else {},
            "default_emotion_keys": default_emotion_keys,
            "default_emotion_map": default_emotion_map
            if isinstance(default_emotion_map, dict)
            else {},
            "motions": meta.get("motions") or [],
            "expressions": meta.get("expressions") or [],
        }

    def _load_local_module(self, name: str, relative_parts: list[str]):
        import importlib.util

        module_path = Path(__file__).resolve().parent.parent.joinpath(*relative_parts)
        spec = importlib.util.spec_from_file_location(name, module_path)
        if spec is None or spec.loader is None:
            raise ImportError(name)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module

    @staticmethod
    def _sanitize_plugin_item(item: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "trigger": str(item.get("trigger") or ""),
            "name": str(item.get("name") or item.get("trigger") or ""),
            "type": str(item.get("type") or "react"),
            "description": str(item.get("description") or ""),
            "enabled": bool(item.get("enabled", True)),
            "version": str(item.get("version") or ""),
            "author": str(item.get("author") or ""),
            "access_control": item.get("access_control") or {},
            "access_summary": str(item.get("access_summary") or ""),
        }

    def _list_plugins(self) -> list[Dict[str, Any]]:
        manager = self._get_plugin_manager()
        if manager is None or not hasattr(manager, "get_all_plugins_info"):
            return []
        try:
            rows = manager.get_all_plugins_info() or []
            return [
                self._sanitize_plugin_item(item)
                for item in rows
                if isinstance(item, dict)
            ]
        except Exception:
            return []

    def _serialize_plugin_config(self, trigger: str) -> Dict[str, Any]:
        manager = self._get_plugin_manager()
        if manager is None or not hasattr(manager, "get_plugin_config"):
            return {}
        try:
            config = manager.get_plugin_config(trigger) or {}
            if not isinstance(config, dict):
                return {}
            return self._mask_secrets(config)
        except Exception:
            return {}

    def _serialize_plugin_config_schema(self, trigger: str) -> Dict[str, Any]:
        manager = self._get_plugin_manager()
        if manager is None or not hasattr(manager, "get_plugin_config_schema"):
            return {}
        try:
            schema = manager.get_plugin_config_schema(trigger) or {}
            return schema if isinstance(schema, dict) else {}
        except Exception:
            return {}

    def _save_plugin_config(
        self, trigger: str, payload: Dict[str, Any]
    ) -> tuple[bool, str]:
        manager = self._get_plugin_manager()
        if manager is None or not hasattr(manager, "save_plugin_config"):
            return False, "plugin_manager_unavailable"
        trigger = str(trigger or "").strip()
        if not trigger:
            return False, "invalid_trigger"
        current = (
            manager.get_plugin_config(trigger)
            if hasattr(manager, "get_plugin_config")
            else {}
        )
        if not isinstance(current, dict):
            current = {}
        merged = self._restore_masked_secrets(payload, current)
        try:
            ok = bool(manager.save_plugin_config(trigger, merged))
            return (True, "") if ok else (False, "save_failed")
        except Exception as exc:
            return False, str(exc)

    def _set_plugin_enabled(self, trigger: str, enabled: bool) -> tuple[bool, str]:
        manager = self._get_plugin_manager()
        if manager is None:
            return False, "plugin_manager_unavailable"
        trigger = str(trigger or "").strip()
        if not trigger:
            return False, "invalid_trigger"
        try:
            ok = (
                manager.enable_plugin(trigger)
                if enabled
                else manager.disable_plugin(trigger)
            )
            if not ok:
                return False, "plugin_not_found"
            return True, ""
        except Exception as exc:
            return False, str(exc)

    def _reload_plugin(self, trigger: str) -> tuple[bool, str]:
        manager = self._get_plugin_manager()
        if manager is None or not hasattr(manager, "reload_plugin"):
            return False, "plugin_manager_unavailable"
        trigger = str(trigger or "").strip()
        if not trigger:
            return False, "invalid_trigger"
        try:
            ok = bool(manager.reload_plugin(trigger))
            return (True, "") if ok else (False, "reload_failed")
        except Exception as exc:
            return False, str(exc)

    def _scan_dependencies(self, root: str) -> Dict[str, Any]:
        try:
            dep_module = self._load_local_module(
                "gui_http_dependency_check", ["modules", "dependency_check.py"]
            )

            plugins_dir = self._join_path(root, "plugins")
            rows = dep_module.scan_missing_dependencies(plugins_dir)
            return {
                "rows": rows,
                "install_command": dep_module.build_install_command(rows),
            }
        except Exception as exc:
            return {
                "rows": [],
                "install_command": "",
                "error": str(exc),
            }

    def _install_dependencies(self, root: str, confirm: bool = False) -> Dict[str, Any]:
        try:
            dep_module = self._load_local_module(
                "gui_http_dependency_check_install", ["modules", "dependency_check.py"]
            )

            plugins_dir = self._join_path(root, "plugins")
            rows = dep_module.scan_missing_dependencies(plugins_dir)
            install_command = dep_module.build_install_command(rows)
            if not rows:
                return {
                    "ok": "1",
                    "message": "no missing dependencies",
                    "rows": rows,
                    "install_command": install_command,
                }
            if not confirm:
                return {
                    "ok": "0",
                    "code": "confirmation_required",
                    "message": "dependency installation requires confirm=true",
                    "rows": rows,
                    "install_command": install_command,
                }
            return dep_module.install_missing(rows, timeout=900)
        except Exception as exc:
            return {"ok": "0", "message": str(exc)}

    def _list_memory_items(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        store = self._get_memory_store()
        if store is None or not hasattr(store, "list_items"):
            return {"items": [], "error": "memory_store_unavailable"}
        try:
            items = store.list_items(
                status=str(payload.get("status") or "active"),
                type_=str(payload.get("type") or ""),
                query=str(payload.get("query") or ""),
                limit=int(payload.get("limit") or 200),
                offset=int(payload.get("offset") or 0),
            )
            return {"items": items}
        except Exception as exc:
            return {"items": [], "error": str(exc)}

    def _list_memory_episodes(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        store = self._get_memory_store()
        if store is None or not hasattr(store, "list_episodes"):
            return {"episodes": [], "error": "memory_store_unavailable"}
        try:
            rows = store.list_episodes(
                status=str(payload.get("status") or "active"),
                query=str(payload.get("query") or ""),
                limit=int(payload.get("limit") or 50),
                offset=int(payload.get("offset") or 0),
            )
            return {"episodes": rows}
        except Exception as exc:
            return {"episodes": [], "error": str(exc)}

    def _list_qq_profiles(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        store = self._get_memory_store()
        if store is None or not hasattr(store, "list_qq_user_profiles"):
            return {"profiles": [], "error": "memory_store_unavailable"}
        try:
            rows = store.list_qq_user_profiles(
                query=str(payload.get("query") or ""),
                limit=int(payload.get("limit") or 300),
                owner_only=bool(payload.get("owner_only")),
            )
            return {"profiles": rows}
        except Exception as exc:
            return {"profiles": [], "error": str(exc)}

    def _upsert_qq_profile(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        store = self._get_memory_store()
        if store is None or not hasattr(store, "upsert_qq_user_profile"):
            return {"error": "memory_store_unavailable"}
        try:
            profile = payload.get("profile") or payload
            result = store.upsert_qq_user_profile(profile)
            if not result:
                return {"error": "save_failed"}
            return {"profile": result}
        except Exception as exc:
            return {"error": str(exc)}

    def _delete_qq_profile(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        store = self._get_memory_store()
        if store is None or not hasattr(store, "delete_qq_user_profile"):
            return {"error": "memory_store_unavailable"}
        user_id = str(payload.get("user_id") or "").strip()
        if not user_id:
            return {"error": "invalid_user_id"}
        try:
            ok = bool(store.delete_qq_user_profile(user_id))
            return {
                "ok": ok,
                "user_id": user_id,
                "error": "delete_failed" if not ok else "",
            }
        except Exception as exc:
            return {"error": str(exc)}

    def _list_recent_events(self, root: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        db_path = self._get_event_logger_path(root)
        if not db_path.exists():
            return {"events": []}
        limit = max(1, min(200, int(payload.get("limit") or 50)))
        try:
            import sqlite3

            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT ts_iso, type, name, payload_json FROM events ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            conn.close()
            events = []
            for row in rows:
                events.append(
                    {
                        "ts_iso": row["ts_iso"],
                        "type": row["type"],
                        "name": row["name"],
                        "payload": json.loads(row["payload_json"] or "{}"),
                    }
                )
            return {"events": events}
        except Exception as exc:
            return {"events": [], "error": str(exc)}

    def _list_outbound_records(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        gateway = getattr(self.app_ref, "chat_gateway", None) if self.app_ref is not None else None
        tracker = getattr(gateway, "outbound_tracker", None)
        if tracker is None or not hasattr(tracker, "recent"):
            return {"records": [], "error": "outbound_tracker_unavailable"}
        limit = max(1, min(500, int(payload.get("limit") or 50)))
        try:
            return {"records": tracker.recent(limit=limit)}
        except Exception as exc:
            return {"records": [], "error": str(exc)}

    def _list_reply_effects(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        chat_service = (
            getattr(self.app_ref, "chat_service", None) if self.app_ref is not None else None
        )
        brain = getattr(chat_service, "brain", None)
        memory_core = getattr(brain, "memory_core", None)
        if memory_core is None:
            return {"records": [], "stats": {}, "error": "memory_core_unavailable"}
        limit = max(1, min(500, int(payload.get("limit") or 50)))
        session_id = str(payload.get("session_id") or "").strip()
        try:
            return {
                "records": memory_core.list_feedback(limit=limit, session_id=session_id),
                "stats": memory_core.feedback_stats(limit=max(limit, 200), session_id=session_id),
            }
        except Exception as exc:
            return {"records": [], "stats": {}, "error": str(exc)}

    def _get_deferred_tool_stats(self) -> Dict[str, Any]:
        manager = self._get_plugin_manager()
        if manager is None or not hasattr(manager, "get_deferred_tool_stats"):
            return {"stats": {}, "error": "plugin_manager_unavailable"}
        try:
            return {"stats": manager.get_deferred_tool_stats()}
        except Exception as exc:
            return {"stats": {}, "error": str(exc)}

    def _delete_memory_item(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        store = self._get_memory_store()
        if store is None or not hasattr(store, "delete_item"):
            return {"error": "memory_store_unavailable"}
        item_id = str(payload.get("id") or "").strip()
        if not item_id:
            return {"error": "invalid_id"}
        try:
            ok = bool(store.delete_item(item_id))
            if not ok:
                return {"error": "not_found"}
            return {"id": item_id, "deleted": True}
        except Exception as exc:
            return {"error": str(exc)}

    def _upsert_memory_item(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        store = self._get_memory_store()
        if store is None or not hasattr(store, "upsert_item"):
            return {"error": "memory_store_unavailable"}
        try:
            item = payload.get("item") or payload
            item_id = store.upsert_item(item)
            return {"item": store.get_item(item_id)}
        except Exception as exc:
            return {"error": str(exc)}

    def _delete_episode(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        store = self._get_memory_store()
        if store is None or not hasattr(store, "delete_episode"):
            return {"error": "memory_store_unavailable"}
        episode_id = str(payload.get("id") or "").strip()
        if not episode_id:
            return {"error": "invalid_id"}
        try:
            ok = bool(store.delete_episode(episode_id))
            if not ok:
                return {"error": "not_found"}
            return {"id": episode_id, "deleted": True}
        except Exception as exc:
            return {"error": str(exc)}

    def _upsert_episode(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        store = self._get_memory_store()
        if store is None or not hasattr(store, "upsert_episode"):
            return {"error": "memory_store_unavailable"}
        try:
            item = payload.get("episode") or payload
            episode_id = store.upsert_episode(item)
            return {"episode": store.get_episode(episode_id)}
        except Exception as exc:
            return {"error": str(exc)}

    def _list_transcript(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        store = self._get_memory_store()
        if store is None or not hasattr(store, "list_transcript"):
            return {"rows": [], "error": "memory_store_unavailable"}
        try:
            role = str(payload.get("role") or "").strip()
            rows = store.list_transcript(
                role=None if not role or role == "(all)" else role,
                query=str(payload.get("query") or ""),
                limit=int(payload.get("limit") or 200),
                offset=int(payload.get("offset") or 0),
            )
            return {"rows": rows}
        except Exception as exc:
            return {"rows": [], "error": str(exc)}

    def _delete_transcript(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        store = self._get_memory_store()
        if store is None:
            return {"error": "memory_store_unavailable"}
        tr_id = payload.get("id")
        if tr_id in (None, ""):
            return {"error": "invalid_id"}
        try:
            ok = (
                bool(store.delete_transcript(int(tr_id)))
                if hasattr(store, "delete_transcript")
                else False
            )
            return {
                "ok": ok,
                "id": int(tr_id),
                "error": "delete_failed" if not ok else "",
            }
        except Exception as exc:
            return {"error": str(exc)}

    def _clear_transcript(self) -> Dict[str, Any]:
        store = self._get_memory_store()
        if store is None or not hasattr(store, "clear_transcript"):
            return {"error": "memory_store_unavailable"}
        try:
            count = int(store.clear_transcript())
            if count < 0:
                return {"error": "clear_failed"}
            return {"ok": True, "deleted": count}
        except Exception as exc:
            return {"error": str(exc)}

    def _collect_dashboard(self, root: str) -> Dict[str, Any]:
        plugin_rows = self._list_plugins()
        dep_data = self._scan_dependencies(root)
        memory_data = self._list_memory_items({"status": "active", "limit": 12})
        episode_data = self._list_memory_episodes({"status": "active", "limit": 8})
        qq_data = self._list_qq_profiles({"limit": 50})
        event_data = self._list_recent_events(root, {"limit": 20})
        transcript_data = self._list_transcript({"limit": 80})
        return {
            "plugins": plugin_rows,
            "dependencies": dep_data,
            "memory": memory_data,
            "episodes": episode_data,
            "qqProfiles": qq_data,
            "events": event_data,
            "transcript": transcript_data,
        }

    def _load_data_snapshot(self, root: str) -> Dict[str, Any]:
        paths = self._build_paths(root)
        custom_models = self._load_json(
            Path(paths["customModels"]), {"models": {}, "router": {}, "providers": {}}
        )
        runtime_settings = self._load_json(Path(paths["runtimeSettings"]), {})
        characters = self._load_json(
            Path(paths["characters"]), {"active_id": "", "characters": {}}
        )
        mcp_config = self._load_json(Path(paths["mcpConfig"]), {})
        return {
            "root": root,
            "paths": paths,
            "customModels": self._mask_secrets(custom_models),
            "runtimeSettings": self._mask_secrets(runtime_settings),
            "characters": self._mask_secrets(characters),
            "mcpConfig": self._mask_secrets(mcp_config),
        }


    def _models_service(self, root: str):
        from services.gui_api.models_service import ModelsCatalogService

        paths = self._build_paths(root)
        return ModelsCatalogService(Path(paths["customModels"]))

    async def _handle_models_get(self, _request: web.Request) -> web.Response:
        root = self._find_backend_root(os.getcwd())
        if not root:
            return self._json_response(
                {"ok": False, "error": "backend_not_found"}, status=404
            )
        data = self._models_service(root).list_catalog()
        return self._json_response({"ok": True, "data": data})

    async def _handle_models_upsert(self, request: web.Request) -> web.Response:
        root = self._find_backend_root(os.getcwd())
        if not root:
            return self._json_response(
                {"ok": False, "error": "backend_not_found"}, status=404
            )
        payload = await self._read_payload(request)
        result = self._models_service(root).upsert_model(payload)
        if not result.get("ok"):
            code = 400 if result.get("error") != "write_failed" else 500
            return self._json_response(result, status=code)
        self._reload_custom_models()
        return self._json_response(result)

    async def _handle_models_delete(self, request: web.Request) -> web.Response:
        root = self._find_backend_root(os.getcwd())
        if not root:
            return self._json_response(
                {"ok": False, "error": "backend_not_found"}, status=404
            )
        payload = await self._read_payload(request)
        model_id = str(payload.get("id") or payload.get("model_id") or "").strip()
        result = self._models_service(root).delete_model(model_id)
        if not result.get("ok"):
            code = 404 if result.get("error") == "not_found" else 400
            if result.get("error") == "write_failed":
                code = 500
            return self._json_response(result, status=code)
        self._reload_custom_models()
        return self._json_response(result)

    async def _handle_providers_upsert(self, request: web.Request) -> web.Response:
        root = self._find_backend_root(os.getcwd())
        if not root:
            return self._json_response(
                {"ok": False, "error": "backend_not_found"}, status=404
            )
        payload = await self._read_payload(request)
        result = self._models_service(root).upsert_provider(payload)
        if not result.get("ok"):
            code = 400 if result.get("error") != "write_failed" else 500
            return self._json_response(result, status=code)
        self._reload_custom_models()
        return self._json_response(result)

    async def _handle_providers_delete(self, request: web.Request) -> web.Response:
        root = self._find_backend_root(os.getcwd())
        if not root:
            return self._json_response(
                {"ok": False, "error": "backend_not_found"}, status=404
            )
        payload = await self._read_payload(request)
        provider_id = str(payload.get("id") or payload.get("provider_id") or "").strip()
        result = self._models_service(root).delete_provider(provider_id)
        if not result.get("ok"):
            code = 404 if result.get("error") == "not_found" else 400
            if result.get("error") == "write_failed":
                code = 500
            return self._json_response(result, status=code)
        self._reload_custom_models()
        return self._json_response(result)

    async def _handle_models_router_save(self, request: web.Request) -> web.Response:
        root = self._find_backend_root(os.getcwd())
        if not root:
            return self._json_response(
                {"ok": False, "error": "backend_not_found"}, status=404
            )
        payload = await self._read_payload(request)
        router = payload.get("router") if isinstance(payload.get("router"), dict) else payload
        result = self._models_service(root).save_router(router if isinstance(router, dict) else {})
        if not result.get("ok"):
            code = 400 if result.get("error") != "write_failed" else 500
            return self._json_response(result, status=code)
        self._reload_custom_models()
        return self._json_response(result)

    def _reload_custom_models(self) -> None:
        if self.app_ref is None:
            return
        try:
            runtime_config = self._load_local_module(
                "gui_http_runtime_config", ["config.py"]
            )
            runtime_config.load_custom_models(force=True)
        except Exception:
            pass

    def _characters_service(self, root: str):
        from services.gui_api.characters_service import CharactersService

        paths = self._build_paths(root)
        return CharactersService(Path(paths["characters"]))

    def _reload_characters(self) -> None:
        if self.app_ref is None:
            return
        try:
            character_module = self._load_local_module(
                "gui_http_character_manager",
                ["modules", "character_manager.py"],
            )
            character_manager = character_module.character_manager
            character_manager.load()
        except Exception:
            pass

    def _character_write_status(self, result: Dict[str, Any]) -> int:
        error = str(result.get("error") or "")
        if error == "write_failed":
            return 500
        if error in {"not_found", "costume_not_found"}:
            return 404
        if error in {
            "cannot_delete_active",
            "cannot_delete_last_costume",
            "invalid_costume",
        }:
            return 400
        return 400

    async def _handle_characters_list(self, _request: web.Request) -> web.Response:
        root = self._find_backend_root(os.getcwd())
        if not root:
            return self._json_response(
                {"ok": False, "error": "backend_not_found"}, status=404
            )
        data = self._characters_service(root).list_characters()
        return self._json_response({"ok": True, "data": data})

    async def _handle_characters_get(self, request: web.Request) -> web.Response:
        root = self._find_backend_root(os.getcwd())
        if not root:
            return self._json_response(
                {"ok": False, "error": "backend_not_found"}, status=404
            )
        character_id = str(request.query.get("id") or request.query.get("character_id") or "").strip()
        result = self._characters_service(root).get_character(character_id)
        if not result.get("ok"):
            return self._json_response(result, status=self._character_write_status(result))
        return self._json_response(result)

    async def _handle_characters_upsert(self, request: web.Request) -> web.Response:
        root = self._find_backend_root(os.getcwd())
        if not root:
            return self._json_response(
                {"ok": False, "error": "backend_not_found"}, status=404
            )
        payload = await self._read_payload(request)
        result = self._characters_service(root).upsert_character(payload)
        if not result.get("ok"):
            return self._json_response(result, status=self._character_write_status(result))
        self._reload_characters()
        return self._json_response(result)

    async def _handle_characters_delete(self, request: web.Request) -> web.Response:
        root = self._find_backend_root(os.getcwd())
        if not root:
            return self._json_response(
                {"ok": False, "error": "backend_not_found"}, status=404
            )
        payload = await self._read_payload(request)
        character_id = str(payload.get("id") or payload.get("character_id") or "").strip()
        result = self._characters_service(root).delete_character(character_id)
        if not result.get("ok"):
            return self._json_response(result, status=self._character_write_status(result))
        self._reload_characters()
        return self._json_response(result)

    async def _handle_characters_activate(self, request: web.Request) -> web.Response:
        root = self._find_backend_root(os.getcwd())
        if not root:
            return self._json_response(
                {"ok": False, "error": "backend_not_found"}, status=404
            )
        payload = await self._read_payload(request)
        character_id = str(payload.get("id") or payload.get("character_id") or "").strip()
        result = self._characters_service(root).activate_character(character_id)
        if not result.get("ok"):
            return self._json_response(result, status=self._character_write_status(result))
        self._reload_characters()
        return self._json_response(result)

    async def _handle_characters_costume_upsert(self, request: web.Request) -> web.Response:
        root = self._find_backend_root(os.getcwd())
        if not root:
            return self._json_response(
                {"ok": False, "error": "backend_not_found"}, status=404
            )
        payload = await self._read_payload(request)
        character_id = str(payload.get("character_id") or payload.get("id") or "").strip()
        result = self._characters_service(root).upsert_costume(character_id, payload)
        if not result.get("ok"):
            return self._json_response(result, status=self._character_write_status(result))
        self._reload_characters()
        return self._json_response(result)

    async def _handle_characters_costume_delete(self, request: web.Request) -> web.Response:
        root = self._find_backend_root(os.getcwd())
        if not root:
            return self._json_response(
                {"ok": False, "error": "backend_not_found"}, status=404
            )
        payload = await self._read_payload(request)
        character_id = str(payload.get("character_id") or payload.get("id") or "").strip()
        costume_name = str(payload.get("name") or payload.get("costume") or "").strip()
        result = self._characters_service(root).delete_costume(character_id, costume_name)
        if not result.get("ok"):
            return self._json_response(result, status=self._character_write_status(result))
        self._reload_characters()
        return self._json_response(result)

    async def _handle_characters_costume_wear(self, request: web.Request) -> web.Response:
        root = self._find_backend_root(os.getcwd())
        if not root:
            return self._json_response(
                {"ok": False, "error": "backend_not_found"}, status=404
            )
        payload = await self._read_payload(request)
        character_id = str(payload.get("character_id") or payload.get("id") or "").strip()
        costume_name = str(payload.get("name") or payload.get("costume") or "").strip()
        result = self._characters_service(root).set_current_costume(character_id, costume_name)
        if not result.get("ok"):
            return self._json_response(result, status=self._character_write_status(result))
        self._reload_characters()
        return self._json_response(result)

    async def _handle_health(self, _request: web.Request) -> web.Response:
        return self._json_response({"ok": True, "service": "gui_http"})

    async def _handle_runtime_status(self, _request: web.Request) -> web.Response:
        return self._json_response({"ok": True, "data": self._build_runtime_status()})

    async def _handle_runtime_control(self, request: web.Request) -> web.Response:
        payload = await self._read_payload(request)
        action = str(payload.get("action") or "").strip().lower()
        if action not in {"shutdown", "restart"}:
            return self._json_response(
                {"ok": False, "error": "invalid_action"}, status=400
            )
        control = getattr(self.app_ref, "request_runtime_control", None)
        if not callable(control):
            return self._json_response(
                {"ok": False, "error": "runtime_control_unavailable"}, status=409
            )
        ok, error = control(action)
        if not ok:
            return self._json_response(
                {"ok": False, "error": str(error or "runtime_control_failed")},
                status=409,
            )
        return self._json_response({"ok": True, "action": action})

    async def _handle_settings_get(self, _request: web.Request) -> web.Response:
        root = self._find_backend_root(os.getcwd())
        if not root:
            return self._json_response(
                {"ok": False, "error": "backend_not_found"}, status=404
            )
        snapshot = self._load_data_snapshot(root)
        return self._json_response({"ok": True, "data": snapshot})

    async def _handle_dashboard_get(self, _request: web.Request) -> web.Response:
        root = self._find_backend_root(os.getcwd())
        if not root:
            return self._json_response(
                {"ok": False, "error": "backend_not_found"}, status=404
            )
        return self._json_response({"ok": True, "data": self._collect_dashboard(root)})

    async def _read_payload(self, request: web.Request) -> Dict[str, Any]:
        try:
            payload = await request.json(loads=json.loads)
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    async def _handle_settings_save(
        self, request: web.Request, section: str
    ) -> web.Response:
        payload = await self._read_payload(request)
        if not payload:
            return self._json_response(
                {"ok": False, "error": "empty_payload"}, status=400
            )

        root = self._find_backend_root(os.getcwd())
        if not root:
            return self._json_response(
                {"ok": False, "error": "backend_not_found"}, status=404
            )

        paths = self._build_paths(root)
        target = paths.get(section)
        if not target:
            return self._json_response(
                {"ok": False, "error": "invalid_section"}, status=400
            )

        current_payload = self._load_json(Path(target), {})
        payload = self._restore_masked_secrets(payload, current_payload)

        ok = self._save_json(Path(target), payload)
        if not ok:
            return self._json_response(
                {"ok": False, "error": "write_failed"}, status=500
            )

        if self.app_ref is not None:
            try:
                if section == "customModels":
                    runtime_config = self._load_local_module(
                        "gui_http_runtime_config", ["config.py"]
                    )
                    runtime_config.load_custom_models(force=True)
                elif section == "runtimeSettings":
                    self.app_ref.apply_external_settings()
                elif section == "characters":
                    character_module = self._load_local_module(
                        "gui_http_character_manager",
                        ["modules", "character_manager.py"],
                    )
                    character_manager = character_module.character_manager
                    character_manager.load()
            except Exception:
                pass

        snapshot = self._load_data_snapshot(root)
        return self._json_response({"ok": True, "data": snapshot})

    async def _handle_character_costume_meta(
        self, request: web.Request
    ) -> web.Response:
        root = self._find_backend_root(os.getcwd())
        if not root:
            return self._json_response(
                {"ok": False, "error": "backend_not_found"}, status=404
            )
        character_id = str(request.query.get("character_id") or "").strip()
        costume_name = str(request.query.get("costume") or "").strip()
        data = self._get_character_costume_meta(root, character_id, costume_name)
        return self._json_response({"ok": True, "data": data})

    async def _handle_character_preview_motion(
        self, request: web.Request
    ) -> web.Response:
        payload = await self._read_payload(request)
        motion_name = str(
            payload.get("motion_name") or payload.get("name") or ""
        ).strip()
        motion_type = int(payload.get("motion_type") or payload.get("type") or 0)
        if not motion_name:
            return self._json_response(
                {"ok": False, "error": "invalid_motion"}, status=400
            )
        try:
            if self.app_ref is not None and hasattr(
                self.app_ref, "on_gui_preview_motion"
            ):
                self.app_ref.on_gui_preview_motion(motion_name, motion_type)
            return self._json_response(
                {
                    "ok": True,
                    "data": {"motion_name": motion_name, "motion_type": motion_type},
                }
            )
        except Exception as exc:
            return self._json_response({"ok": False, "error": str(exc)}, status=400)

    async def _handle_character_preview_expression(
        self, request: web.Request
    ) -> web.Response:
        payload = await self._read_payload(request)
        exp_id = payload.get("exp_id")
        if exp_id is None:
            return self._json_response(
                {"ok": False, "error": "invalid_expression"}, status=400
            )
        try:
            exp_id_int = int(exp_id)
            if self.app_ref is not None and hasattr(
                self.app_ref, "on_gui_preview_expression"
            ):
                self.app_ref.on_gui_preview_expression(exp_id_int)
            return self._json_response({"ok": True, "data": {"exp_id": exp_id_int}})
        except Exception as exc:
            return self._json_response({"ok": False, "error": str(exc)}, status=400)

    async def _handle_plugin_list(self, _request: web.Request) -> web.Response:
        return self._json_response(
            {"ok": True, "data": {"plugins": self._list_plugins()}}
        )

    async def _handle_plugin_toggle(self, request: web.Request) -> web.Response:
        payload = await self._read_payload(request)
        trigger = str(payload.get("trigger") or "").strip()
        enabled = bool(payload.get("enabled"))
        ok, error = self._set_plugin_enabled(trigger, enabled)
        status = 200 if ok else 400
        return self._json_response(
            {"ok": ok, "error": error, "data": {"plugins": self._list_plugins()}},
            status=status,
        )

    async def _handle_plugin_reload(self, request: web.Request) -> web.Response:
        payload = await self._read_payload(request)
        trigger = str(payload.get("trigger") or "").strip()
        ok, error = self._reload_plugin(trigger)
        status = 200 if ok else 400
        return self._json_response(
            {"ok": ok, "error": error, "data": {"plugins": self._list_plugins()}},
            status=status,
        )

    async def _handle_plugin_config_get(self, request: web.Request) -> web.Response:
        trigger = str(request.query.get("trigger") or "").strip()
        if not trigger:
            return self._json_response(
                {"ok": False, "error": "invalid_trigger"}, status=400
            )
        data = self._serialize_plugin_config(trigger)
        schema = self._serialize_plugin_config_schema(trigger)
        return self._json_response({"ok": True, "data": {"config": data, "schema": schema}})

    async def _handle_plugin_config_schema_get(
        self, request: web.Request
    ) -> web.Response:
        trigger = str(request.query.get("trigger") or "").strip()
        if not trigger:
            return self._json_response(
                {"ok": False, "error": "invalid_trigger"}, status=400
            )
        schema = self._serialize_plugin_config_schema(trigger)
        return self._json_response({"ok": True, "data": {"schema": schema}})

    async def _handle_plugin_config_save(self, request: web.Request) -> web.Response:
        payload = await self._read_payload(request)
        trigger = str(payload.get("trigger") or "").strip()
        config = (
            payload.get("config")
            if isinstance(payload.get("config"), dict)
            else payload
        )
        if not isinstance(config, dict):
            config = {}
        ok, error = self._save_plugin_config(trigger, config)
        return self._json_response(
            {
                "ok": ok,
                "error": error,
                "data": {
                    "config": self._serialize_plugin_config(trigger),
                    "plugins": self._list_plugins(),
                },
            },
            status=200 if ok else 400,
        )

    async def _handle_dependency_scan(self, _request: web.Request) -> web.Response:
        root = self._find_backend_root(os.getcwd())
        if not root:
            return self._json_response(
                {"ok": False, "error": "backend_not_found"}, status=404
            )
        return self._json_response({"ok": True, "data": self._scan_dependencies(root)})

    async def _handle_dependency_install(self, request: web.Request) -> web.Response:
        root = self._find_backend_root(os.getcwd())
        if not root:
            return self._json_response(
                {"ok": False, "error": "backend_not_found"}, status=404
            )
        payload = await self._read_payload(request)
        confirm = bool(payload.get("confirm") or payload.get("confirmed"))
        result = self._install_dependencies(root, confirm=confirm)
        ok = str(result.get("ok", "0")) == "1"
        status = 200 if ok else 500
        if result.get("code") == "confirmation_required":
            status = 409
        return self._json_response(
            {"ok": ok, "data": result, "error": result.get("message", "")},
            status=status,
        )

    async def _handle_knowledge_list(self, _request: web.Request) -> web.Response:
        result = self._knowledge_gui_service().list_dirs()
        if not result.get("ok"):
            return self._json_response(result, status=self._knowledge_result_status(result))
        return self._json_response(result)

    async def _handle_knowledge_save_dirs(self, request: web.Request) -> web.Response:
        payload = await self._read_payload(request)
        dirs = payload.get("dirs") if isinstance(payload.get("dirs"), list) else payload
        result = self._knowledge_gui_service().save_dirs(dirs)
        if not result.get("ok"):
            return self._json_response(result, status=self._knowledge_result_status(result))
        return self._json_response(result)

    async def _handle_knowledge_stats(self, _request: web.Request) -> web.Response:
        result = self._knowledge_gui_service().stats()
        if not result.get("ok"):
            return self._json_response(result, status=self._knowledge_result_status(result))
        return self._json_response(result)

    async def _handle_knowledge_search(self, request: web.Request) -> web.Response:
        query = str(request.query.get("query") or request.query.get("q") or "").strip()
        if not query:
            payload = await self._read_payload(request)
            query = str(payload.get("query") or payload.get("q") or "").strip()
            limit = int(payload.get("limit") or 5)
        else:
            limit = int(request.query.get("limit") or 5)
        result = self._knowledge_gui_service().search(query, limit=limit)
        if not result.get("ok"):
            return self._json_response(result, status=self._knowledge_result_status(result))
        return self._json_response(result)

    async def _handle_knowledge_import(self, request: web.Request) -> web.Response:
        payload = await self._read_payload(request)
        path = str(payload.get("path") or "").strip()
        result = self._knowledge_gui_service().import_file(path)
        if not result.get("ok"):
            return self._json_response(result, status=self._knowledge_result_status(result))
        return self._json_response(result)

    async def _handle_knowledge_rebuild(self, _request: web.Request) -> web.Response:
        result = self._knowledge_gui_service().rebuild()
        if not result.get("ok"):
            return self._json_response(result, status=self._knowledge_result_status(result))
        return self._json_response(result)

    async def _handle_knowledge_delete_dirs(self, request: web.Request) -> web.Response:
        payload = await self._read_payload(request)
        dirs = payload.get("dirs") if isinstance(payload.get("dirs"), list) else []
        result = self._knowledge_gui_service().delete_by_dirs(dirs)
        if not result.get("ok"):
            return self._json_response(result, status=self._knowledge_result_status(result))
        return self._json_response(result)

    async def _handle_knowledge_learn(self, _request: web.Request) -> web.Response:
        result = self._knowledge_gui_service().learn_configured_dirs()
        if not result.get("ok"):
            return self._json_response(result, status=self._knowledge_result_status(result))
        return self._json_response(result)

    async def _handle_knowledge_create_doc(self, request: web.Request) -> web.Response:
        payload = await self._read_payload(request)
        result = self._knowledge_gui_service().create_doc(payload)
        if not result.get("ok"):
            return self._json_response(result, status=self._knowledge_result_status(result))
        return self._json_response(result)

    async def _handle_diary_list(self, request: web.Request) -> web.Response:
        result = self._diary_gui_service().list_diaries(
            query=str(request.query.get("query") or ""),
            limit=int(request.query.get("limit") or 500),
        )
        if not result.get("ok"):
            return self._json_response(result, status=self._diary_result_status(result))
        return self._json_response(result)

    async def _handle_diary_get(self, request: web.Request) -> web.Response:
        diary_id = str(request.query.get("id") or request.query.get("diary_id") or "").strip()
        result = self._diary_gui_service().get_diary(diary_id)
        if not result.get("ok"):
            return self._json_response(result, status=self._diary_result_status(result))
        return self._json_response(result)

    async def _handle_diary_upsert(self, request: web.Request) -> web.Response:
        payload = await self._read_payload(request)
        result = self._diary_gui_service().upsert_diary(payload)
        if not result.get("ok"):
            return self._json_response(result, status=self._diary_result_status(result))
        return self._json_response(result)

    async def _handle_diary_delete(self, request: web.Request) -> web.Response:
        payload = await self._read_payload(request)
        diary_id = str(payload.get("id") or payload.get("diary_id") or "").strip()
        result = self._diary_gui_service().delete_diary(diary_id)
        if not result.get("ok"):
            return self._json_response(result, status=self._diary_result_status(result))
        return self._json_response(result)

    async def _handle_diary_export(self, request: web.Request) -> web.Response:
        payload = await self._read_payload(request)
        ids = payload.get("ids") if isinstance(payload.get("ids"), list) else None
        result = self._diary_gui_service().export_markdown(
            query=str(payload.get("query") or ""),
            ids=ids,
            path=str(payload.get("path") or ""),
        )
        if not result.get("ok"):
            return self._json_response(result, status=self._diary_result_status(result))
        return self._json_response(result)

    async def _handle_memory_core_list(self, request: web.Request) -> web.Response:
        result = self._memory_gui_service().list_core_records(
            status=str(request.query.get("status") or "active"),
            person_id=str(request.query.get("person_id") or ""),
            category_id=str(request.query.get("category_id") or "all"),
            query=str(request.query.get("query") or ""),
            limit=int(request.query.get("limit") or 500),
        )
        if not result.get("ok"):
            return self._json_response(result, status=self._memory_result_status(result))
        return self._json_response(result)

    async def _handle_memory_core_get(self, request: web.Request) -> web.Response:
        record_id = str(request.query.get("id") or request.query.get("record_id") or "").strip()
        result = self._memory_gui_service().get_core_record(record_id)
        if not result.get("ok"):
            return self._json_response(result, status=self._memory_result_status(result))
        return self._json_response(result)

    async def _handle_memory_core_upsert(self, request: web.Request) -> web.Response:
        payload = await self._read_payload(request)
        result = self._memory_gui_service().upsert_core_record(payload)
        if not result.get("ok"):
            return self._json_response(result, status=self._memory_result_status(result))
        return self._json_response(result)

    async def _handle_memory_core_delete(self, request: web.Request) -> web.Response:
        payload = await self._read_payload(request)
        record_id = str(payload.get("id") or payload.get("record_id") or "").strip()
        result = self._memory_gui_service().delete_core_record(record_id)
        if not result.get("ok"):
            return self._json_response(result, status=self._memory_result_status(result))
        return self._json_response(result)

    async def _handle_memory_core_category(self, request: web.Request) -> web.Response:
        payload = await self._read_payload(request)
        record_id = str(payload.get("id") or payload.get("record_id") or "").strip()
        category_id = str(payload.get("category_id") or payload.get("category_override") or "")
        result = self._memory_gui_service().set_category_override(record_id, category_id)
        if not result.get("ok"):
            return self._json_response(result, status=self._memory_result_status(result))
        return self._json_response(result)

    async def _handle_memory_vector_status(self, _request: web.Request) -> web.Response:
        result = self._memory_gui_service().vector_status()
        if not result.get("ok"):
            return self._json_response(result, status=self._memory_result_status(result))
        return self._json_response(result)

    async def _handle_memory_vector_rebuild(self, _request: web.Request) -> web.Response:
        result = self._memory_gui_service().rebuild_vector_index()
        if not result.get("ok"):
            return self._json_response(result, status=self._memory_result_status(result))
        return self._json_response(result)

    async def _handle_memory_embedding_test(self, _request: web.Request) -> web.Response:
        result = self._memory_gui_service().test_embedding()
        if not result.get("ok"):
            return self._json_response(result, status=self._memory_result_status(result))
        return self._json_response(result)

    async def _handle_memory_items(self, request: web.Request) -> web.Response:
        payload = dict(request.query)
        return self._json_response(
            {"ok": True, "data": self._list_memory_items(payload)}
        )

    async def _handle_memory_item_save(self, request: web.Request) -> web.Response:
        payload = await self._read_payload(request)
        data = self._upsert_memory_item(payload)
        ok = not data.get("error")
        return self._json_response(
            {"ok": ok, "data": data, "error": data.get("error", "")},
            status=200 if ok else 400,
        )

    async def _handle_memory_item_delete(self, request: web.Request) -> web.Response:
        payload = await self._read_payload(request)
        data = self._delete_memory_item(payload)
        ok = not data.get("error")
        return self._json_response(
            {"ok": ok, "data": data, "error": data.get("error", "")},
            status=200 if ok else 400,
        )

    async def _handle_memory_episodes(self, request: web.Request) -> web.Response:
        payload = dict(request.query)
        return self._json_response(
            {"ok": True, "data": self._list_memory_episodes(payload)}
        )

    async def _handle_memory_episode_save(self, request: web.Request) -> web.Response:
        payload = await self._read_payload(request)
        data = self._upsert_episode(payload)
        ok = not data.get("error")
        return self._json_response(
            {"ok": ok, "data": data, "error": data.get("error", "")},
            status=200 if ok else 400,
        )

    async def _handle_memory_episode_delete(self, request: web.Request) -> web.Response:
        payload = await self._read_payload(request)
        data = self._delete_episode(payload)
        ok = not data.get("error")
        return self._json_response(
            {"ok": ok, "data": data, "error": data.get("error", "")},
            status=200 if ok else 400,
        )

    async def _handle_transcript_list(self, request: web.Request) -> web.Response:
        payload = dict(request.query)
        return self._json_response({"ok": True, "data": self._list_transcript(payload)})

    async def _handle_transcript_delete(self, request: web.Request) -> web.Response:
        payload = await self._read_payload(request)
        data = self._delete_transcript(payload)
        ok = not data.get("error")
        return self._json_response(
            {"ok": ok, "data": data, "error": data.get("error", "")},
            status=200 if ok else 400,
        )

    async def _handle_transcript_clear(self, _request: web.Request) -> web.Response:
        data = self._clear_transcript()
        ok = not data.get("error")
        return self._json_response(
            {"ok": ok, "data": data, "error": data.get("error", "")},
            status=200 if ok else 400,
        )

    async def _handle_qq_profiles(self, request: web.Request) -> web.Response:
        payload = dict(request.query)
        data = self._list_qq_profiles(payload)
        return self._json_response({"ok": True, "data": data})

    async def _handle_qq_profile_save(self, request: web.Request) -> web.Response:
        payload = await self._read_payload(request)
        data = self._upsert_qq_profile(payload)
        ok = not data.get("error")
        return self._json_response(
            {"ok": ok, "data": data, "error": data.get("error", "")},
            status=200 if ok else 400,
        )

    async def _handle_qq_profile_delete(self, request: web.Request) -> web.Response:
        payload = await self._read_payload(request)
        data = self._delete_qq_profile(payload)
        ok = not data.get("error")
        return self._json_response(
            {"ok": ok, "data": data, "error": data.get("error", "")},
            status=200 if ok else 400,
        )

    async def _handle_events(self, request: web.Request) -> web.Response:
        root = self._find_backend_root(os.getcwd())
        if not root:
            return self._json_response(
                {"ok": False, "error": "backend_not_found"}, status=404
            )
        payload = dict(request.query)
        return self._json_response(
            {"ok": True, "data": self._list_recent_events(root, payload)}
        )

    async def _handle_outbound_records(self, request: web.Request) -> web.Response:
        payload = dict(request.query)
        data = self._list_outbound_records(payload)
        ok = not data.get("error")
        return self._json_response(
            {"ok": ok, "data": data, "error": data.get("error", "")},
            status=200 if ok else 503,
        )

    async def _handle_reply_effects(self, request: web.Request) -> web.Response:
        payload = dict(request.query)
        data = self._list_reply_effects(payload)
        ok = not data.get("error")
        return self._json_response(
            {"ok": ok, "data": data, "error": data.get("error", "")},
            status=200 if ok else 503,
        )

    async def _handle_deferred_tool_stats(self, _request: web.Request) -> web.Response:
        data = self._get_deferred_tool_stats()
        ok = not data.get("error")
        return self._json_response(
            {"ok": ok, "data": data, "error": data.get("error", "")},
            status=200 if ok else 503,
        )

    async def _handle_activity_events(self, request: web.Request) -> web.Response:
        store = self._get_memory_store()
        if store is None:
            return self._json_response(
                {"ok": False, "error": "memory_store_unavailable"}, status=500
            )
        payload = dict(request.query)
        limit = max(1, min(5000, int(payload.get("limit", 200) or 200)))
        date_str = str(payload.get("date") or "").strip()
        try:
            rows = store.list_activity_events(limit=limit, date_str=date_str)
            return self._json_response({"ok": True, "data": rows})
        except Exception as exc:
            return self._json_response(
                {"ok": False, "error": f"activity_events_failed: {exc}"},
                status=500,
            )


    async def _handle_activity_config(self, _request: web.Request) -> web.Response:
        app = self.app_ref
        if app is not None and hasattr(app, "get_activity_client_config"):
            try:
                data = app.get_activity_client_config()
            except Exception as exc:
                return self._json_response(
                    {"ok": False, "error": f"activity_config_unavailable: {exc}"},
                    status=500,
                )
            if not isinstance(data, dict):
                return self._json_response(
                    {"ok": False, "error": "activity_config_invalid"},
                    status=500,
                )
            return self._json_response({"ok": True, "data": dict(data)})

        # Fallback when app_ref is missing methods (e.g. partial stubs in tests).
        settings: Dict[str, Any] = {}
        if app is not None and hasattr(app, "_load_runtime_settings"):
            try:
                loaded = app._load_runtime_settings()
                if isinstance(loaded, dict):
                    settings = loaded
            except Exception:
                settings = {}

        def _int(key: str, default: int) -> int:
            try:
                value = int(settings.get(key, default) or default)
            except Exception:
                value = int(default)
            return max(1, value)

        data = {
            "revision": max(0, int(settings.get("activity_config_revision") or 0)),
            "monitor_enabled": bool(settings.get("activity_monitor_enabled", True)),
            "sedentary_reminder_minutes": _int("sedentary_reminder_minutes", 60),
            "sedentary_break_minutes": _int("sedentary_break_minutes", 5),
            "sedentary_cooldown_minutes": _int("sedentary_cooldown_minutes", 60),
            "include_process_path": bool(
                settings.get("activity_include_process_path", False)
            ),
            "include_window_title": bool(
                settings.get("activity_include_window_title", False)
            ),
            "include_browser_context": bool(
                settings.get("activity_include_browser_context", False)
            ),
        }
        return self._json_response({"ok": True, "data": data})

    async def _handle_activity_ingest(self, request: web.Request) -> web.Response:
        store = self._get_memory_store()
        if store is None:
            return self._json_response(
                {"ok": False, "error": "memory_store_unavailable"}, status=500
            )
        try:
            payload = await request.json(loads=json.loads)
        except Exception as exc:
            return self._json_response(
                {"ok": False, "error": f"bad_json: {exc}"}, status=400
            )
        if not isinstance(payload, dict):
            return self._json_response(
                {"ok": False, "error": "payload_must_be_object"}, status=400
            )
        source = str(payload.get("source") or "").strip()
        if source != LIVE2D_ACTIVITY_SOURCE:
            return self._json_response(
                {
                    "ok": False,
                    "error": "invalid_activity_source",
                    "expected": LIVE2D_ACTIVITY_SOURCE,
                },
                status=400,
            )
        try:
            if hasattr(store, "ingest_activity_event"):
                result = store.ingest_activity_event(payload)
            else:
                store.add_activity_event(payload)
                result = {"latest": False, "historized": True}
            self._request_work_session_status_refresh()
            return self._json_response({"ok": True, **result})
        except Exception as exc:
            return self._json_response(
                {"ok": False, "error": f"save_failed: {exc}"}, status=500
            )

    async def _handle_media_download(self, request: web.Request) -> web.Response:
        ticket = str(request.match_info.get("ticket") or "").strip()
        if not ticket:
            return self._json_response(
                {"ok": False, "error": "ticket_required"}, status=400
            )
        try:
            opened = self.media_registry.consume(ticket)
        except MediaTicketError as exc:
            return self._json_response(
                {"ok": False, "error": str(exc)}, status=404
            )
        except Exception as exc:
            return self._json_response(
                {"ok": False, "error": f"media_unavailable: {exc}"}, status=500
            )
        return web.FileResponse(
            path=opened.path,
            headers={
                "Content-Type": opened.media_type,
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    async def _async_start(self) -> None:
        app = web.Application(middlewares=[self._cors_middleware, self._auth_middleware])
        app.router.add_get(self._api_path("/health"), self._handle_health)
        app.router.add_get(
            self._api_path("/runtime/status"), self._handle_runtime_status
        )
        app.router.add_post(
            self._api_path("/runtime/control"), self._handle_runtime_control
        )
        app.router.add_get(self._api_path("/settings"), self._handle_settings_get)
        app.router.add_get(self._api_path("/models"), self._handle_models_get)
        app.router.add_post(self._api_path("/models/upsert"), self._handle_models_upsert)
        app.router.add_post(self._api_path("/models/delete"), self._handle_models_delete)
        app.router.add_post(self._api_path("/models/providers/upsert"), self._handle_providers_upsert)
        app.router.add_post(self._api_path("/models/providers/delete"), self._handle_providers_delete)
        app.router.add_post(self._api_path("/models/router"), self._handle_models_router_save)
        app.router.add_get(self._api_path("/dashboard"), self._handle_dashboard_get)
        app.router.add_get(self._api_path("/characters"), self._handle_characters_list)
        app.router.add_get(self._api_path("/characters/get"), self._handle_characters_get)
        app.router.add_post(self._api_path("/characters/upsert"), self._handle_characters_upsert)
        app.router.add_post(self._api_path("/characters/delete"), self._handle_characters_delete)
        app.router.add_post(self._api_path("/characters/activate"), self._handle_characters_activate)
        app.router.add_post(
            self._api_path("/characters/costumes/upsert"),
            self._handle_characters_costume_upsert,
        )
        app.router.add_post(
            self._api_path("/characters/costumes/delete"),
            self._handle_characters_costume_delete,
        )
        app.router.add_post(
            self._api_path("/characters/costumes/wear"),
            self._handle_characters_costume_wear,
        )
        app.router.add_get(
            self._api_path("/characters/costume-meta"),
            self._handle_character_costume_meta,
        )
        app.router.add_post(
            self._api_path("/characters/preview-motion"),
            self._handle_character_preview_motion,
        )
        app.router.add_post(
            self._api_path("/characters/preview-expression"),
            self._handle_character_preview_expression,
        )
        app.router.add_get(self._api_path("/plugins"), self._handle_plugin_list)
        app.router.add_post(
            self._api_path("/plugins/toggle"), self._handle_plugin_toggle
        )
        app.router.add_post(
            self._api_path("/plugins/reload"), self._handle_plugin_reload
        )
        app.router.add_get(
            self._api_path("/plugins/config"), self._handle_plugin_config_get
        )
        app.router.add_get(
            self._api_path("/plugins/config/schema"),
            self._handle_plugin_config_schema_get,
        )
        app.router.add_post(
            self._api_path("/plugins/config"), self._handle_plugin_config_save
        )
        app.router.add_get(
            self._api_path("/dependencies"), self._handle_dependency_scan
        )
        app.router.add_post(
            self._api_path("/dependencies/install"), self._handle_dependency_install
        )
        app.router.add_get(self._api_path("/diary"), self._handle_diary_list)
        app.router.add_get(self._api_path("/diary/get"), self._handle_diary_get)
        app.router.add_post(self._api_path("/diary/upsert"), self._handle_diary_upsert)
        app.router.add_post(self._api_path("/diary/delete"), self._handle_diary_delete)
        app.router.add_post(self._api_path("/diary/export"), self._handle_diary_export)
        app.router.add_get(self._api_path("/knowledge"), self._handle_knowledge_list)
        app.router.add_get(self._api_path("/knowledge/stats"), self._handle_knowledge_stats)
        app.router.add_get(self._api_path("/knowledge/search"), self._handle_knowledge_search)
        app.router.add_post(self._api_path("/knowledge/search"), self._handle_knowledge_search)
        app.router.add_post(self._api_path("/knowledge/dirs"), self._handle_knowledge_save_dirs)
        app.router.add_post(self._api_path("/knowledge/import"), self._handle_knowledge_import)
        app.router.add_post(self._api_path("/knowledge/rebuild"), self._handle_knowledge_rebuild)
        app.router.add_post(
            self._api_path("/knowledge/delete-dirs"), self._handle_knowledge_delete_dirs
        )
        app.router.add_post(self._api_path("/knowledge/learn"), self._handle_knowledge_learn)
        app.router.add_post(
            self._api_path("/knowledge/create-doc"), self._handle_knowledge_create_doc
        )
        app.router.add_get(self._api_path("/memory/core"), self._handle_memory_core_list)
        app.router.add_get(self._api_path("/memory/core/get"), self._handle_memory_core_get)
        app.router.add_post(
            self._api_path("/memory/core/upsert"), self._handle_memory_core_upsert
        )
        app.router.add_post(
            self._api_path("/memory/core/delete"), self._handle_memory_core_delete
        )
        app.router.add_post(
            self._api_path("/memory/core/category"), self._handle_memory_core_category
        )
        app.router.add_get(
            self._api_path("/memory/vector/status"), self._handle_memory_vector_status
        )
        app.router.add_post(
            self._api_path("/memory/vector/rebuild"), self._handle_memory_vector_rebuild
        )
        app.router.add_post(
            self._api_path("/memory/embedding/test"), self._handle_memory_embedding_test
        )
        app.router.add_get(self._api_path("/memory/items"), self._handle_memory_items)
        app.router.add_post(
            self._api_path("/memory/items/upsert"), self._handle_memory_item_save
        )
        app.router.add_post(
            self._api_path("/memory/items/delete"), self._handle_memory_item_delete
        )
        app.router.add_get(
            self._api_path("/memory/episodes"), self._handle_memory_episodes
        )
        app.router.add_post(
            self._api_path("/memory/episodes/upsert"), self._handle_memory_episode_save
        )
        app.router.add_post(
            self._api_path("/memory/episodes/delete"),
            self._handle_memory_episode_delete,
        )
        app.router.add_get(
            self._api_path("/memory/transcript"), self._handle_transcript_list
        )
        app.router.add_post(
            self._api_path("/memory/transcript/delete"), self._handle_transcript_delete
        )
        app.router.add_post(
            self._api_path("/memory/transcript/clear"), self._handle_transcript_clear
        )
        app.router.add_get(self._api_path("/qq/profiles"), self._handle_qq_profiles)
        app.router.add_post(
            self._api_path("/qq/profiles/upsert"), self._handle_qq_profile_save
        )
        app.router.add_post(
            self._api_path("/qq/profiles/delete"), self._handle_qq_profile_delete
        )
        app.router.add_get(self._api_path("/events"), self._handle_events)
        app.router.add_get(self._api_path("/outbound"), self._handle_outbound_records)
        app.router.add_get(self._api_path("/reply-effects"), self._handle_reply_effects)
        app.router.add_get(
            self._api_path("/deferred-tools/stats"), self._handle_deferred_tool_stats
        )
        app.router.add_get(
            self._api_path("/activity-events"), self._handle_activity_events
        )
        app.router.add_get(
            self._api_path("/activity-config"), self._handle_activity_config
        )
        app.router.add_post(
            self._api_path("/activity-ingest"), self._handle_activity_ingest
        )
        app.router.add_get(
            self._api_path("/media/{ticket}"), self._handle_media_download
        )
        app.router.add_route("OPTIONS", "/{tail:.*}", self._handle_health)
        app.router.add_post(
            self._api_path("/settings/custom_models"),
            lambda request: self._handle_settings_save(request, "customModels"),
        )
        app.router.add_post(
            self._api_path("/settings/runtime"),
            lambda request: self._handle_settings_save(request, "runtimeSettings"),
        )
        app.router.add_post(
            self._api_path("/settings/characters"),
            lambda request: self._handle_settings_save(request, "characters"),
        )
        app.router.add_post(
            self._api_path("/settings/mcp"),
            lambda request: self._handle_settings_save(request, "mcpConfig"),
        )

        requested_port = self.port
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        last_bind_error: Optional[BaseException] = None
        for candidate_port in self._candidate_ports():
            try:
                self.port = candidate_port
                self._site = web.TCPSite(self._runner, self.host, self.port)
                await self._site.start()
                self._apply_bound_port(candidate_port)
                if self.logger:
                    if self.port != requested_port:
                        self.logger.warning(
                            f"GUI HTTP port {requested_port} unavailable; "
                            f"listening on fallback {self.port}"
                        )
                    self.logger.info(f"GUI HTTP listening on {self._http_url()}")
                return
            except OSError as exc:
                last_bind_error = exc
                self._site = None
                if self.logger:
                    self.logger.warning(
                        f"GUI HTTP bind failed on {self.host}:{candidate_port}: {exc}"
                    )

        try:
            await self._runner.cleanup()
        finally:
            self._runner = None
            self._site = None
            self.port = requested_port
        if last_bind_error is not None:
            raise last_bind_error
        raise RuntimeError("GUI HTTP failed to bind")

    async def _async_shutdown(self) -> None:
        if self._runner is not None:
            try:
                await self._runner.cleanup()
            finally:
                self._runner = None
                self._site = None

    def start(self) -> None:
        if self._thread is not None:
            return

        def _worker():
            loop = None
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                self._server_loop = loop
                loop.run_until_complete(self._async_start())
            except BaseException as exc:
                self._start_error = exc
                self._ready.set()
                return

            self._ready.set()
            try:
                loop.run_forever()
            finally:
                try:
                    if loop:
                        loop.run_until_complete(self._async_shutdown())
                finally:
                    if loop:
                        loop.close()
                    self._server_loop = None

        self._ready.clear()
        self._start_error = None
        self._thread = threading.Thread(target=_worker, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=5.0)
        if self._start_error is not None:
            thread = self._thread
            self._thread = None
            if thread and thread.is_alive():
                thread.join(timeout=0.2)
            raise RuntimeError(str(self._start_error))

    def stop(self) -> None:
        if self._thread is None or self._server_loop is None:
            self._thread = None
            self._server_loop = None
            return

        loop = self._server_loop
        thread = self._thread

        try:
            stopper = asyncio.run_coroutine_threadsafe(self._async_shutdown(), loop)
            stopper.result(timeout=5.0)
        except Exception:
            pass
        finally:
            try:
                loop.call_soon_threadsafe(loop.stop)
            except Exception:
                pass
            if thread.is_alive():
                thread.join(timeout=5.0)
            self._thread = None
            self._server_loop = None
