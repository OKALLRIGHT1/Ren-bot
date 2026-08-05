from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from modules.model_catalog import MODEL_PURPOSE_OPTIONS, get_model_purposes, normalize_purposes


ROUTE_TASK_OPTIONS = (
    ("default", "默认回复", "主回复与未单独配置任务的兜底链路", ("chat",), True),
    ("reply_polish", "回复润色", "回复风格、情绪标签与自然化处理", ("chat",), True),
    ("tool_reasoning", "工具推理", "工具选择、参数规划与多步任务", ("tool_reasoning", "chat"), True),
    ("summary", "总结压缩", "长对话、工具结果和记忆摘要", ("summary", "chat"), True),
    ("gatekeeper", "能力判断", "判断是否需要调用插件或工具", ("chat", "tool_reasoning"), True),
    (
        "memory_writeback",
        "记忆写回",
        "从对话抽取稳定用户事实并写入长期记忆",
        ("chat", "tool_reasoning", "summary"),
        True,
    ),
    ("translation", "翻译", "语言转换与本地化", ("translation", "chat"), True),
    ("screen_classify", "屏幕分类", "前台应用和画面内容分类", ("chat", "vision"), True),
    ("sensor_vision_talk", "屏幕视觉回复", "根据截图生成屏幕吐槽", ("vision", "chat"), True),
    ("codex", "代码助手", "Codex 与工作区任务", ("code", "chat"), True),
    ("web_search", "联网搜索", "搜索插件的检索与整理", ("web_search",), False),
    ("image_gen", "文生图", "根据文字生成图片", ("image_gen", "image_edit"), False),
    ("image_edit", "图像编辑", "图生图和局部修改", ("image_edit", "image_gen"), False),
    # NOTE: embedding is NOT an LLM_ROUTER task. Memory/knowledge use an ordered
    # catalog queue in runtime_settings.embedding_model_ids (fallback:
    # embedding_model_id / EMBEDDING_CONFIG).
)


def _load_json(path: Path, fallback: Dict[str, Any]) -> Dict[str, Any]:
    if not path.exists():
        return dict(fallback)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return dict(fallback)
    return data if isinstance(data, dict) else dict(fallback)


def _save_json(path: Path, data: Dict[str, Any]) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)
        return True
    except Exception:
        return False


@dataclass
class SecretUpdate:
    action: str  # keep | replace | clear
    value: str = ""

    @classmethod
    def parse(cls, raw: Any) -> "SecretUpdate":
        if raw is None:
            return cls(action="keep")
        if isinstance(raw, str):
            text = raw.strip()
            if not text or text in {"********", "****", "[masked]", "***"}:
                return cls(action="keep")
            return cls(action="replace", value=text)
        if isinstance(raw, dict):
            action = str(raw.get("action") or "keep").strip().lower()
            if action not in {"keep", "replace", "clear"}:
                action = "keep"
            return cls(action=action, value=str(raw.get("value") or ""))
        return cls(action="keep")

    def apply(self, current: str) -> str:
        if self.action == "clear":
            return ""
        if self.action == "replace":
            return str(self.value or "")
        return str(current or "")


def mask_model_row(model_id: str, cfg: Dict[str, Any]) -> Dict[str, Any]:
    row = dict(cfg) if isinstance(cfg, dict) else {}
    api_key = str(row.pop("api_key", "") or "")
    purposes = get_model_purposes(row)
    return {
        "id": str(model_id),
        "model": str(row.get("model") or ""),
        "base_url": str(row.get("base_url") or ""),
        "provider": str(row.get("provider") or ""),
        "api_style": str(row.get("api_style") or row.get("api_mode") or ""),
        "purposes": purposes,
        "has_api_key": bool(api_key.strip()),
        "embedding_dimension": row.get("embedding_dimension"),
        "embedding_endpoint_path": row.get("embedding_endpoint_path") or "",
        "embedding_provider": row.get("embedding_provider") or "",
        "endpoint_path": row.get("endpoint_path") or "",
        "edit_endpoint_path": row.get("edit_endpoint_path") or "",
        "notes": str(row.get("notes") or ""),
    }


def normalize_provider_row(provider_id: str, cfg: Dict[str, Any]) -> Dict[str, Any]:
    row = dict(cfg) if isinstance(cfg, dict) else {}
    api_key = str(row.pop("api_key", "") or "")
    return {
        "id": str(provider_id),
        "base_url": str(row.get("base_url") or ""),
        "has_api_key": bool(api_key.strip()),
        "notes": str(row.get("notes") or ""),
    }


class ModelsCatalogService:
    def __init__(self, catalog_path: Path) -> None:
        self.path = Path(catalog_path)

    def _empty(self) -> Dict[str, Any]:
        return {"models": {}, "router": {}, "providers": {}}

    def _load(self) -> Dict[str, Any]:
        data = _load_json(self.path, self._empty())
        models = data.get("models") if isinstance(data.get("models"), dict) else {}
        router = data.get("router") if isinstance(data.get("router"), dict) else {}
        providers = data.get("providers") if isinstance(data.get("providers"), dict) else {}
        return {"models": models, "router": router, "providers": providers}

    def _write(self, data: Dict[str, Any]) -> bool:
        return _save_json(self.path, data)

    def list_catalog(self) -> Dict[str, Any]:
        data = self._load()
        models = [
            mask_model_row(model_id, cfg if isinstance(cfg, dict) else {})
            for model_id, cfg in sorted(data["models"].items(), key=lambda item: str(item[0]))
        ]
        providers = [
            normalize_provider_row(provider_id, cfg if isinstance(cfg, dict) else {})
            for provider_id, cfg in sorted(
                data["providers"].items(), key=lambda item: str(item[0])
            )
        ]
        router = {
            str(task): [str(x) for x in chain if str(x).strip()]
            if isinstance(chain, list)
            else []
            for task, chain in data["router"].items()
        }
        return {
            "models": models,
            "providers": providers,
            "router": router,
            "purpose_options": [
                {"id": purpose_id, "label": label}
                for purpose_id, label in MODEL_PURPOSE_OPTIONS
            ],
            "route_tasks": [
                {
                    "id": task_id,
                    "label": label,
                    "description": description,
                    "purposes": list(purposes),
                    "allow_untagged": allow_untagged,
                }
                for task_id, label, description, purposes, allow_untagged in ROUTE_TASK_OPTIONS
            ],
        }

    def upsert_model(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        model_id = str(payload.get("id") or payload.get("model_id") or "").strip()
        if not model_id:
            return {"ok": False, "error": "invalid_model_id"}
        data = self._load()
        current = dict(data["models"].get(model_id) or {})
        secret = SecretUpdate.parse(payload.get("api_key"))
        current["model"] = str(payload.get("model") or current.get("model") or "").strip()
        current["base_url"] = str(payload.get("base_url") or current.get("base_url") or "").strip()
        if "provider" in payload:
            current["provider"] = str(payload.get("provider") or "").strip()
        if "api_style" in payload or "api_mode" in payload:
            current["api_style"] = str(
                payload.get("api_style") or payload.get("api_mode") or ""
            ).strip()
        if "purposes" in payload or "purpose" in payload:
            purposes = payload.get("purposes")
            if purposes is None and payload.get("purpose") is not None:
                purposes = [payload.get("purpose")]
            current["purposes"] = normalize_purposes(purposes)
        for key in (
            "embedding_dimension",
            "embedding_endpoint_path",
            "embedding_provider",
            "endpoint_path",
            "edit_endpoint_path",
            "notes",
        ):
            if key in payload:
                current[key] = payload.get(key)
        current["api_key"] = secret.apply(str(current.get("api_key") or ""))
        if not current["api_key"]:
            current.pop("api_key", None)
        data["models"][model_id] = current
        if not self._write(data):
            return {"ok": False, "error": "write_failed"}
        return {"ok": True, "data": self.list_catalog()}

    def delete_model(self, model_id: str) -> Dict[str, Any]:
        model_id = str(model_id or "").strip()
        if not model_id:
            return {"ok": False, "error": "invalid_model_id"}
        data = self._load()
        if model_id not in data["models"]:
            return {"ok": False, "error": "not_found"}
        data["models"].pop(model_id, None)
        for task, chain in list(data["router"].items()):
            if isinstance(chain, list):
                data["router"][task] = [str(x) for x in chain if str(x) != model_id]
        if not self._write(data):
            return {"ok": False, "error": "write_failed"}
        return {"ok": True, "data": self.list_catalog()}

    def upsert_provider(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        provider_id = str(payload.get("id") or payload.get("provider_id") or "").strip()
        if not provider_id:
            return {"ok": False, "error": "invalid_provider_id"}
        data = self._load()
        current = dict(data["providers"].get(provider_id) or {})
        secret = SecretUpdate.parse(payload.get("api_key"))
        if "base_url" in payload:
            current["base_url"] = str(payload.get("base_url") or "").strip()
        if "notes" in payload:
            current["notes"] = str(payload.get("notes") or "")
        current["api_key"] = secret.apply(str(current.get("api_key") or ""))
        if not current["api_key"]:
            current.pop("api_key", None)
        data["providers"][provider_id] = current
        if not self._write(data):
            return {"ok": False, "error": "write_failed"}
        return {"ok": True, "data": self.list_catalog()}

    def delete_provider(self, provider_id: str) -> Dict[str, Any]:
        provider_id = str(provider_id or "").strip()
        if not provider_id:
            return {"ok": False, "error": "invalid_provider_id"}
        data = self._load()
        if provider_id not in data["providers"]:
            return {"ok": False, "error": "not_found"}
        data["providers"].pop(provider_id, None)
        if not self._write(data):
            return {"ok": False, "error": "write_failed"}
        return {"ok": True, "data": self.list_catalog()}

    def save_router(self, router: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(router, dict):
            return {"ok": False, "error": "invalid_router"}
        data = self._load()
        known = set(data["models"].keys())
        cleaned: Dict[str, List[str]] = {}
        for task, chain in router.items():
            task_name = str(task or "").strip()
            if not task_name:
                continue
            if not isinstance(chain, list):
                cleaned[task_name] = []
                continue
            cleaned[task_name] = [
                str(item).strip()
                for item in chain
                if str(item).strip() and str(item).strip() in known
            ]
        data["router"] = cleaned
        if not self._write(data):
            return {"ok": False, "error": "write_failed"}
        return {"ok": True, "data": self.list_catalog()}
