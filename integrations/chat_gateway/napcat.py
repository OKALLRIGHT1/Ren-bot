from __future__ import annotations

import asyncio
import base64
import json
import mimetypes
import os
import re
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple
from urllib import error, request

try:
    from config import (
        NAPCAT_ALLOW_GROUP,
        NAPCAT_ALLOW_PRIVATE,
        NAPCAT_API_BASE,
        NAPCAT_API_TOKEN,
        NAPCAT_GROUP_REQUIRE_AT,
        NAPCAT_REPLY_ENABLED,
    )
except Exception:
    NAPCAT_ALLOW_GROUP = False
    NAPCAT_ALLOW_PRIVATE = True
    NAPCAT_API_BASE = "http://127.0.0.1:3000"
    NAPCAT_API_TOKEN = ""
    NAPCAT_GROUP_REQUIRE_AT = True
    NAPCAT_REPLY_ENABLED = True

try:
    from config import NAPCAT_OWNER_USER_IDS
except Exception:
    NAPCAT_OWNER_USER_IDS = []

try:
    from config import NAPCAT_OWNER_LABEL
except Exception:
    NAPCAT_OWNER_LABEL = "主人"


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ALLOWED_FILE_ROOTS = (
    PROJECT_ROOT / "audio_cache",
    PROJECT_ROOT / "temp_audio",
    PROJECT_ROOT / "data" / "outbound",
    PROJECT_ROOT / "data" / "qq_file_browser",
    PROJECT_ROOT / "plugins" / "meme_pack" / "assets",
)

try:
    from config import NAPCAT_IMAGE_VISION_ENABLED
except Exception:
    NAPCAT_IMAGE_VISION_ENABLED = True

try:
    from config import NAPCAT_IMAGE_PROMPT
except Exception:
    NAPCAT_IMAGE_PROMPT = (
        "请客观详细描述这张QQ图片的内容，并提取其中可用于回复的关键信息。"
    )

from .base import BaseChatAdapter, ChatMessageEvent
from .components import component, components_to_dicts


class NapCatOneBotAdapter(BaseChatAdapter):
    name = "napcat_qq"

    def __init__(
        self,
        *,
        api_base: str = NAPCAT_API_BASE,
        api_token: str = NAPCAT_API_TOKEN,
        reply_enabled: bool = NAPCAT_REPLY_ENABLED,
        allow_group: bool = NAPCAT_ALLOW_GROUP,
        allow_private: bool = NAPCAT_ALLOW_PRIVATE,
        group_require_at: bool = NAPCAT_GROUP_REQUIRE_AT,
        owner_user_ids: Optional[List[str]] = None,
        owner_label: str = NAPCAT_OWNER_LABEL,
        image_vision_enabled: bool = NAPCAT_IMAGE_VISION_ENABLED,
        image_prompt: str = NAPCAT_IMAGE_PROMPT,
        filter_mode: str = "off",
        user_whitelist: Optional[List[str]] = None,
        user_blacklist: Optional[List[str]] = None,
        group_whitelist: Optional[List[str]] = None,
        group_blacklist: Optional[List[str]] = None,
        group_no_at_keywords: Optional[List[str]] = None,
        ws_action_sender: Optional[
            Callable[[str, Dict[str, Any], float], Awaitable[Dict[str, Any]]]
        ] = None,
    ):
        self.api_base = str(api_base or "").rstrip("/")
        self.api_token = str(api_token or "").strip()
        self.reply_enabled = bool(reply_enabled)
        self.allow_group = bool(allow_group)
        self.allow_private = bool(allow_private)
        self.group_require_at = bool(group_require_at)
        raw_owner_ids = (
            owner_user_ids
            if isinstance(owner_user_ids, list)
            else NAPCAT_OWNER_USER_IDS
        )
        self.owner_user_ids = {
            str(item).strip() for item in (raw_owner_ids or []) if str(item).strip()
        }
        self.owner_label = str(owner_label or "主人").strip() or "主人"
        self.image_vision_enabled = bool(image_vision_enabled)
        self.image_prompt = (
            str(image_prompt or "").strip()
            or "请客观详细描述这张QQ图片的内容，并提取其中可用于回复的关键信息。"
        )
        self.filter_mode = str(filter_mode or "off").strip().lower()
        if self.filter_mode not in {"off", "whitelist", "blacklist"}:
            self.filter_mode = "off"
        self.user_whitelist = {
            str(item).strip() for item in (user_whitelist or []) if str(item).strip()
        }
        self.user_blacklist = {
            str(item).strip() for item in (user_blacklist or []) if str(item).strip()
        }
        self.group_whitelist = {
            str(item).strip() for item in (group_whitelist or []) if str(item).strip()
        }
        self.group_blacklist = {
            str(item).strip() for item in (group_blacklist or []) if str(item).strip()
        }
        self.group_no_at_keywords = [
            str(item).strip()
            for item in (group_no_at_keywords or [])
            if str(item).strip()
        ]
        self.ws_action_sender = ws_action_sender
        self.allowed_file_roots = self._build_allowed_file_roots()

    def _build_allowed_file_roots(self) -> List[Path]:
        roots = list(DEFAULT_ALLOWED_FILE_ROOTS)
        extra = str(os.getenv("NAPCAT_ALLOWED_FILE_ROOTS", "") or "").strip()
        if extra:
            for item in re.split(r"[;\n]+", extra):
                text = item.strip()
                if text:
                    roots.append(Path(text).expanduser())

        resolved: List[Path] = []
        for root in roots:
            try:
                candidate = root.resolve()
            except Exception:
                candidate = root.absolute()
            if candidate not in resolved:
                resolved.append(candidate)
        return resolved

    @staticmethod
    def _is_relative_to(path: Path, base: Path) -> bool:
        try:
            return path.is_relative_to(base)
        except AttributeError:
            return path == base or base in path.parents

    def _file_allowed(self, path: Path) -> bool:
        return any(self._is_relative_to(path, root) for root in self.allowed_file_roots)

    def _file_blocked_result(self, session_id: str, path: Path, kind: str) -> Dict[str, Any]:
        return {
            "ok": False,
            "reason": f"{kind}_path_not_allowed",
            "session_id": session_id,
            f"{kind}_path": str(path),
            "allowed_roots": [str(root) for root in self.allowed_file_roots],
        }

    def set_ws_action_sender(
        self,
        sender: Optional[
            Callable[[str, Dict[str, Any], float], Awaitable[Dict[str, Any]]]
        ],
    ) -> None:
        self.ws_action_sender = sender

    def set_group_no_at_keywords(self, keywords: Optional[List[str]]) -> None:
        self.group_no_at_keywords = [
            str(item).strip() for item in (keywords or []) if str(item).strip()
        ]

    def _passes_filter(self, message_type: str, user_id: str, group_id: Any) -> bool:
        mode = self.filter_mode
        if mode == "off":
            return True

        user_key = str(user_id or "").strip()
        group_key = str(group_id or "").strip()

        if user_key and user_key in self.owner_user_ids:
            return True

        if mode == "whitelist":
            if message_type == "group":
                return (group_key and group_key in self.group_whitelist) or (
                    user_key and user_key in self.user_whitelist
                )
            return bool(user_key and user_key in self.user_whitelist)

        if mode == "blacklist":
            if message_type == "group":
                if group_key and group_key in self.group_blacklist:
                    return False
                if user_key and user_key in self.user_blacklist:
                    return False
                return True
            return not bool(user_key and user_key in self.user_blacklist)

        return True

    def _allow_group_without_at(self, raw_message: str) -> bool:
        if not self.group_no_at_keywords:
            return False
        text = str(raw_message or "").strip()
        if not text:
            return False
        low = text.lower()
        for key in self.group_no_at_keywords:
            if key.startswith("re:"):
                pattern = key[3:]
                try:
                    if re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL):
                        return True
                except re.error:
                    continue
            elif key.lower() in low:
                return True
        return False

    def _message_targets_self(self, payload: Dict[str, Any], self_id: str) -> bool:
        if not self_id:
            return False
        message = payload.get("message")
        if isinstance(message, list):
            for seg in message:
                if not isinstance(seg, dict):
                    continue
                if str(seg.get("type") or "").lower() != "at":
                    continue
                data = seg.get("data") or {}
                qq = str(data.get("qq") or "").strip()
                if qq == self_id or qq == "all":
                    return True
        raw_text = str(payload.get("raw_message") or payload.get("message") or "")
        return f"[CQ:at,qq={self_id}]" in raw_text or "[CQ:at,qq=all]" in raw_text

    def _strip_self_mentions(self, text: str, self_id: str) -> str:
        cleaned = str(text or "")
        if self_id:
            cleaned = cleaned.replace(f"[CQ:at,qq={self_id}]", " ")
        cleaned = cleaned.replace("[CQ:at,qq=all]", " ")
        return " ".join(cleaned.split())

    def _extract_image_segment(self, seg: Dict[str, Any]) -> Dict[str, Any]:
        data = seg.get("data") or {}
        image = {
            "url": str(data.get("url") or "").strip(),
            "file": str(data.get("file") or "").strip(),
            "summary": str(data.get("summary") or "").strip(),
            "name": str(data.get("name") or "").strip(),
        }
        return {key: value for key, value in image.items() if value}

    def _extract_file_segment(self, seg: Dict[str, Any]) -> Dict[str, Any]:
        data = seg.get("data") or {}
        file_payload = {
            "file": str(data.get("file") or "").strip(),
            "name": str(data.get("name") or "").strip(),
            "url": str(data.get("url") or "").strip(),
            "size": data.get("size"),
            "id": str(data.get("id") or data.get("file_id") or "").strip(),
        }
        return {
            key: value
            for key, value in file_payload.items()
            if value not in (None, "", [])
        }

    def _extract_reply_segment(self, seg: Dict[str, Any]) -> Dict[str, Any]:
        data = seg.get("data") or {}
        reply_payload = {
            "message_id": str(
                data.get("id") or data.get("message_id") or data.get("msg_id") or ""
            ).strip(),
            "user_id": str(data.get("user_id") or "").strip(),
            "text": str(data.get("text") or "").strip(),
        }
        return {
            key: value
            for key, value in reply_payload.items()
            if value not in (None, "", [])
        }

    def _extract_reply_from_raw_message(self, raw_text: str) -> Dict[str, Any]:
        text = str(raw_text or "")
        if not text:
            return {}
        match = re.search(r"\[CQ:reply,([^\]]+)\]", text, flags=re.IGNORECASE)
        if not match:
            return {}
        attrs_text = match.group(1)
        attrs: Dict[str, str] = {}
        for part in attrs_text.split(","):
            if "=" not in part:
                continue
            key, value = part.split("=", 1)
            key = str(key or "").strip().lower()
            value = str(value or "").strip()
            if key:
                attrs[key] = value
        reply_payload = {
            "message_id": str(
                attrs.get("id") or attrs.get("message_id") or attrs.get("msg_id") or ""
            ).strip(),
            "user_id": str(attrs.get("user_id") or "").strip(),
        }
        return {
            key: value
            for key, value in reply_payload.items()
            if value not in (None, "", [])
        }

    def _segment_to_text(self, seg: Dict[str, Any], self_id: str) -> str:
        seg_type = str(seg.get("type") or "").strip().lower()
        data = seg.get("data") or {}
        if seg_type == "text":
            return str(data.get("text") or "")
        if seg_type == "at":
            qq = str(data.get("qq") or "").strip()
            if qq == "all":
                return "@全体成员"
            if self_id and qq == self_id:
                return ""
            return f"@{qq}" if qq else ""
        placeholders = {
            "image": "[图片]",
            "face": "[表情]",
            "file": "[文件]",
            "video": "[视频]",
            "record": "[语音]",
            "reply": "",
            "json": "[卡片消息]",
            "xml": "[卡片消息]",
        }
        return placeholders.get(seg_type, "")

    def _extract_message_components(
        self, payload: Dict[str, Any], self_id: str
    ) -> List[Dict[str, Any]]:
        message = payload.get("message")
        items = []
        if isinstance(message, list):
            for seg in message:
                if not isinstance(seg, dict):
                    continue
                seg_type = str(seg.get("type") or "").strip().lower() or "unknown"
                data = seg.get("data") if isinstance(seg.get("data"), dict) else {}
                text = self._segment_to_text(seg, self_id)
                if seg_type == "image":
                    img = self._extract_image_segment(seg)
                    items.append(component("image", text or "[图片]", img or data))
                elif seg_type == "file":
                    file_payload = self._extract_file_segment(seg)
                    items.append(component("file", text or "[文件]", file_payload or data))
                elif seg_type == "reply":
                    reply_payload = self._extract_reply_segment(seg)
                    items.append(component("reply", "", reply_payload or data))
                elif seg_type == "at":
                    items.append(component("at", text, data))
                elif seg_type == "text":
                    items.append(component("text", text, data))
                else:
                    items.append(component(seg_type, text, data))
        else:
            raw_text = str(payload.get("raw_message") or payload.get("message") or "")
            if raw_text:
                items.append(component("text", raw_text, {}))
            reply_payload = self._extract_reply_from_raw_message(raw_text)
            if reply_payload:
                items.append(component("reply", "", reply_payload))
        return components_to_dicts(items)

    def _extract_message_payload(
        self,
        payload: Dict[str, Any],
        self_id: str,
    ) -> Tuple[str, List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
        message = payload.get("message")
        images: List[Dict[str, Any]] = []
        files: List[Dict[str, Any]] = []
        reply_meta: Dict[str, Any] = {}
        if isinstance(message, list):
            parts: List[str] = []
            for seg in message:
                if not isinstance(seg, dict):
                    continue
                seg_type = str(seg.get("type") or "").strip().lower()
                if seg_type == "image":
                    image_payload = self._extract_image_segment(seg)
                    if image_payload:
                        images.append(image_payload)
                elif seg_type == "file":
                    file_payload = self._extract_file_segment(seg)
                    if file_payload:
                        files.append(file_payload)
                elif seg_type == "reply":
                    reply_payload = self._extract_reply_segment(seg)
                    if reply_payload:
                        reply_meta = reply_payload
                text = self._segment_to_text(seg, self_id)
                if text:
                    parts.append(text)
            if parts or images or files:
                return " ".join(" ".join(parts).split()), images, files, reply_meta
        raw_text = str(payload.get("raw_message") or payload.get("message") or "")
        if not reply_meta:
            reply_meta = self._extract_reply_from_raw_message(raw_text)
        return " ".join(raw_text.split()), images, files, reply_meta

    def _parse_session(self, session_id: str) -> Tuple[str, str]:
        session_text = str(session_id or "").strip()
        if ":" not in session_text:
            raise ValueError(f"Invalid session id: {session_id}")
        chat_type, peer_id = session_text.split(":", 1)
        chat_type = chat_type.strip().lower()
        peer_id = peer_id.strip()
        if not peer_id:
            raise ValueError(f"Invalid session id: {session_id}")
        return chat_type, peer_id

    def _build_send_action(self, session_id: str, message: Any) -> Dict[str, Any]:
        chat_type, peer_id = self._parse_session(session_id)
        if chat_type == "group":
            if not self.allow_group:
                return {
                    "ok": False,
                    "reason": "group_disabled",
                    "session_id": session_id,
                }
            return {
                "ok": True,
                "action": "send_group_msg",
                "payload": {"group_id": int(peer_id), "message": message},
            }
        if chat_type == "private":
            if not self.allow_private:
                return {
                    "ok": False,
                    "reason": "private_disabled",
                    "session_id": session_id,
                }
            return {
                "ok": True,
                "action": "send_private_msg",
                "payload": {"user_id": int(peer_id), "message": message},
            }
        return {
            "ok": False,
            "reason": f"unsupported_session_type:{chat_type}",
            "session_id": session_id,
        }

    async def _send_action(
        self, session_id: str, action: str, payload: Dict[str, Any], **kwargs: Any
    ) -> Any:
        timeout = float(kwargs.get("timeout") or 8)
        skip_http_fallback = bool(kwargs.get("skip_http_fallback"))
        ws_result = None

        if self.ws_action_sender is not None:
            try:
                ws_result = await self.ws_action_sender(action, payload, timeout)
            except Exception as exc:
                ws_result = {
                    "ok": False,
                    "reason": str(exc),
                    "transport": "websocket",
                    "session_id": session_id,
                }
            if isinstance(ws_result, dict):
                ws_result.setdefault("session_id", session_id)
                ws_result.setdefault("transport", "websocket")
            if isinstance(ws_result, dict) and ws_result.get("ok"):
                return ws_result
            if skip_http_fallback and isinstance(ws_result, dict):
                return ws_result

        if skip_http_fallback:
            return {
                "ok": False,
                "reason": "ws_unavailable",
                "transport": "websocket",
                "session_id": session_id,
            }

        if not self.api_base:
            if isinstance(ws_result, dict):
                return ws_result
            return {"ok": False, "reason": "api_base_missing", "session_id": session_id}

        url = f"{self.api_base}/{action}"

        def _post() -> Dict[str, Any]:
            raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            req = request.Request(url, data=raw, method="POST")
            req.add_header("Content-Type", "application/json; charset=utf-8")
            if self.api_token:
                req.add_header("Authorization", f"Bearer {self.api_token}")
            with request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                status = getattr(resp, "status", 200)
            try:
                parsed = json.loads(body) if body else {}
            except Exception:
                parsed = {"raw": body}
            return {
                "ok": True,
                "status": status,
                "session_id": session_id,
                "response": parsed,
                "transport": "http",
            }

        try:
            http_result = await asyncio.to_thread(_post)
            if isinstance(ws_result, dict) and not ws_result.get("ok"):
                http_result["ws_fallback"] = ws_result
            return http_result
        except error.HTTPError as exc:
            body = (
                exc.read().decode("utf-8", errors="replace")
                if hasattr(exc, "read")
                else ""
            )
            result = {
                "ok": False,
                "reason": f"http_error:{exc.code}",
                "session_id": session_id,
                "body": body,
                "transport": "http",
            }
            if isinstance(ws_result, dict):
                result["ws_fallback"] = ws_result
            return result
        except Exception as exc:
            result = {
                "ok": False,
                "reason": str(exc),
                "session_id": session_id,
                "transport": "http",
            }
            if isinstance(ws_result, dict):
                result["ws_fallback"] = ws_result
            return result

    async def call_action(
        self,
        action: str,
        params: Optional[Dict[str, Any]] = None,
        *,
        timeout: float = 8.0,
        skip_http_fallback: bool = False,
    ) -> Any:
        action_name = str(action or "").strip()
        if not action_name:
            return {"ok": False, "reason": "empty_action"}
        payload = params if isinstance(params, dict) else {}
        return await self._send_action(
            "",
            action_name,
            payload,
            timeout=timeout,
            skip_http_fallback=skip_http_fallback,
        )

    def normalize_event(self, payload: Dict[str, Any]) -> Optional[ChatMessageEvent]:
        post_type = str(payload.get("post_type") or "")
        message_type = str(payload.get("message_type") or "")
        self_id = str(payload.get("self_id") or "")
        raw_message, images, files, reply_meta = self._extract_message_payload(
            payload, self_id
        )
        components = self._extract_message_components(payload, self_id)
        if post_type != "message" or not raw_message:
            return None

        user_id = str(payload.get("user_id") or "")
        if self_id and user_id and self_id == user_id:
            return None

        if not self._passes_filter(message_type, user_id, payload.get("group_id")):
            return None

        if message_type == "group":
            if not self.allow_group:
                return None
            if self.group_require_at and not self._message_targets_self(
                payload, self_id
            ):
                if not self._allow_group_without_at(raw_message):
                    return None
            session_id = f"group:{payload.get('group_id')}"
            raw_message = self._strip_self_mentions(raw_message, self_id).strip()
            if not raw_message:
                return None
        else:
            if not self.allow_private:
                return None
            session_id = f"private:{user_id}"

        sender = payload.get("sender") or {}
        sender_name = str(sender.get("card") or sender.get("nickname") or user_id)
        is_owner = bool(user_id and user_id in self.owner_user_ids)

        return ChatMessageEvent(
            source="qq_gateway",
            channel="qq",
            user_id=user_id,
            session_id=session_id,
            text=raw_message,
            metadata={
                "adapter": "napcat_qq",
                "message_type": message_type,
                "group_id": payload.get("group_id"),
                "self_id": self_id,
                "message_id": payload.get("message_id"),
                "sender_name": sender_name,
                "sender": sender,
                "is_owner": is_owner,
                "owner_label": self.owner_label,
                "sender_role": "owner" if is_owner else "contact",
                "images": images,
                "has_image": bool(images),
                "image_count": len(images),
                "files": files,
                "has_file": bool(files),
                "file_count": len(files),
                "reply": reply_meta,
                "components": components,
                "image_vision_enabled": self.image_vision_enabled,
                "image_prompt": self.image_prompt,
                "filter_mode": self.filter_mode,
            },
        )

    async def fetch_message_by_id(self, session_id: str, message_id: str, **kwargs: Any) -> Any:
        msg_id = str(message_id or "").strip()
        if not msg_id:
            return {
                "ok": False,
                "reason": "empty_message_id",
                "session_id": session_id,
            }
        payload: Dict[str, Any]
        if msg_id.isdigit():
            payload = {"message_id": int(msg_id)}
        else:
            payload = {"message_id": msg_id}
        result = await self._send_action(
            session_id,
            "get_msg",
            payload,
            timeout=float(kwargs.get("timeout") or 10),
        )
        if not isinstance(result, dict) or not result.get("ok"):
            return result
        response = result.get("response")
        raw_item = response.get("data") if isinstance(response, dict) else response
        if not isinstance(raw_item, dict):
            result["item"] = None
            return result
        normalized = self._normalize_history_item(session_id, raw_item)
        result["item"] = normalized
        return result

    async def send_text(self, session_id: str, text: str, **kwargs: Any) -> Any:
        text = str(text or "").strip()
        if not text:
            return {"ok": False, "reason": "empty_text", "session_id": session_id}
        if not self.reply_enabled:
            return {
                "ok": False,
                "reason": "reply_disabled",
                "session_id": session_id,
                "text": text,
            }
        action_info = self._build_send_action(session_id, text)
        if not action_info.get("ok"):
            action_info.setdefault("text", text)
            return action_info
        result = await self._send_action(
            session_id, action_info["action"], action_info["payload"], **kwargs
        )
        if isinstance(result, dict):
            result.setdefault("text", text)
        return result

    def _extract_history_items(self, response: Any) -> List[Dict[str, Any]]:
        if isinstance(response, list):
            return [item for item in response if isinstance(item, dict)]
        if isinstance(response, dict):
            for key in ("messages", "records", "data", "list"):
                value = response.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
                if isinstance(value, dict):
                    nested = self._extract_history_items(value)
                    if nested:
                        return nested
        return []

    def _normalize_history_item(
        self, session_id: str, item: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        if not isinstance(item, dict):
            return None
        chat_type, peer_id = self._parse_session(session_id)
        self_id = str(item.get("self_id") or "").strip()
        text, images, files, reply_meta = self._extract_message_payload(item, self_id)
        components = self._extract_message_components(item, self_id)
        text = str(text or "").strip()
        if not text:
            return None

        sender = item.get("sender") or {}
        user_id = str(
            item.get("user_id")
            or sender.get("user_id")
            or sender.get("uin")
            or peer_id
            or ""
        ).strip()
        sender_name = str(
            sender.get("card") or sender.get("nickname") or user_id or peer_id
        ).strip()
        is_owner = bool(user_id and user_id in self.owner_user_ids)
        is_self = bool(self_id and user_id and self_id == user_id)
        ts_value = item.get("time")
        try:
            ts = int(float(ts_value))
        except Exception:
            ts = 0

        return {
            "ts": ts,
            "session_id": session_id,
            "role": "assistant" if is_self else "user",
            "content": text,
            "meta": {
                "adapter": "napcat_qq",
                "source": "qq_gateway",
                "message_type": "group" if chat_type == "group" else "private",
                "group_id": int(peer_id) if chat_type == "group" and peer_id.isdigit() else peer_id,
                "user_id": user_id,
                "sender_name": sender_name,
                "sender": sender,
                "is_owner": is_owner,
                "owner_label": self.owner_label,
                "sender_role": "owner" if is_owner else "contact",
                "message_id": item.get("message_id"),
                "images": images,
                "has_image": bool(images),
                "image_count": len(images),
                "files": files,
                "has_file": bool(files),
                "file_count": len(files),
                "reply": reply_meta,
                "components": components,
                "history_imported": True,
            },
        }

    async def fetch_recent_history(self, session_id: str, **kwargs: Any) -> Any:
        limit = max(1, min(200, int(kwargs.get("limit") or 80)))
        chat_type, peer_id = self._parse_session(session_id)
        timeout = float(kwargs.get("timeout") or 10)

        candidate_actions: List[Tuple[str, Dict[str, Any]]] = []
        if chat_type == "group":
            group_id: Any = int(peer_id) if peer_id.isdigit() else peer_id
            candidate_actions.extend(
                [
                    ("get_group_msg_history", {"group_id": group_id, "count": limit}),
                    ("get_group_msg_history", {"group_id": group_id, "limit": limit}),
                ]
            )
        elif chat_type == "private":
            user_id: Any = int(peer_id) if peer_id.isdigit() else peer_id
            candidate_actions.extend(
                [
                    ("get_friend_msg_history", {"user_id": user_id, "count": limit}),
                    ("get_private_msg_history", {"user_id": user_id, "count": limit}),
                    ("get_friend_msg_history", {"user_id": user_id, "limit": limit}),
                ]
            )
        else:
            return {
                "ok": False,
                "reason": f"unsupported_session_type:{chat_type}",
                "session_id": session_id,
                "items": [],
            }

        last_failure: Dict[str, Any] = {
            "ok": False,
            "reason": "history_fetch_failed",
            "session_id": session_id,
            "items": [],
        }
        for action, payload in candidate_actions:
            result = await self._send_action(
                session_id, action, payload, timeout=timeout
            )
            if not isinstance(result, dict) or not result.get("ok"):
                last_failure = (
                    result
                    if isinstance(result, dict)
                    else {
                        "ok": False,
                        "reason": "invalid_history_response",
                        "session_id": session_id,
                        "items": [],
                    }
                )
                continue
            response = result.get("response")
            items = self._extract_history_items(response)
            normalized = []
            for item in items:
                row = self._normalize_history_item(session_id, item)
                if row:
                    normalized.append(row)
            result["items"] = normalized
            result["history_action"] = action
            return result
        last_failure.setdefault("items", [])
        return last_failure

    async def send_voice(self, session_id: str, voice_path: str, **kwargs: Any) -> Any:
        path_text = str(voice_path or "").strip()
        if not path_text:
            return {"ok": False, "reason": "empty_voice_path", "session_id": session_id}
        if not self.reply_enabled:
            return {
                "ok": False,
                "reason": "reply_disabled",
                "session_id": session_id,
                "voice_path": path_text,
            }

        voice_file = Path(path_text).expanduser()
        try:
            voice_file = voice_file.resolve()
        except Exception:
            voice_file = voice_file.absolute()
        if not voice_file.exists() or not voice_file.is_file():
            return {
                "ok": False,
                "reason": "voice_file_missing",
                "session_id": session_id,
                "voice_path": str(voice_file),
            }
        if not self._file_allowed(voice_file):
            return self._file_blocked_result(session_id, voice_file, "voice")

        variants = self._build_voice_message_variants(voice_file)
        ws_timeout = float(kwargs.get("timeout") or 8)
        quick_timeout = min(ws_timeout, 3.0)
        last_result: Any = None

        for variant_name, message in variants:
            action_info = self._build_send_action(session_id, message)
            if not action_info.get("ok"):
                action_info.setdefault("voice_path", str(voice_file))
                action_info.setdefault("voice_variant", variant_name)
                last_result = action_info
                continue
            result = await self._send_action(
                session_id,
                action_info["action"],
                action_info["payload"],
                timeout=quick_timeout,
                skip_http_fallback=True,
                **kwargs,
            )
            if isinstance(result, dict):
                result.setdefault("voice_path", str(voice_file))
                result.setdefault("voice_variant", variant_name)
            if isinstance(result, dict) and result.get("ok"):
                return result
            if self._is_probable_ws_voice_delivery(result):
                return {
                    "ok": True,
                    "transport": "websocket_assumed",
                    "session_id": session_id,
                    "voice_path": str(voice_file),
                    "voice_variant": variant_name,
                    "response": result,
                    "assumed_success": True,
                }
            last_result = result

        primary_name, primary_message = variants[0]
        action_info = self._build_send_action(session_id, primary_message)
        if not action_info.get("ok"):
            action_info.setdefault("voice_path", str(voice_file))
            action_info.setdefault("voice_variant", primary_name)
            return action_info
        result = await self._send_action(
            session_id, action_info["action"], action_info["payload"], **kwargs
        )
        if isinstance(result, dict):
            result.setdefault("voice_path", str(voice_file))
            result.setdefault("voice_variant", primary_name)
            if last_result is not None:
                result.setdefault("voice_ws_attempt", last_result)
            if self._is_probable_ws_voice_delivery(last_result):
                return {
                    "ok": True,
                    "transport": "websocket_assumed",
                    "session_id": session_id,
                    "voice_path": str(voice_file),
                    "voice_variant": primary_name,
                    "response": result,
                    "voice_ws_attempt": last_result,
                    "assumed_success": True,
                }
        return result

    def _build_voice_message_variants(self, voice_file: Path) -> List[Tuple[str, Any]]:
        absolute_path = str(voice_file)
        posix_path = voice_file.as_posix()
        file_uri = voice_file.as_uri()
        cq_uri = f"[CQ:record,file={file_uri}]"
        cq_path = f"[CQ:record,file={absolute_path}]"
        cq_posix = f"[CQ:record,file={posix_path}]"
        return [
            (
                "record_file_uri",
                [{"type": "record", "data": {"file": file_uri}}],
            ),
            (
                "record_absolute_path",
                [{"type": "record", "data": {"file": absolute_path}}],
            ),
            (
                "record_posix_path",
                [{"type": "record", "data": {"file": posix_path}}],
            ),
            ("cq_record_file_uri", cq_uri),
            ("cq_record_absolute_path", cq_path),
            ("cq_record_posix_path", cq_posix),
        ]

    def _is_probable_ws_voice_delivery(self, result: Any) -> bool:
        if not isinstance(result, dict):
            return False
        if result.get("ok"):
            return False
        transport = str(result.get("transport") or "").strip().lower()
        if transport != "websocket":
            return False
        reason = str(result.get("reason") or "").strip().lower()
        return reason in {"", "timeout"}

    async def send_image(self, session_id: str, image_path: str, **kwargs: Any) -> Any:
        path_text = str(image_path or "").strip()
        if not path_text:
            return {"ok": False, "reason": "empty_image_path", "session_id": session_id}
        if not self.reply_enabled:
            return {
                "ok": False,
                "reason": "reply_disabled",
                "session_id": session_id,
                "image_path": path_text,
            }

        image_file = Path(path_text).expanduser()
        try:
            image_file = image_file.resolve()
        except Exception:
            image_file = image_file.absolute()
        if not image_file.exists() or not image_file.is_file():
            return {
                "ok": False,
                "reason": "image_file_missing",
                "session_id": session_id,
                "image_path": str(image_file),
            }
        if not self._file_allowed(image_file):
            return self._file_blocked_result(session_id, image_file, "image")

        caption = str(kwargs.get("caption") or "").strip()
        message = [
            {
                "type": "image",
                "data": {
                    "file": image_file.as_uri(),
                },
            }
        ]
        if caption:
            message.append(
                {
                    "type": "text",
                    "data": {
                        "text": caption,
                    },
                }
            )

        action_info = self._build_send_action(session_id, message)
        if not action_info.get("ok"):
            action_info.setdefault("image_path", str(image_file))
            action_info.setdefault("caption", caption)
            return action_info
        result = await self._send_action(
            session_id, action_info["action"], action_info["payload"], **kwargs
        )
        if isinstance(result, dict) and not result.get("ok"):
            data_uri = self._image_file_to_data_uri(image_file)
            if data_uri:
                fallback_message = [
                    {
                        "type": "image",
                        "data": {
                            "file": data_uri,
                        },
                    }
                ]
                if caption:
                    fallback_message.append(
                        {
                            "type": "text",
                            "data": {
                                "text": caption,
                            },
                        }
                    )
                fallback_action = self._build_send_action(session_id, fallback_message)
                if fallback_action.get("ok"):
                    fallback_result = await self._send_action(
                        session_id,
                        fallback_action["action"],
                        fallback_action["payload"],
                        **kwargs,
                    )
                    if isinstance(fallback_result, dict):
                        fallback_result.setdefault("image_path", str(image_file))
                        fallback_result.setdefault("caption", caption)
                        fallback_result.setdefault("image_transport", "base64")
                    if isinstance(fallback_result, dict) and fallback_result.get("ok"):
                        return fallback_result
        if isinstance(result, dict):
            result.setdefault("image_path", str(image_file))
            result.setdefault("caption", caption)
        return result

    def _image_file_to_data_uri(self, image_file: Path) -> str:
        try:
            raw = image_file.read_bytes()
        except Exception:
            return ""
        if not raw:
            return ""
        mime = mimetypes.guess_type(str(image_file))[0] or "image/jpeg"
        encoded = base64.b64encode(raw).decode("ascii")
        return f"data:{mime};base64,{encoded}"

    async def send_file(self, session_id: str, file_path: str, **kwargs: Any) -> Any:
        path_text = str(file_path or "").strip()
        if not path_text:
            return {"ok": False, "reason": "empty_file_path", "session_id": session_id}
        if not self.reply_enabled:
            return {
                "ok": False,
                "reason": "reply_disabled",
                "session_id": session_id,
                "file_path": path_text,
            }

        file_item = Path(path_text).expanduser()
        try:
            file_item = file_item.resolve()
        except Exception:
            file_item = file_item.absolute()
        if not file_item.exists() or not file_item.is_file():
            return {
                "ok": False,
                "reason": "file_missing",
                "session_id": session_id,
                "file_path": str(file_item),
            }
        if not self._file_allowed(file_item):
            return self._file_blocked_result(session_id, file_item, "file")

        file_name = str(kwargs.get("name") or "").strip() or file_item.name
        message = [
            {
                "type": "file",
                "data": {
                    "file": file_item.as_uri(),
                    "name": file_name,
                },
            }
        ]
        action_info = self._build_send_action(session_id, message)
        if not action_info.get("ok"):
            action_info.setdefault("file_path", str(file_item))
            action_info.setdefault("file_name", file_name)
            return action_info
        result = await self._send_action(
            session_id, action_info["action"], action_info["payload"], **kwargs
        )
        if isinstance(result, dict):
            result.setdefault("file_path", str(file_item))
            result.setdefault("file_name", file_name)
        return result

    async def send_share(self, session_id: str, url: str, **kwargs: Any) -> Any:
        url_text = str(url or "").strip()
        if not url_text:
            return {"ok": False, "reason": "empty_share_url", "session_id": session_id}
        if not self.reply_enabled:
            return {
                "ok": False,
                "reason": "reply_disabled",
                "session_id": session_id,
                "url": url_text,
            }

        title = str(kwargs.get("title") or "").strip() or url_text
        content = str(kwargs.get("content") or "").strip()
        image = str(kwargs.get("image") or "").strip()

        data = {"url": url_text, "title": title}
        if content:
            data["content"] = content
        if image:
            data["image"] = image

        message = [
            {
                "type": "share",
                "data": data,
            }
        ]
        action_info = self._build_send_action(session_id, message)
        if not action_info.get("ok"):
            action_info.setdefault("url", url_text)
            action_info.setdefault("title", title)
            return action_info
        result = await self._send_action(
            session_id, action_info["action"], action_info["payload"], **kwargs
        )
        if isinstance(result, dict):
            result.setdefault("url", url_text)
            result.setdefault("title", title)
        return result
