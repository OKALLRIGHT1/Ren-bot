"""QQ input enrichment and private-message buffering."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from services.chat_support.qq_link_enrichment import QqLinkEnrichmentService
from services.chat_support.qq_private_buffer import QqPrivateMessageBuffer


@dataclass(frozen=True)
class QqInputConfig:
    enable_forward_context: bool = True
    enable_recall: bool = True
    enable_typing: bool = True


class QqInputService:
    """Owns mutable QQ input state and enriches a turn before routing."""

    def __init__(
        self,
        *,
        gateway_getter: Callable[[], Any],
        message_buffer: QqPrivateMessageBuffer,
        link_enrichment: QqLinkEnrichmentService,
        config: QqInputConfig,
        logger: Any = None,
    ) -> None:
        self.gateway_getter = gateway_getter
        self.message_buffer = message_buffer
        self.link_enrichment = link_enrichment
        self.config = config
        self.logger = logger

    async def prepare_private_message(
        self, user_text: str, ctx: Optional[Dict[str, Any]]
    ) -> Optional[str]:
        await self.enrich_reply_context(ctx)
        await self.enrich_forward_context(ctx)
        result = await self.message_buffer.wait(user_text, ctx)
        if result is None:
            return None
        if result.bypassed:
            return result.text

        channel_meta = (ctx or {}).get("channel_meta") or {}
        session_id = str(channel_meta.get("session_id") or "").strip()
        count = int((ctx or {}).get("qq_buffered_count") or 0)
        if self.logger:
            self.logger.info(
                f"[QQ-BUFFER][{session_id}] merged count={count} chars={len(result.text)}"
            )

        images = channel_meta.get("images") if isinstance(channel_meta, dict) else []
        enriched_text, enriched_images = await self.link_enrichment.enrich(
            result.text, images if isinstance(images, list) else []
        )
        if isinstance(channel_meta, dict):
            channel_meta["images"] = enriched_images
            channel_meta["has_image"] = bool(enriched_images)
            channel_meta["image_count"] = len(enriched_images)
        return enriched_text

    async def enrich_reply_context(self, ctx: Optional[Dict[str, Any]]) -> None:
        if not isinstance(ctx, dict):
            return
        meta = ctx.get("channel_meta") or {}
        if not isinstance(meta, dict):
            return
        reply = meta.get("reply") or {}
        if not isinstance(reply, dict):
            return
        message_id = str(reply.get("message_id") or "").strip()
        if not message_id:
            return

        gateway = self.gateway_getter()
        if gateway is None or not hasattr(gateway, "fetch_message_by_id"):
            return
        adapter = str(meta.get("adapter") or "napcat_qq").strip() or "napcat_qq"
        session_id = str(meta.get("session_id") or "").strip()
        try:
            result = await gateway.fetch_message_by_id(
                adapter, session_id, message_id, timeout=5
            )
        except Exception as exc:
            if self.logger:
                self.logger.warning(f"QQ quoted message fetch failed: {exc}")
            return
        if not isinstance(result, dict) or not result.get("ok"):
            return
        item = result.get("item") or {}
        if not isinstance(item, dict):
            return

        text = str(item.get("content") or item.get("text") or "").strip()
        item_meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
        if text and not str(reply.get("text") or reply.get("content") or "").strip():
            reply["text"] = text
        sender_name = str(
            item_meta.get("sender_name")
            or item_meta.get("user_id")
            or reply.get("user_id")
            or ""
        ).strip()
        if sender_name:
            reply["sender_name"] = sender_name

        quoted_images = item.get("images")
        if not isinstance(quoted_images, list):
            quoted_images = item_meta.get("images")
        quoted_images = [
            dict(image) for image in (quoted_images or []) if isinstance(image, dict)
        ]
        if quoted_images:
            reply["images"] = quoted_images
            meta["images"] = self._merge_images(meta.get("images"), quoted_images)
            meta["has_image"] = bool(meta["images"])
            meta["image_count"] = len(meta["images"])

    async def enrich_forward_context(self, ctx: Optional[Dict[str, Any]]) -> None:
        if not isinstance(ctx, dict) or not self.config.enable_forward_context:
            return
        meta = ctx.get("channel_meta") or {}
        if not isinstance(meta, dict):
            return
        components = meta.get("components") if isinstance(meta.get("components"), list) else []
        forward_ids = []
        for component in components:
            if not isinstance(component, dict):
                continue
            if str(component.get("type") or "").strip().lower() != "forward":
                continue
            data = component.get("data") if isinstance(component.get("data"), dict) else {}
            forward_id = str(
                data.get("id") or data.get("message_id") or data.get("res_id") or ""
            ).strip()
            if forward_id:
                forward_ids.append(forward_id)
        if not forward_ids:
            return

        gateway = self.gateway_getter()
        if gateway is None or not hasattr(gateway, "fetch_forward_message"):
            return
        adapter = str(meta.get("adapter") or "napcat_qq").strip() or "napcat_qq"
        session_id = str(meta.get("session_id") or "").strip()
        forward_contexts = []
        merged_images = list(meta.get("images") or [])
        for forward_id in forward_ids[:2]:
            try:
                result = await gateway.fetch_forward_message(
                    adapter, session_id, forward_id, timeout=8
                )
            except Exception as exc:
                if self.logger:
                    self.logger.warning(f"QQ forward fetch failed: {exc}")
                continue
            if not isinstance(result, dict) or not result.get("ok"):
                continue
            items = result.get("items") if isinstance(result.get("items"), list) else []
            lines = []
            for item in items[:30]:
                if not isinstance(item, dict):
                    continue
                sender = str(item.get("sender_name") or "unknown").strip()
                text = str(item.get("text") or "").strip()
                if text:
                    lines.append(f"{sender}: {text}")
                merged_images.extend(
                    image
                    for image in (item.get("images") or [])
                    if isinstance(image, dict)
                )
            if lines:
                forward_contexts.append(
                    "<forward_content>\n" + "\n".join(lines) + "\n</forward_content>"
                )
        if forward_contexts:
            meta["forward_contexts"] = forward_contexts
            meta["images"] = merged_images
            meta["has_image"] = bool(merged_images)
            meta["image_count"] = len(merged_images)

    async def handle_notice(self, event: Any) -> None:
        event_type, session_id, metadata = self._notice_parts(event)
        if not session_id:
            return
        if event_type == "qq_private_recall" and self.config.enable_recall:
            message_id = str(metadata.get("message_id") or "").strip()
            removed = await self.message_buffer.handle_recall(session_id, message_id)
            if removed and self.logger:
                self.logger.info(
                    f"[QQ-BUFFER][{session_id}] recall removed message_id={message_id}"
                )
            return
        if event_type == "qq_private_typing" and self.config.enable_typing:
            await self.message_buffer.handle_typing(
                session_id, is_typing=bool(metadata.get("is_typing"))
            )

    @staticmethod
    def _notice_parts(event: Any) -> tuple[str, str, Dict[str, Any]]:
        if isinstance(event, dict):
            raw_meta = event.get("metadata") or {}
            return (
                str(event.get("event_type") or "").strip(),
                str(event.get("session_id") or "").strip(),
                raw_meta if isinstance(raw_meta, dict) else {},
            )
        raw_meta = getattr(event, "metadata", {}) or {}
        return (
            str(getattr(event, "event_type", "") or "").strip(),
            str(getattr(event, "session_id", "") or "").strip(),
            raw_meta if isinstance(raw_meta, dict) else {},
        )

    @staticmethod
    def _merge_images(current: Any, incoming: list[Dict[str, Any]]) -> list[Dict[str, Any]]:
        merged = [dict(image) for image in (current or []) if isinstance(image, dict)]
        seen = {QqInputService._image_key(image) for image in merged}
        for image in incoming:
            key = QqInputService._image_key(image)
            if key in seen:
                continue
            seen.add(key)
            merged.append(dict(image))
        return merged

    @staticmethod
    def _image_key(image: Dict[str, Any]) -> str:
        return str(
            image.get("url")
            or image.get("file")
            or image.get("name")
            or image
        )
