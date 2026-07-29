import asyncio
import base64
import json
import logging
import os
import re
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from services.capability_manager import ToolCapability, ToolCapabilityMatch

try:
    import aiohttp
except Exception:
    aiohttp = None


COMMAND_PREFIXES = ("/画图", "/画画")
logger = logging.getLogger(__name__)


class Plugin:
    def __init__(self):
        self._config_path = Path(__file__).with_name("config.json")
        self._config: Dict[str, Any] = {}
        self._settings: Dict[str, Any] = {}
        self.reload_config()

    def get_capabilities(self):
        return [
            ToolCapability(
                id="qq_draw.generate_image_cmd",
                plugin="qq_draw",
                trigger_mode="command_only",
                match=self._match_draw_command,
                check_available=self._check_available,
                description="通过 /画图 或 /画画 生成图片",
                examples=["/画图 一只猫", "/画画 雨天的街道"],
            )
        ]

    def _check_available(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        self.reload_config()
        providers = self._build_provider_queue()
        if not providers:
            return {
                "available": False,
                "reason": (
                    "no_image_models: 请先在「模型与路由」给模型勾选画图用途，"
                    "再打开本插件设置，把要用的模型加到「画图模型」执行链"
                ),
            }
        if any(self._resolve_provider_api_key(provider) for provider in providers):
            return {"available": True}
        return {
            "available": False,
            "reason": "missing_secret: 画图模型未配置 API Key",
        }

    def reload_config(self):
        try:
            self._config = json.loads(self._config_path.read_text(encoding="utf-8"))
        except Exception:
            self._config = {}
        runtime_settings = getattr(self, "settings", None)
        settings = (
            runtime_settings
            if isinstance(runtime_settings, dict) and runtime_settings
            else (self._config.get("settings") or {})
        )
        # Only keep request-style knobs here. Connection info comes from 模型与路由.
        self._settings = {
            "model_queue": self._read_setting(settings, "model_queue", []),
            "size_value": self._read_setting(settings, "size_value", "1024x1024"),
            "quality": self._read_setting(settings, "quality", ""),
            "style": self._read_setting(settings, "style", ""),
            "negative_prompt": self._read_setting(settings, "negative_prompt", ""),
            "extra_body_json": self._read_setting(settings, "extra_body_json", "{}"),
            "request_timeout_sec": self._read_setting(
                settings, "request_timeout_sec", 300
            ),
            "caption_template": self._read_setting(
                settings, "caption_template", "🖼️ 已按你的要求画好了。"
            ),
            "image_to_image_enabled": bool(
                self._read_setting(settings, "image_to_image_enabled", True)
            ),
            "max_input_images": self._read_setting(settings, "max_input_images", 8),
            "input_image_field": self._read_setting(
                settings, "input_image_field", "image"
            ),
            "input_image_format": self._read_setting(
                settings, "input_image_format", "data_url"
            ),
            "include_chat_image_part": bool(
                self._read_setting(settings, "include_chat_image_part", True)
            ),
            "debug_logging": bool(self._read_setting(settings, "debug_logging", False)),
        }

    def _read_setting(self, settings: dict, key: str, default):
        value = settings.get(key, default)
        if isinstance(value, dict):
            return value.get("default", default)
        return value

    def _request_defaults(self) -> Dict[str, Any]:
        return {
            "size_value": self._settings.get("size_value"),
            "quality": self._settings.get("quality"),
            "style": self._settings.get("style"),
            "negative_prompt": self._settings.get("negative_prompt"),
            "extra_body_json": self._settings.get("extra_body_json"),
            "request_timeout_sec": self._settings.get("request_timeout_sec"),
            "input_image_field": self._settings.get("input_image_field"),
            "input_image_format": self._settings.get("input_image_format"),
            "include_chat_image_part": self._settings.get("include_chat_image_part"),
            "max_input_images": self._settings.get("max_input_images"),
        }

    def _max_input_images(self) -> int:
        raw = self._settings.get("max_input_images", 8)
        if isinstance(raw, dict):
            raw = raw.get("default", 8)
        try:
            value = int(raw)
        except Exception:
            value = 8
        if value <= 0:
            return 8
        return min(value, 16)

    def _normalize_image_list(self, images: Any) -> List[str]:
        if images is None:
            return []
        if isinstance(images, str):
            text = images.strip()
            return [text] if text else []
        if not isinstance(images, (list, tuple)):
            return []
        cleaned: List[str] = []
        for item in images:
            text = str(item or "").strip()
            if text:
                cleaned.append(text)
        return cleaned

    def _selected_model_ids(self) -> List[str]:
        raw = self._settings.get("model_queue", [])
        if raw is None:
            return []
        if isinstance(raw, str):
            text = raw.strip()
            if not text:
                return []
            if text.startswith("["):
                try:
                    parsed = json.loads(text)
                    raw = parsed
                except Exception:
                    raw = [
                        part.strip()
                        for part in re.split(r"[\n,，;；]+", text)
                        if part.strip()
                    ]
            else:
                raw = [
                    part.strip()
                    for part in re.split(r"[\n,，;；]+", text)
                    if part.strip()
                ]
        if not isinstance(raw, (list, tuple)):
            return []
        result: List[str] = []
        seen = set()
        for item in raw:
            mid = str(item or "").strip()
            if not mid or mid in seen:
                continue
            seen.add(mid)
            result.append(mid)
        return result

    def _load_model_catalog(self) -> Dict[str, Dict[str, Any]]:
        catalog: Dict[str, Dict[str, Any]] = {}
        try:
            from config import MODELS as CONFIG_MODELS

            if isinstance(CONFIG_MODELS, dict):
                for key, value in CONFIG_MODELS.items():
                    if isinstance(value, dict):
                        catalog[str(key)] = dict(value)
        except Exception:
            pass

        custom_path = Path("data/custom_models.json")
        if custom_path.exists():
            try:
                payload = json.loads(custom_path.read_text(encoding="utf-8"))
                models = payload.get("models") if isinstance(payload, dict) else {}
                if isinstance(models, dict):
                    for key, value in models.items():
                        if isinstance(value, dict):
                            catalog[str(key)] = dict(value)
            except Exception as exc:
                self._debug(f"load custom_models failed: {exc}")
        return catalog

    def _load_router(self) -> Dict[str, Any]:
        router: Dict[str, Any] = {}
        try:
            from config import LLM_ROUTER

            if isinstance(LLM_ROUTER, dict):
                router.update(LLM_ROUTER)
        except Exception:
            pass
        custom_path = Path("data/custom_models.json")
        if custom_path.exists():
            try:
                payload = json.loads(custom_path.read_text(encoding="utf-8"))
                custom_router = payload.get("router") if isinstance(payload, dict) else {}
                if isinstance(custom_router, dict):
                    router.update(custom_router)
            except Exception as exc:
                self._debug(f"load router failed: {exc}")
        return router

    def _build_provider_queue(
        self, *, image_base64: str = "", image_base64_list: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """Prefer models selected in this plugin; fallback to 任务路由 image_*."""
        try:
            from modules.model_catalog import list_image_providers
        except Exception as exc:
            self._debug(f"list_image_providers unavailable: {exc}")
            return []

        images = self._normalize_image_list(image_base64_list)
        if not images and image_base64:
            images = self._normalize_image_list(image_base64)
        has_input_images = bool(images)

        selected = self._selected_model_ids()
        providers = list_image_providers(
            self._load_model_catalog(),
            image_base64=images[0] if has_input_images else "",
            request_defaults=self._request_defaults(),
            router=self._load_router(),
            selected_ids=selected if selected else None,
        )
        cleaned: List[Dict[str, Any]] = []
        seen = set()
        for provider in providers:
            if not provider.get("base_url"):
                self._debug(
                    f"skip model={provider.get('name')}: missing base_url"
                )
                continue
            signature = (
                str(provider.get("name") or "").strip().lower(),
                str(provider.get("base_url") or "").strip().lower(),
                str(provider.get("endpoint_path") or "").strip().lower(),
                str(provider.get("model_name") or "").strip().lower(),
            )
            if signature in seen:
                continue
            seen.add(signature)
            cleaned.append(provider)
        return cleaned

    def _resolve_provider_api_key(self, provider: Optional[Dict[str, Any]] = None) -> str:
        provider = provider or {}
        key = str(provider.get("api_key") or "").strip()
        if key:
            return key

        env_raw = str(provider.get("api_key_env") or "").strip()
        env_names = [
            part.strip()
            for part in re.split(r"[\s,，;；|]+", env_raw)
            if part.strip()
        ]
        if not env_names:
            name = str(provider.get("name") or provider.get("model_name") or "").lower()
            if "grok" in name or "xai" in name:
                env_names = ["GROK_API_KEY", "XAI_API_KEY"]
            else:
                env_names = ["GROK_API_KEY", "XAI_API_KEY", "OPENAI_API_KEY"]
        for env_name in env_names:
            value = str(os.getenv(env_name) or "").strip()
            if value:
                return value
        return ""

    def should_handle_direct(
        self, user_text: str, context: dict, matched_alias: str
    ) -> bool:
        text = str(user_text or "").strip()
        return any(text.startswith(prefix) for prefix in COMMAND_PREFIXES)

    def _match_draw_command(
        self, text: str, ctx: Dict[str, Any]
    ) -> Optional[ToolCapabilityMatch]:
        raw = str(text or "").strip()
        prompt = self._extract_prompt(raw)
        if not prompt:
            return None
        return ToolCapabilityMatch(
            capability_id="qq_draw.generate_image_cmd",
            plugin="qq_draw",
            score=1.0,
            args={"prompt": prompt},
            raw_text=raw,
            reason="draw_command_prefix",
        )

    def _extract_prompt(self, text: str) -> str:
        raw = str(text or "").strip()
        for prefix in COMMAND_PREFIXES:
            if raw.startswith(prefix):
                return raw[len(prefix) :].strip()
        return ""

    def _is_qq_context(self, context: dict) -> bool:
        source = str((context or {}).get("source") or "").strip().lower()
        return source in {"qq_gateway", "napcat_qq"}

    def _format_input_image_payload(
        self, image_base64: str, provider: Optional[Dict[str, Any]] = None
    ) -> str:
        raw = str(image_base64 or "").strip()
        if not raw:
            return ""
        provider = provider or self._request_defaults()
        image_format = str(
            provider.get("input_image_format")
            or self._settings.get("input_image_format")
            or "data_url"
        ).strip().lower()
        if image_format == "base64":
            return raw
        return f"data:image/png;base64,{raw}"

    def _format_input_image_payloads(
        self,
        image_base64: str = "",
        image_base64_list: Optional[List[str]] = None,
        provider: Optional[Dict[str, Any]] = None,
    ) -> List[str]:
        images = self._normalize_image_list(image_base64_list)
        if not images and image_base64:
            images = self._normalize_image_list(image_base64)
        payloads: List[str] = []
        for item in images:
            payload = self._format_input_image_payload(item, provider=provider)
            if payload:
                payloads.append(payload)
        return payloads

    def _build_request_body(
        self,
        prompt: str,
        image_base64: str = "",
        image_base64_list: Optional[List[str]] = None,
        provider: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        provider = provider or self._request_defaults()
        input_image_payloads = self._format_input_image_payloads(
            image_base64=image_base64,
            image_base64_list=image_base64_list,
            provider=provider,
        )
        use_chat_image_part = bool(
            input_image_payloads
            and provider.get(
                "include_chat_image_part",
                self._settings.get("include_chat_image_part", True),
            )
        )
        message_content: Any
        if use_chat_image_part:
            message_content = [
                {
                    "type": "text",
                    "text": prompt,
                }
            ]
            for payload in input_image_payloads:
                message_content.append(
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": payload,
                        },
                    }
                )
        else:
            message_content = prompt

        # 1. 【核心兼容】：同时带上 prompt（画图用）和 messages（聊天用）
        body: Dict[str, Any] = {
            "prompt": prompt,
            "messages": [
                {
                    "role": "user",
                    "content": message_content,
                }
            ]
        }

        # 2. 注入所有通用与画图参数
        model_value = str(provider.get("model_name") or "").strip()
        if model_value:
            body["model"] = model_value

        size_value = str(
            provider.get("size_value") or self._settings.get("size_value") or ""
        ).strip()
        if size_value:
            body["size"] = size_value

        quality_value = str(
            provider.get("quality") or self._settings.get("quality") or ""
        ).strip()
        if quality_value:
            body["quality"] = quality_value

        style_value = str(
            provider.get("style") or self._settings.get("style") or ""
        ).strip()
        if style_value:
            body["style"] = style_value

        negative_value = str(
            provider.get("negative_prompt")
            or self._settings.get("negative_prompt")
            or ""
        ).strip()
        if negative_value:
            body["negative_prompt"] = negative_value

        if input_image_payloads and bool(
            self._settings.get("image_to_image_enabled", True)
        ):
            field_name = str(
                provider.get("input_image_field")
                or self._settings.get("input_image_field")
                or "image"
            ).strip()
            if field_name:
                # 1 张保持兼容单值；多张传数组，让上游按参考图列表处理。
                body[field_name] = (
                    input_image_payloads[0]
                    if len(input_image_payloads) == 1
                    else list(input_image_payloads)
                )
                if len(input_image_payloads) > 1:
                    body["images"] = list(input_image_payloads)

        # 3. 合并自定义额外参数
        extra_args_raw = (
            str(
                provider.get("extra_body_json")
                or self._settings.get("extra_body_json")
                or "{}"
            ).strip()
            or "{}"
        )
        extra_args = json.loads(extra_args_raw)
        if not isinstance(extra_args, dict):
            raise ValueError("extra_body_json 必须是 JSON 对象")
        body.update(extra_args)

        self._normalize_request_body(body)
        return body

    def _is_image_edit_endpoint(
        self,
        image_base64: str = "",
        image_base64_list: Optional[List[str]] = None,
        provider: Optional[Dict[str, Any]] = None,
    ) -> bool:
        provider = provider or {}
        images = self._normalize_image_list(image_base64_list)
        if not images and image_base64:
            images = self._normalize_image_list(image_base64)
        endpoint_path = (
            str(
                provider.get("edit_endpoint_path")
                or provider.get("endpoint_path")
                or ""
            ).strip().lower()
            if images
            else str(provider.get("endpoint_path") or "").strip().lower()
        )
        return endpoint_path.endswith("/images/edits")

    def _decode_base64_image(self, image_base64: str) -> bytes:
        raw = str(image_base64 or "").strip()
        if not raw:
            return b""
        try:
            return base64.b64decode(raw, validate=False)
        except Exception:
            return b""

    def _build_edit_form_fields(
        self,
        prompt: str,
        image_base64: str = "",
        image_base64_list: Optional[List[str]] = None,
        provider: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Dict[str, Any], List[bytes]]:
        body = self._build_request_body(
            prompt, image_base64="", image_base64_list=None, provider=provider
        )
        images = self._normalize_image_list(image_base64_list)
        if not images and image_base64:
            images = self._normalize_image_list(image_base64)
        image_bytes_list: List[bytes] = []
        for item in images:
            decoded = self._decode_base64_image(item)
            if decoded:
                image_bytes_list.append(decoded)
        if not image_bytes_list:
            raise RuntimeError("图生图输入图片解码失败")

        fields: Dict[str, Any] = {}
        skip_keys = {
            "messages",
            "image",
            "images",
            "image_base64",
            "init_image",
            "input_image",
        }
        for key, value in body.items():
            if key in skip_keys:
                continue
            if value in (None, "", [], {}):
                continue
            if isinstance(value, (dict, list)):
                fields[key] = json.dumps(value, ensure_ascii=False)
            else:
                fields[key] = str(value)
        return fields, image_bytes_list

    def _normalize_request_body(self, body: Dict[str, Any]) -> None:
        response_format = body.get("response_format")
        if isinstance(response_format, str):
            text = response_format.strip()
            if text:
                body["response_format"] = {"type": text}
            else:
                body.pop("response_format", None)

    def _debug_enabled(self) -> bool:
        return bool(self._settings.get("debug_logging", False))

    def _debug(self, message: str):
        if self._debug_enabled():
            logger.info(f"[qq_draw] {message}")

    def _resolve_request_timeout_sec(
        self, provider: Optional[Dict[str, Any]] = None
    ) -> float:
        provider = provider or {}
        raw = provider.get(
            "request_timeout_sec", self._settings.get("request_timeout_sec", 300)
        )
        if isinstance(raw, dict):
            raw = raw.get("default", 300)
        try:
            value = float(raw)
        except Exception:
            value = 300.0
        if value <= 0:
            value = 300.0
        return value

    def _client_timeout(
        self, provider: Optional[Dict[str, Any]] = None
    ):
        total = self._resolve_request_timeout_sec(provider=provider)
        # Image generation can stream slowly; pin sock_read to the same budget.
        return aiohttp.ClientTimeout(
            total=total, connect=30, sock_connect=30, sock_read=total
        )

    def _default_http_headers(self, api_key: str = "") -> Dict[str, str]:
        # Some reverse proxies (Cloudflare) block non-browser clients with 403/1010.
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
        }
        key = str(api_key or "").strip()
        if key:
            headers["Authorization"] = f"Bearer {key}"
        return headers

    def _build_request_url(
        self,
        image_base64: str = "",
        image_base64_list: Optional[List[str]] = None,
        provider: Optional[Dict[str, Any]] = None,
    ) -> str:
        provider = provider or {}
        base_url = str(provider.get("base_url") or "").strip()
        images = self._normalize_image_list(image_base64_list)
        if not images and image_base64:
            images = self._normalize_image_list(image_base64)
        has_input_images = bool(images)
        endpoint_path = (
            str(
                provider.get("edit_endpoint_path")
                or provider.get("endpoint_path")
                or ""
            ).strip()
            if has_input_images
            else str(provider.get("endpoint_path") or "").strip()
        )
        if not base_url:
            raise ValueError("未配置 base_url（请在模型与路由里填写）")
        if not endpoint_path:
            endpoint_path = (
                "/v1/images/edits" if has_input_images else "/v1/images/generations"
            )
        try:
            from modules.model_catalog import join_endpoint_url
        except Exception:
            join_endpoint_url = None
        if join_endpoint_url is not None:
            return join_endpoint_url(base_url, endpoint_path)
        # fallback if catalog import fails
        base = base_url.rstrip("/")
        path = endpoint_path if endpoint_path.startswith("/") else "/" + endpoint_path
        if base.endswith("/v1") and path.startswith("/v1/"):
            path = path[3:]
        return base + path

    def _extract_image_bytes(self, payload: Any) -> Optional[bytes]:
        if isinstance(payload, bytes):
            return payload

        if isinstance(payload, str):
            text = payload.strip()
            if not text:
                return None
            if text.startswith("data:image") and "," in text:
                text = text.split(",", 1)[1]
            try:
                return base64.b64decode(text, validate=False)
            except Exception:
                return None

        if isinstance(payload, dict):
            for key in ("image_base64", "base64", "data", "b64_json"):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    result = self._extract_image_bytes(value)
                    if result:
                        return result
            for key in ("image_bytes", "bytes"):
                value = payload.get(key)
                if isinstance(value, bytes) and value:
                    return value
            for key in ("image_url", "url"):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    return None
        return None

    def _pick_image_url_from_text(self, text: str) -> str:
        raw = str(text or "").strip()
        if not raw:
            return ""
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = None
        if parsed is not None:
            candidate = self._pick_image_url_from_result(parsed)
            if candidate:
                return candidate

        markdown_urls = re.findall(r"!\[[^\]]*\]\((https?://[^)\s]+)\)", raw)
        if markdown_urls:
            return markdown_urls[-1].strip()

        urls = re.findall(r"https?://[^\s<>()\"']+", raw)
        if urls:
            return urls[-1].rstrip("，。,.!！?？;；")
        return ""

    def _iter_nested_values(self, value: Any, *, max_depth: int = 6):
        if max_depth <= 0:
            return
        if isinstance(value, dict):
            for item in value.values():
                yield item
                yield from self._iter_nested_values(item, max_depth=max_depth - 1)
        elif isinstance(value, list):
            for item in value:
                yield item
                yield from self._iter_nested_values(item, max_depth=max_depth - 1)

    def _pick_nested_image_url(self, result: Any) -> str:
        urls: list[str] = []
        for value in self._iter_nested_values(result):
            if isinstance(value, str):
                candidate = self._pick_image_url_from_text(value)
                if candidate:
                    urls.append(candidate)
            elif isinstance(value, dict):
                candidate = value.get("url") or value.get("image_url")
                if isinstance(candidate, str) and candidate.strip():
                    urls.append(candidate.strip())
                elif isinstance(candidate, dict):
                    nested = candidate.get("url") or candidate.get("image_url")
                    if isinstance(nested, str) and nested.strip():
                        urls.append(nested.strip())
        return urls[-1] if urls else ""

    def _iter_choice_contents(self, payload: Dict[str, Any]):
        choices = payload.get("choices")
        if not isinstance(choices, list):
            return
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            for holder_key in ("message", "delta"):
                holder = choice.get(holder_key)
                if not isinstance(holder, dict):
                    continue
                content = holder.get("content")
                if isinstance(content, str) and content.strip():
                    yield content
                elif isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict):
                            yield part

    def _pick_image_url_from_result(self, result: Any) -> str:
        if isinstance(result, dict):
            for key in ("image_url", "url"):
                value = result.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            data = result.get("data")
            if isinstance(data, list):
                for item in data:
                    if not isinstance(item, dict):
                        continue
                    for key in ("url", "image_url"):
                        value = item.get(key)
                        if isinstance(value, str) and value.strip():
                            return value.strip()
            content = result.get("content")
            if isinstance(content, list):
                for item in content:
                    if not isinstance(item, dict):
                        continue
                    candidate = item.get("url") or item.get("image_url")
                    if isinstance(candidate, str) and candidate.strip():
                        return candidate.strip()
                    source = item.get("source")
                    if isinstance(source, dict):
                        candidate = source.get("url") or source.get("image_url")
                        if isinstance(candidate, str) and candidate.strip():
                            return candidate.strip()
            for content in self._iter_choice_contents(result):
                if isinstance(content, str):
                    candidate = self._pick_image_url_from_text(content)
                    if candidate:
                        return candidate
                elif isinstance(content, dict):
                    candidate = content.get("url") or content.get("image_url")
                    if isinstance(candidate, str) and candidate.strip():
                        return candidate.strip()
                    if isinstance(candidate, dict):
                        nested = candidate.get("url") or candidate.get("image_url")
                        if isinstance(nested, str) and nested.strip():
                            return nested.strip()
                    source = content.get("source")
                    if isinstance(source, dict):
                        candidate = source.get("url") or source.get("image_url")
                        if isinstance(candidate, str) and candidate.strip():
                            return candidate.strip()
        return self._pick_nested_image_url(result)

    def _pick_image_bytes_from_result(self, result: Any) -> Optional[bytes]:
        direct = self._extract_image_bytes(result)
        if direct:
            return direct

        if isinstance(result, dict):
            data = result.get("data")
            if isinstance(data, list):
                for item in data:
                    candidate = self._extract_image_bytes(item)
                    if candidate:
                        return candidate
            content = result.get("content")
            if isinstance(content, list):
                for item in content:
                    if not isinstance(item, dict):
                        continue
                    item_type = str(item.get("type") or "").strip().lower()
                    if item_type in {"image", "embeddedresource", "resource"}:
                        candidate = self._extract_image_bytes(item)
                        if candidate:
                            return candidate
                        candidate = self._extract_image_bytes(item.get("source"))
                        if candidate:
                            return candidate
                    else:
                        candidate = self._extract_image_bytes(item)
                        if candidate:
                            return candidate
            structured = result.get("structuredContent")
            if structured is not None:
                candidate = self._extract_image_bytes(structured)
                if candidate:
                    return candidate
            for content in self._iter_choice_contents(result):
                candidate = self._extract_image_bytes(content)
                if candidate:
                    return candidate
            for value in self._iter_nested_values(result):
                candidate = self._extract_image_bytes(value)
                if candidate:
                    return candidate
        return None

    def _format_no_image_result(self, result: Any) -> str:
        try:
            summary = json.dumps(result, ensure_ascii=False, default=str)
        except Exception:
            summary = repr(result)
        if len(summary) > 500:
            summary = summary[:500].rstrip() + "..."
        return (
            "生图接口已返回结果，但当前插件没解析出图片。"
            "目前支持 base64、图片 URL、OpenAI data[]、chat choices[] 等常见结构。"
            f"返回摘要：{summary}"
        )

    def _extract_sse_payloads(self, text: str) -> list:
        payloads = []
        for line in str(text or "").splitlines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            item = line[5:].strip()
            if not item or item == "[DONE]":
                continue
            try:
                payloads.append(json.loads(item))
            except Exception:
                payloads.append(item)
        return payloads

    def _merge_sse_payloads(self, payloads: list) -> Any:
        if not payloads:
            return None

        last_error = None
        for item in payloads:
            if isinstance(item, dict) and isinstance(item.get("error"), dict):
                last_error = item

        if last_error is not None:
            return last_error

        urls = []
        images = []
        texts = []
        for item in payloads:
            if not isinstance(item, dict):
                continue
            candidate_url = self._pick_image_url_from_result(item)
            if candidate_url:
                urls.append(candidate_url)
            candidate_bytes = self._pick_image_bytes_from_result(item)
            if candidate_bytes:
                images.append(candidate_bytes)
            for choice in item.get("choices") or []:
                if not isinstance(choice, dict):
                    continue
                delta = choice.get("delta") or {}
                message = choice.get("message") or {}
                for holder in (delta, message):
                    if not isinstance(holder, dict):
                        continue
                    content = holder.get("content")
                    if isinstance(content, str) and content.strip():
                        texts.append(content.strip())
                    elif isinstance(content, list):
                        for part in content:
                            if not isinstance(part, dict):
                                continue
                            part_text = part.get("text") or part.get("content")
                            if isinstance(part_text, str) and part_text.strip():
                                texts.append(part_text.strip())
                            url = part.get("url") or part.get("image_url")
                            if isinstance(url, str) and url.strip():
                                urls.append(url.strip())
                            image_value = (
                                part.get("image_base64")
                                or part.get("b64_json")
                                or part.get("base64")
                            )
                            if isinstance(image_value, str) and image_value.strip():
                                candidate = self._extract_image_bytes(image_value)
                                if candidate:
                                    images.append(candidate)

        merged: Dict[str, Any] = {}
        images = [item for item in images if item]
        if urls:
            merged["image_url"] = urls[-1]
        if images:
            merged["image_bytes"] = images[-1]
        if texts:
            merged["text"] = "\n".join(texts)
        return merged or payloads[-1]

    def _save_temp_image(self, image_bytes: bytes) -> str:
        temp_dir = Path(tempfile.gettempdir()) / "live2d_llm_generated_images"
        temp_dir.mkdir(parents=True, exist_ok=True)
        file_path = temp_dir / f"qq_draw_{uuid.uuid4().hex}.png"
        file_path.write_bytes(image_bytes)
        return str(file_path)

    async def _download_image_bytes(
        self,
        url: str,
        headers: Dict[str, str],
        provider: Optional[Dict[str, Any]] = None,
    ) -> bytes:
        if aiohttp is None:
            raise RuntimeError("aiohttp 未安装，无法下载图片 URL")
        timeout = self._client_timeout(provider=provider)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=headers or None) as resp:
                if resp.status >= 400:
                    text = await resp.text()
                    raise RuntimeError(f"下载图片失败: HTTP {resp.status} {text[:200]}")
                return await resp.read()

    async def _call_image_api_with_provider(
        self,
        prompt: str,
        image_base64: str = "",
        image_base64_list: Optional[List[str]] = None,
        provider: Optional[Dict[str, Any]] = None,
    ) -> Any:
        if aiohttp is None:
            raise RuntimeError("aiohttp 未安装，无法调用生图接口")

        if not provider:
            raise RuntimeError(
                "没有可用的画图模型。请到「模型与路由」添加模型并勾选用途「画图」。"
            )
        api_key = self._resolve_provider_api_key(provider)
        if not api_key:
            raise RuntimeError(
                f"模型 {provider.get('name') or 'provider'} 未配置 API Key。"
                "请在模型与路由里填写，或设置对应环境变量（如 GROK_API_KEY）。"
            )

        images = self._normalize_image_list(image_base64_list)
        if not images and image_base64:
            images = self._normalize_image_list(image_base64)

        url = self._build_request_url(
            image_base64_list=images, provider=provider
        )
        body = self._build_request_body(
            prompt, image_base64_list=images, provider=provider
        )
        api_mode = str(provider.get("api_mode") or "images").strip().lower()
        headers = self._default_http_headers(api_key)

        timeout = self._client_timeout(provider=provider)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            if images and self._is_image_edit_endpoint(
                image_base64_list=images, provider=provider
            ):
                form = aiohttp.FormData()
                fields, image_bytes_list = self._build_edit_form_fields(
                    prompt, image_base64_list=images, provider=provider
                )
                for key, value in fields.items():
                    form.add_field(key, value)
                # OpenAI-style edits accept repeated "image" parts for multi-ref.
                for index, image_bytes in enumerate(image_bytes_list):
                    form.add_field(
                        "image",
                        image_bytes,
                        filename=f"input_{index + 1}.png",
                        content_type="image/png",
                    )
                debug_body = dict(fields)
                debug_body["image"] = f"<{len(image_bytes_list)} binary images omitted>"
                self._debug(
                    f"provider={provider.get('name')} mode={api_mode} model={fields.get('model', '')} url={url} multipart={json.dumps(debug_body, ensure_ascii=False)[:500]}"
                )
                async with session.post(url, headers=headers, data=form) as resp:
                    text = await resp.text()
                    self._debug(
                        f"provider={provider.get('name')} raw_status={resp.status} raw_response={text[:1000]}"
                    )
                    if resp.status >= 400:
                        raise RuntimeError(self._format_api_error(resp.status, text))
                    try:
                        parsed = json.loads(text)
                        self._debug(
                            f"provider={provider.get('name')} parsed_json={json.dumps(parsed, ensure_ascii=False)[:500]}"
                        )
                        return parsed
                    except Exception:
                        raise RuntimeError(f"接口返回不是合法 JSON: {text[:500]}")

            headers["Content-Type"] = "application/json"
            debug_body = dict(body)
            if "messages" in debug_body:
                debug_body["messages"] = "<messages omitted>"
            self._debug(
                f"provider={provider.get('name')} mode={api_mode} model={body.get('model', '')} url={url} body={json.dumps(debug_body, ensure_ascii=False)[:500]}"
            )
            async with session.post(url, headers=headers, json=body) as resp:
                text = await resp.text()
                self._debug(
                    f"provider={provider.get('name')} raw_status={resp.status} raw_response={text[:1000]}"
                )
                if resp.status >= 400:
                    raise RuntimeError(self._format_api_error(resp.status, text))
                sse_payloads = self._extract_sse_payloads(text)
                if sse_payloads:
                    self._debug(
                        f"provider={provider.get('name')} sse_chunks={len(sse_payloads)} first_chunk={json.dumps(sse_payloads[0], ensure_ascii=False)[:500]}"
                    )
                    merged = self._merge_sse_payloads(sse_payloads)
                    if isinstance(merged, dict) and isinstance(
                        merged.get("error"), dict
                    ):
                        error_text = json.dumps(merged, ensure_ascii=False)
                        self._debug(f"merged_error={error_text[:500]}")
                        raise RuntimeError(
                            self._format_api_error(resp.status, error_text)
                        )
                    self._debug(
                        f"provider={provider.get('name')} merged_result={json.dumps(merged, ensure_ascii=False)[:500]}"
                    )
                    return merged
                try:
                    parsed = json.loads(text)
                    self._debug(
                        f"provider={provider.get('name')} parsed_json={json.dumps(parsed, ensure_ascii=False)[:500]}"
                    )
                    return parsed
                except Exception:
                    raise RuntimeError(f"接口返回不是合法 JSON: {text[:500]}")

    async def _materialize_image_bytes(
        self,
        result: Any,
        provider: Optional[Dict[str, Any]] = None,
    ) -> bytes:
        image_bytes = self._pick_image_bytes_from_result(result)
        if image_bytes:
            return image_bytes

        image_url = self._pick_image_url_from_result(result)
        if not image_url:
            return b""

        api_key = self._resolve_provider_api_key(provider)
        headers = self._default_http_headers(api_key)
        return await self._download_image_bytes(
            image_url, headers, provider=provider
        )

    async def _call_image_api(
        self,
        prompt: str,
        image_base64: str = "",
        image_base64_list: Optional[List[str]] = None,
    ) -> Tuple[Any, Dict[str, Any], bytes]:
        images = self._normalize_image_list(image_base64_list)
        if not images and image_base64:
            images = self._normalize_image_list(image_base64)
        providers = self._build_provider_queue(image_base64_list=images)
        if not providers:
            raise RuntimeError(
                "没有可用的画图模型。请："
                "1) 在「模型与路由」给模型勾选用途「画图」；"
                "2) 打开 QQ生图 插件设置，把要用的模型加到「画图模型」执行链。"
            )
        errors: List[str] = []
        last_result: Any = None
        last_provider = providers[0]

        for provider in providers:
            name = str(provider.get("name") or "provider").strip() or "provider"
            try:
                self._debug(
                    f"trying provider={name} model={provider.get('model_name')} base_url={provider.get('base_url')} input_images={len(images)}"
                )
                result = await self._call_image_api_with_provider(
                    prompt, image_base64_list=images, provider=provider
                )
                last_result = result
                last_provider = provider
                image_bytes = await self._materialize_image_bytes(
                    result, provider=provider
                )
                if image_bytes:
                    self._debug(f"provider={name} succeeded")
                    return result, provider, image_bytes
                summary = self._format_no_image_result(result)
                errors.append(f"{name}: {summary}")
                self._debug(f"provider={name} returned no image, continue fallback")
            except Exception as exc:
                error_text = str(exc).strip() or repr(exc)
                errors.append(f"{name}: {error_text}")
                logger.warning(f"[qq_draw] provider failed name={name} err={error_text}")
                continue

        if last_result is not None and not errors:
            return last_result, last_provider, b""
        if last_result is not None and all("没解析出图片" in item for item in errors):
            return last_result, last_provider, b""
        joined = " | ".join(errors[:4]) if errors else "未知错误"
        raise RuntimeError(f"全部生图通道失败：{joined}")

    async def _load_image_base64_list_from_meta(
        self, images: Any, *, source: str = "attachment"
    ) -> List[str]:
        if not isinstance(images, list) or not images:
            return []
        try:
            from integrations.chat_gateway.media_utils import load_image_base64
        except Exception as exc:
            self._debug(f"load_image_base64 unavailable: {exc}")
            return []

        max_images = self._max_input_images()
        loaded: List[str] = []
        for index, image_meta in enumerate(images[:max_images]):
            try:
                value = str(
                    await asyncio.to_thread(load_image_base64, image_meta)
                ).strip()
            except Exception as exc:
                self._debug(f"load {source} image[{index}] failed: {exc}")
                continue
            if value:
                loaded.append(value)
        return loaded

    async def _load_context_image_base64_list(self, context: dict) -> List[str]:
        if not self._is_qq_context(context):
            return []
        if not bool(self._settings.get("image_to_image_enabled", True)):
            return []
        channel_meta = (context or {}).get("channel_meta") or {}
        images = channel_meta.get("images") or []
        return await self._load_image_base64_list_from_meta(
            images, source="attachment"
        )

    async def _load_reply_image_base64_list(self, context: dict) -> List[str]:
        if not self._is_qq_context(context):
            return []
        if not bool(self._settings.get("image_to_image_enabled", True)):
            return []
        channel_meta = (context or {}).get("channel_meta") or {}
        reply_meta = channel_meta.get("reply") or {}
        if not isinstance(reply_meta, dict):
            return []
        reply_message_id = str(reply_meta.get("message_id") or "").strip()
        if not reply_message_id:
            return []

        chat_service = (context or {}).get("chat_service")
        gateway = getattr(chat_service, "chat_gateway", None)
        if gateway is None:
            return []
        adapter = getattr(gateway, "adapters", {}).get("napcat_qq")
        if adapter is None or not hasattr(adapter, "fetch_message_by_id"):
            return []

        session_id = str(channel_meta.get("session_id") or "").strip()
        if not session_id:
            return []

        try:
            result = await adapter.fetch_message_by_id(
                session_id, reply_message_id, timeout=10
            )
        except Exception as exc:
            self._debug(f"fetch reply message failed: {exc}")
            return []
        if not isinstance(result, dict) or not result.get("ok"):
            self._debug(f"fetch reply message not ok: {result}")
            return []
        item = result.get("item")
        if not isinstance(item, dict):
            return []
        meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
        images = meta.get("images") or []
        return await self._load_image_base64_list_from_meta(images, source="reply")

    def _format_api_error(self, status: int, text: str) -> str:
        raw = (text or "").strip()
        message = raw[:500]
        code = ""
        try:
            payload = json.loads(raw)
            if isinstance(payload, dict):
                error_obj = payload.get("error")
                if isinstance(error_obj, dict):
                    detail = str(error_obj.get("message") or "").strip()
                    if detail:
                        message = detail
                    code = str(error_obj.get("code") or "").strip()
        except Exception:
            pass

        hint = ""
        lower_message = message.lower()
        if "field messages is required" in lower_message:
            hint = (
                "；这个站当前更像聊天补全接口，不是标准图片生成接口。"
                "请确认它是否真的支持 /v1/images/generations，或改成该站文档要求的图片模型/图片路径。"
            )
        elif "response_format" in lower_message:
            hint = "；请先把“额外请求体(JSON)”留空，或仅保留该站文档明确要求的字段。"
        elif "image rate limit exceeded" in lower_message:
            hint = "；图片额度或频率限制已触发，换 key、等额度恢复，或稍后重试。"
        elif status == 404:
            hint = "；图片接口路径可能不对，请检查是否真的是 /v1/images/generations。"
        elif status in {401, 403}:
            hint = "；请检查 API Key 是否有效，以及是否有图片模型权限。"
        elif status == 502:
            hint = "；网关或上游模型服务异常，可稍后重试，或更换模型名/站点。"

        code_text = f" [{code}]" if code else ""
        return f"HTTP {status}{code_text}: {message}{hint}"

    async def run(self, args: str, context: dict):
        if not self._is_qq_context(context):
            return "这个生图插件目前只给 QQ 入口用。"

        text = str(args or "").strip()
        prompt = self._extract_prompt(text)
        if not prompt:
            return (
                "用法：/画图 你的提示词 或 /画画 你的提示词；"
                "如果同时带 QQ 图片（可多张），会全部作为参考图做图生图。"
            )

        input_images = await self._load_context_image_base64_list(context)
        input_image_source = ""
        if input_images:
            input_image_source = "attachment"
        else:
            input_images = await self._load_reply_image_base64_list(context)
            if input_images:
                input_image_source = "reply"
        if input_images:
            self._debug(
                f"image_to_image inputs detected from QQ {input_image_source}: {len(input_images)}"
            )

        try:
            result, provider, image_bytes = await self._call_image_api(
                prompt, image_base64_list=input_images
            )
        except Exception as exc:
            error_text = str(exc).strip() or repr(exc)
            return f"调用生图接口失败：{error_text}"

        if not image_bytes:
            return self._format_no_image_result(result)

        try:
            image_path = await asyncio.to_thread(self._save_temp_image, image_bytes)
        except Exception as exc:
            return f"图片生成成功，但保存临时图片失败：{exc}"

        caption_tpl = str(
            self._settings.get("caption_template") or "🖼️ 已按你的要求画好了。"
        )
        provider_name = str((provider or {}).get("name") or "primary").strip() or "primary"
        # 这里保留 replace 逻辑作为底层防御，以防你未来在 config 里又加回了 {prompt}
        caption = (
            caption_tpl.replace("{prompt}", prompt)
            .replace("{provider}", provider_name)
            .replace("{model}", str((provider or {}).get("model_name") or ""))
        )
        success_suffix = (
            ""
            if provider_name in {"primary", "main", "default"}
            else f"（{provider_name} 兜底）"
        )
        return {
            "__type__": "gateway_image",
            "image_path": image_path,
            "caption": caption,
            "success_text": f"🖼️ 已把这张图发到 QQ 了{success_suffix}。",
            "fallback_text": "图已经生成出来了，但回发到 QQ 失败了。",
            "provider": provider_name,
            "model_name": str((provider or {}).get("model_name") or ""),
        }
