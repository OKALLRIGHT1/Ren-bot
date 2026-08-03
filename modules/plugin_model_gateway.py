from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Sequence

from modules.model_catalog import (
    model_has_purpose,
    normalize_model_selection,
)


@dataclass(frozen=True)
class PluginModelCallResult:
    ok: bool
    text: str = ""
    model_id: str = ""
    error_code: str = ""
    error_message: str = ""


class PluginModelGateway:
    def __init__(
        self,
        *,
        catalog_getter: Optional[Callable[[], Dict[str, Any]]] = None,
        router_getter: Optional[Callable[[], Dict[str, Any]]] = None,
        chat_callable: Optional[Callable[..., str]] = None,
    ) -> None:
        self._catalog_getter = catalog_getter or self._default_catalog
        self._router_getter = router_getter or self._default_router
        self._chat_callable = chat_callable or self._default_chat_callable

    @staticmethod
    def _default_catalog() -> Dict[str, Any]:
        from config import MODELS

        return MODELS

    @staticmethod
    def _default_router() -> Dict[str, Any]:
        from config import LLM_ROUTER

        return LLM_ROUTER

    @staticmethod
    def _default_chat_callable(*args, **kwargs) -> str:
        from modules.llm import chat_with_ai

        return str(chat_with_ai(*args, **kwargs) or "")

    @staticmethod
    def _looks_like_failure(text: str) -> bool:
        value = str(text or "").strip()
        if not value:
            return True
        return "系统繁忙" in value or "无法连接 AI" in value

    def _resolve_model_ids(
        self,
        *,
        selected_ids: Sequence[str] | str | None,
        required_purpose: str | Sequence[str],
        task_type: str,
        allow_untagged_route: bool,
    ) -> tuple[list[str], Optional[PluginModelCallResult]]:
        catalog = self._catalog_getter()
        catalog = catalog if isinstance(catalog, dict) else {}
        selected = normalize_model_selection(selected_ids)
        manual = bool(selected)

        if not selected:
            router = self._router_getter()
            raw_chain = router.get(task_type, []) if isinstance(router, dict) else []
            selected = normalize_model_selection(raw_chain)
            if not selected:
                return [], PluginModelCallResult(
                    ok=False,
                    error_code="model_route_empty",
                    error_message=f"任务路由 {task_type} 没有配置模型",
                )

        valid: list[str] = []
        for model_id in selected:
            model_cfg = catalog.get(model_id)
            if not isinstance(model_cfg, dict):
                if manual:
                    return [], PluginModelCallResult(
                        ok=False,
                        model_id=model_id,
                        error_code="model_not_found",
                        error_message=f"所选模型 {model_id} 已不存在",
                    )
                continue
            if required_purpose and not model_has_purpose(
                model_cfg,
                required_purpose,
                allow_untagged=(not manual and allow_untagged_route),
            ):
                if manual:
                    return [], PluginModelCallResult(
                        ok=False,
                        model_id=model_id,
                        error_code="model_purpose_mismatch",
                        error_message=f"所选模型 {model_id} 不支持要求的用途",
                    )
                continue
            valid.append(model_id)

        if not valid:
            return [], PluginModelCallResult(
                ok=False,
                error_code="model_unavailable",
                error_message="没有符合用途且可用的模型",
            )
        return valid, None

    async def invoke_text(
        self,
        messages: list[dict[str, Any]],
        *,
        selected_ids: Sequence[str] | str | None = None,
        required_purpose: str | Sequence[str] = "",
        task_type: str = "default",
        caller: str,
        timeout_sec: float = 30,
        allow_untagged_route: bool = True,
    ) -> PluginModelCallResult:
        model_ids, error = self._resolve_model_ids(
            selected_ids=selected_ids,
            required_purpose=required_purpose,
            task_type=task_type,
            allow_untagged_route=allow_untagged_route,
        )
        if error is not None:
            return error

        try:
            call_metadata: Dict[str, Any] = {}
            text = await asyncio.to_thread(
                self._chat_callable,
                messages,
                task_type=task_type,
                caller=caller,
                timeout_sec=float(timeout_sec),
                model_keys_override=model_ids,
                call_metadata=call_metadata,
            )
        except Exception as exc:
            return PluginModelCallResult(
                ok=False,
                model_id=model_ids[0],
                error_code="model_call_failed",
                error_message=str(exc),
            )
        text = str(text or "").strip()
        if self._looks_like_failure(text):
            return PluginModelCallResult(
                ok=False,
                model_id=model_ids[0],
                error_code="model_call_failed",
                error_message=text or "模型返回空内容",
            )
        return PluginModelCallResult(
            ok=True,
            text=text,
            model_id=str(call_metadata.get("model_key") or model_ids[0]),
        )


_PLUGIN_MODEL_GATEWAY: Optional[PluginModelGateway] = None


def get_plugin_model_gateway() -> PluginModelGateway:
    global _PLUGIN_MODEL_GATEWAY
    if _PLUGIN_MODEL_GATEWAY is None:
        _PLUGIN_MODEL_GATEWAY = PluginModelGateway()
    return _PLUGIN_MODEL_GATEWAY
