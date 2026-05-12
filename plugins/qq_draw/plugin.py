import asyncio
import base64
import json
import logging
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

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
        self._settings = {
            "base_url": self._read_setting(
                settings, "base_url", "https://api.sub2api.froge-ai.com"
            ),
            "api_mode": self._read_setting(settings, "api_mode", "chat"),
            "endpoint_path": self._read_setting(
                settings, "endpoint_path", "/v1/chat/completions"
            ),
            "edit_endpoint_path": self._read_setting(
                settings, "edit_endpoint_path", "/v1/images/edits"
            ),
            "api_key": self._read_setting(settings, "api_key", ""),
            "model_name": self._read_setting(settings, "model_name", "grok-2-image"),
            "size_value": self._read_setting(settings, "size_value", "1024x1024"),
            "quality": self._read_setting(settings, "quality", ""),
            "style": self._read_setting(settings, "style", ""),
            "negative_prompt": self._read_setting(settings, "negative_prompt", ""),
            "extra_body_json": self._read_setting(settings, "extra_body_json", "{}"),
            "request_timeout_sec": self._read_setting(
                settings, "request_timeout_sec", 180
            ),
            "caption_template": self._read_setting(
                settings, "caption_template", "🖼️ 已按你的要求画好了。"
            ),
            "image_to_image_enabled": bool(
                self._read_setting(settings, "image_to_image_enabled", True)
            ),
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

    def should_handle_direct(
        self, user_text: str, context: dict, matched_alias: str
    ) -> bool:
        text = str(user_text or "").strip()
        return any(text.startswith(prefix) for prefix in COMMAND_PREFIXES)

    def _extract_prompt(self, text: str) -> str:
        raw = str(text or "").strip()
        for prefix in COMMAND_PREFIXES:
            if raw.startswith(prefix):
                return raw[len(prefix) :].strip()
        return ""

    def _is_qq_context(self, context: dict) -> bool:
        source = str((context or {}).get("source") or "").strip().lower()
        return source in {"qq_gateway", "napcat_qq"}

    def _format_input_image_payload(self, image_base64: str) -> str:
        raw = str(image_base64 or "").strip()
        if not raw:
            return ""
        image_format = str(self._settings.get("input_image_format") or "data_url").strip().lower()
        if image_format == "base64":
            return raw
        return f"data:image/png;base64,{raw}"

    def _build_request_body(
        self, prompt: str, image_base64: str = ""
    ) -> Dict[str, Any]:
        input_image_payload = self._format_input_image_payload(image_base64)
        use_chat_image_part = bool(
            input_image_payload
            and self._settings.get("include_chat_image_part", True)
        )
        message_content: Any
        if use_chat_image_part:
            message_content = [
                {
                    "type": "text",
                    "text": prompt,
                },
                {
                    "type": "image_url",
                    "image_url": {
                        "url": input_image_payload,
                    },
                },
            ]
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
        model_value = str(self._settings.get("model_name") or "").strip()
        if model_value:
            body["model"] = model_value

        size_value = str(self._settings.get("size_value") or "").strip()
        if size_value:
            body["size"] = size_value

        quality_value = str(self._settings.get("quality") or "").strip()
        if quality_value:
            body["quality"] = quality_value

        style_value = str(self._settings.get("style") or "").strip()
        if style_value:
            body["style"] = style_value

        negative_value = str(self._settings.get("negative_prompt") or "").strip()
        if negative_value:
            body["negative_prompt"] = negative_value

        if input_image_payload and bool(
            self._settings.get("image_to_image_enabled", True)
        ):
            field_name = str(self._settings.get("input_image_field") or "image").strip()
            if field_name:
                body[field_name] = input_image_payload

        # 3. 合并自定义额外参数
        extra_args_raw = (
                str(self._settings.get("extra_body_json") or "{}").strip() or "{}"
        )
        extra_args = json.loads(extra_args_raw)
        if not isinstance(extra_args, dict):
            raise ValueError("extra_body_json 必须是 JSON 对象")
        body.update(extra_args)

        self._normalize_request_body(body)
        return body

    def _is_image_edit_endpoint(self, image_base64: str = "") -> bool:
        endpoint_path = (
            str(
                self._settings.get("edit_endpoint_path")
                or self._settings.get("endpoint_path")
                or ""
            ).strip().lower()
            if image_base64
            else str(self._settings.get("endpoint_path") or "").strip().lower()
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
        self, prompt: str, image_base64: str
    ) -> Tuple[Dict[str, Any], bytes]:
        body = self._build_request_body(prompt, image_base64="")
        image_bytes = self._decode_base64_image(image_base64)
        if not image_bytes:
            raise RuntimeError("图生图输入图片解码失败")

        fields: Dict[str, Any] = {}
        for key, value in body.items():
            if key in {"messages", "image", "image_base64", "init_image", "input_image"}:
                continue
            if value in (None, "", [], {}):
                continue
            if isinstance(value, (dict, list)):
                fields[key] = json.dumps(value, ensure_ascii=False)
            else:
                fields[key] = str(value)
        return fields, image_bytes

    def _normalize_request_body(self, body: Dict[str, Any]) -> None:
        response_format = body.get("response_format")
        if isinstance(response_format, str):
            text = response_format.strip()
            if text:
                body["response_format"] = {"type": text}
            else:
                body.pop("response_format", None)

    def _resolve_api_key(self) -> str:
        key = str(self._settings.get("api_key") or "").strip()
        if key:
            return key
        return str(os.getenv("GROK_API_KEY") or "").strip()

    def _debug_enabled(self) -> bool:
        return bool(self._settings.get("debug_logging", False))

    def _debug(self, message: str):
        if self._debug_enabled():
            logger.info(f"[qq_draw] {message}")

    def _resolve_request_timeout_sec(self) -> float:
        raw = self._settings.get("request_timeout_sec", 180)
        try:
            value = float(raw)
        except Exception:
            value = 180.0
        if value <= 0:
            value = 180.0
        return value

    def _build_request_url(self, image_base64: str = "") -> str:
        base_url = str(self._settings.get("base_url") or "").strip().rstrip("/")
        endpoint_path = (
            str(
                self._settings.get("edit_endpoint_path")
                or self._settings.get("endpoint_path")
                or ""
            ).strip()
            if image_base64
            else str(self._settings.get("endpoint_path") or "").strip()
        )
        if not base_url:
            raise ValueError("未配置 base_url")
        if not endpoint_path:
            raise ValueError("未配置 endpoint_path")
        if not endpoint_path.startswith("/"):
            endpoint_path = "/" + endpoint_path
        return base_url + endpoint_path

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
        return ""

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
        return None

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

    async def _download_image_bytes(self, url: str, headers: Dict[str, str]) -> bytes:
        if aiohttp is None:
            raise RuntimeError("aiohttp 未安装，无法下载图片 URL")
        timeout = aiohttp.ClientTimeout(total=self._resolve_request_timeout_sec())
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=headers or None) as resp:
                if resp.status >= 400:
                    text = await resp.text()
                    raise RuntimeError(f"下载图片失败: HTTP {resp.status} {text[:200]}")
                return await resp.read()

    async def _call_image_api(self, prompt: str, image_base64: str = "") -> Any:
        if aiohttp is None:
            raise RuntimeError("aiohttp 未安装，无法调用生图接口")

        api_key = self._resolve_api_key()
        if not api_key:
            raise RuntimeError(
                "未配置 API Key，请在 QQ生图 插件设置里填写，或设置环境变量 GROK_API_KEY"
            )

        url = self._build_request_url(image_base64=image_base64)
        body = self._build_request_body(prompt, image_base64=image_base64)
        api_mode = str(self._settings.get("api_mode") or "chat").strip().lower()
        headers = {
            "Authorization": f"Bearer {api_key}",
        }

        timeout = aiohttp.ClientTimeout(total=self._resolve_request_timeout_sec())
        async with aiohttp.ClientSession(timeout=timeout) as session:
            if image_base64 and self._is_image_edit_endpoint(image_base64=image_base64):
                form = aiohttp.FormData()
                fields, image_bytes = self._build_edit_form_fields(prompt, image_base64)
                for key, value in fields.items():
                    form.add_field(key, value)
                form.add_field(
                    "image",
                    image_bytes,
                    filename="input.png",
                    content_type="image/png",
                )
                debug_body = dict(fields)
                debug_body["image"] = "<binary image omitted>"
                self._debug(
                    f"mode={api_mode} model={fields.get('model', '')} url={url} multipart={json.dumps(debug_body, ensure_ascii=False)[:500]}"
                )
                async with session.post(url, headers=headers, data=form) as resp:
                    text = await resp.text()
                    self._debug(f"raw_status={resp.status} raw_response={text[:1000]}")
                    if resp.status >= 400:
                        raise RuntimeError(self._format_api_error(resp.status, text))
                    try:
                        parsed = json.loads(text)
                        self._debug(
                            f"parsed_json={json.dumps(parsed, ensure_ascii=False)[:500]}"
                        )
                        return parsed
                    except Exception:
                        raise RuntimeError(f"接口返回不是合法 JSON: {text[:500]}")

            headers["Content-Type"] = "application/json"
            debug_body = dict(body)
            if "messages" in debug_body:
                debug_body["messages"] = "<messages omitted>"
            self._debug(
                f"mode={api_mode} model={body.get('model', '')} url={url} body={json.dumps(debug_body, ensure_ascii=False)[:500]}"
            )
            async with session.post(url, headers=headers, json=body) as resp:
                text = await resp.text()
                self._debug(f"raw_status={resp.status} raw_response={text[:1000]}")
                if resp.status >= 400:
                    raise RuntimeError(self._format_api_error(resp.status, text))
                sse_payloads = self._extract_sse_payloads(text)
                if sse_payloads:
                    self._debug(
                        f"sse_chunks={len(sse_payloads)} first_chunk={json.dumps(sse_payloads[0], ensure_ascii=False)[:500]}"
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
                        f"merged_result={json.dumps(merged, ensure_ascii=False)[:500]}"
                    )
                    return merged
                try:
                    parsed = json.loads(text)
                    self._debug(
                        f"parsed_json={json.dumps(parsed, ensure_ascii=False)[:500]}"
                    )
                    return parsed
                except Exception:
                    raise RuntimeError(f"接口返回不是合法 JSON: {text[:500]}")

    async def _load_first_context_image_base64(self, context: dict) -> str:
        if not self._is_qq_context(context):
            return ""
        if not bool(self._settings.get("image_to_image_enabled", True)):
            return ""
        channel_meta = (context or {}).get("channel_meta") or {}
        images = channel_meta.get("images") or []
        if not isinstance(images, list) or not images:
            return ""
        try:
            from integrations.chat_gateway.media_utils import load_image_base64
        except Exception as exc:
            self._debug(f"load_image_base64 unavailable: {exc}")
            return ""
        try:
            return str(
                await asyncio.to_thread(load_image_base64, images[0])
            ).strip()
        except Exception as exc:
            self._debug(f"load first QQ image failed: {exc}")
            return ""

    async def _load_reply_image_base64(self, context: dict) -> str:
        if not self._is_qq_context(context):
            return ""
        if not bool(self._settings.get("image_to_image_enabled", True)):
            return ""
        channel_meta = (context or {}).get("channel_meta") or {}
        reply_meta = channel_meta.get("reply") or {}
        if not isinstance(reply_meta, dict):
            return ""
        reply_message_id = str(reply_meta.get("message_id") or "").strip()
        if not reply_message_id:
            return ""

        chat_service = (context or {}).get("chat_service")
        gateway = getattr(chat_service, "chat_gateway", None)
        if gateway is None:
            return ""
        adapter = getattr(gateway, "adapters", {}).get("napcat_qq")
        if adapter is None or not hasattr(adapter, "fetch_message_by_id"):
            return ""

        session_id = str(channel_meta.get("session_id") or "").strip()
        if not session_id:
            return ""

        try:
            from integrations.chat_gateway.media_utils import load_image_base64
        except Exception as exc:
            self._debug(f"reply load_image_base64 unavailable: {exc}")
            return ""

        try:
            result = await adapter.fetch_message_by_id(
                session_id, reply_message_id, timeout=10
            )
        except Exception as exc:
            self._debug(f"fetch reply message failed: {exc}")
            return ""
        if not isinstance(result, dict) or not result.get("ok"):
            self._debug(f"fetch reply message not ok: {result}")
            return ""
        item = result.get("item")
        if not isinstance(item, dict):
            return ""
        meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
        images = meta.get("images") or []
        if not isinstance(images, list) or not images:
            return ""
        try:
            return str(
                await asyncio.to_thread(load_image_base64, images[0])
            ).strip()
        except Exception as exc:
            self._debug(f"load reply image failed: {exc}")
            return ""

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
            return "用法：/画图 你的提示词 或 /画画 你的提示词；如果同时带一张 QQ 图片，就会优先按图生图处理。"

        input_image_base64 = await self._load_first_context_image_base64(context)
        input_image_source = ""
        if input_image_base64:
            input_image_source = "attachment"
        else:
            input_image_base64 = await self._load_reply_image_base64(context)
            if input_image_base64:
                input_image_source = "reply"
        if input_image_base64:
            self._debug(
                f"image_to_image input detected from QQ {input_image_source}"
            )

        try:
            result = await self._call_image_api(
                prompt, image_base64=input_image_base64
            )
        except Exception as exc:
            error_text = str(exc).strip() or repr(exc)
            return f"调用生图接口失败：{error_text}"

        image_bytes = self._pick_image_bytes_from_result(result)
        if not image_bytes:
            image_url = self._pick_image_url_from_result(result)
            if image_url:
                try:
                    headers = {}
                    api_key = self._resolve_api_key()
                    if api_key:
                        headers["Authorization"] = f"Bearer {api_key}"
                    image_bytes = await self._download_image_bytes(image_url, headers)
                except Exception as exc:
                    return f"图片地址已返回，但下载失败：{exc}"

        if not image_bytes:
            if isinstance(result, dict):
                text_hint = str(result.get("text") or "").strip()
                if text_hint:
                    return (
                        "生图接口已返回内容，但当前没有拿到图片或图片链接。"
                        f"返回文本摘要：{text_hint[:180]}"
                    )
            return (
                "生图接口已返回结果，但当前插件没解析出图片。"
                "目前支持 base64 字段 image_base64 / base64 / data / b64_json，或返回图片 URL。"
            )

        try:
            image_path = await asyncio.to_thread(self._save_temp_image, image_bytes)
        except Exception as exc:
            return f"图片生成成功，但保存临时图片失败：{exc}"

        caption_tpl = str(
            self._settings.get("caption_template") or "🖼️ 已按你的要求画好了。"
        )
        # 这里保留 replace 逻辑作为底层防御，以防你未来在 config 里又加回了 {prompt}
        caption = caption_tpl.replace("{prompt}", prompt)
        return {
            "__type__": "gateway_image",
            "image_path": image_path,
            "caption": caption,
            "success_text": "🖼️ 已把这张图发到 QQ 了。",
            "fallback_text": "图已经生成出来了，但回发到 QQ 失败了。",
        }
