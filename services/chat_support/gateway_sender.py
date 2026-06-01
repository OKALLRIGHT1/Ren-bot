"""Outbound gateway delivery helpers used by ChatService."""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import tempfile
import uuid
from typing import Any, Awaitable, Callable, Dict, List, Optional

from services.chat_support import text_utils


QQ_REMOTE_SOURCES = {"qq_gateway", "napcat_qq"}


def qq_session_label(session_id: str) -> str:
    text = str(session_id or "").strip().lower()
    if text.startswith("group:"):
        return "QQ-GROUP"
    if text.startswith("private:"):
        return "QQ-PRIVATE"
    return "QQ"


def resolve_gateway_target(
    ctx: Optional[Dict[str, Any]],
    *,
    chat_gateway: Any,
    warning_label: str,
    logger: Any = None,
    session_label_fn: Optional[Callable[[str], str]] = None,
    remote_sources: Optional[set[str]] = None,
) -> Optional[Dict[str, Any]]:
    if not chat_gateway or not isinstance(ctx, dict):
        return None
    source = str(ctx.get("source") or "").strip().lower()
    if source not in (remote_sources or QQ_REMOTE_SOURCES):
        return None
    channel_meta = ctx.get("channel_meta") or {}
    adapter_name = (
        str(channel_meta.get("adapter") or "napcat_qq").strip() or "napcat_qq"
    )
    session_id = str(channel_meta.get("session_id") or "").strip()
    if not session_id:
        if logger is not None:
            logger.warning(f"Gateway {warning_label} skipped: missing session_id")
        return None
    sender_name = str(
        channel_meta.get("sender_name") or channel_meta.get("user_id") or ""
    ).strip()
    label_fn = session_label_fn or qq_session_label
    return {
        "adapter_name": adapter_name,
        "channel_meta": channel_meta,
        "sender_name": sender_name,
        "session_id": session_id,
        "session_label": label_fn(session_id),
        "source": source,
    }


def split_gateway_text_parts(text: str) -> List[str]:
    clean = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not clean:
        return []
    clean = re.sub(r"\n{3,}", "\n\n", clean)

    def _is_natural_line(line: str) -> bool:
        item = str(line or "").strip()
        if not item:
            return False
        if len(item) > 90:
            return False
        if re.search(r"https?://|\[[^\]]+\]\([^)]+\)", item):
            return False
        if re.match(r"^\s*(?:[-*•>|#]|\d+[.)、])", item):
            return False
        return True

    blocks = [block.strip() for block in re.split(r"\n\s*\n", clean) if block.strip()]
    parts: List[str] = []
    for block in blocks:
        lines = [line.strip() for line in block.split("\n") if line.strip()]
        if 1 < len(lines) <= 5 and all(_is_natural_line(line) for line in lines):
            parts.extend(lines)
        else:
            parts.append(" ".join(lines))

    parts = [re.sub(r"[ \t]{2,}", " ", part).strip() for part in parts if part.strip()]

    final_parts: List[str] = []
    for part in parts:
        final_parts.extend(_split_long_gateway_part(part))
    final_parts = [part for part in final_parts if part]
    if len(final_parts) > 8:
        head = final_parts[:7]
        tail = " ".join(final_parts[7:]).strip()
        return head + ([tail] if tail else [])
    return final_parts


def _split_long_gateway_part(text: str, max_len: int = 55) -> List[str]:
    raw = str(text or "").strip()
    if not raw or len(raw) <= max_len:
        return [raw] if raw else []
    if re.search(r"https?://|```", raw):
        return [raw]

    chunks = [item.strip() for item in re.split(r"(?<=[。！？!?；;])\s*", raw) if item.strip()]
    if len(chunks) <= 1:
        chunks = [item.strip() for item in re.split(r"(?<=[，,、])\s*", raw) if item.strip()]
    if len(chunks) <= 1:
        return [raw]

    parts: List[str] = []
    current = ""
    for chunk in chunks:
        candidate = f"{current}{chunk}" if current else chunk
        if current and len(candidate) > max_len:
            parts.append(current.strip())
            current = chunk
        else:
            current = candidate
    if current.strip():
        parts.append(current.strip())
    return parts or [raw]


class GatewaySender:
    """Owns outbound QQ gateway delivery while ChatService keeps orchestration."""

    def __init__(
        self,
        *,
        chat_gateway_getter: Callable[[], Any],
        logger: Any,
        prepare_reply_for_output: Callable[..., str],
        strip_emo_tags: Callable[[str], str],
        strip_cmd: Callable[[str], str],
        clean_text_for_tts: Callable[[str], str],
        session_label_fn: Callable[[str], str] = qq_session_label,
        voice_enabled_getter: Callable[[], bool] = lambda: False,
        voice_probability_getter: Callable[[], int] = lambda: 0,
        voice_renderer_getter: Callable[
            [], Optional[Callable[..., Awaitable[Optional[str]]]]
        ] = lambda: None,
        remote_sources: Optional[set[str]] = None,
    ):
        self._chat_gateway_getter = chat_gateway_getter
        self.logger = logger
        self.prepare_reply_for_output = prepare_reply_for_output
        self.strip_emo_tags = strip_emo_tags
        self.strip_cmd = strip_cmd
        self.clean_text_for_tts = clean_text_for_tts
        self.session_label_fn = session_label_fn
        self.voice_enabled_getter = voice_enabled_getter
        self.voice_probability_getter = voice_probability_getter
        self.voice_renderer_getter = voice_renderer_getter
        self.remote_sources = remote_sources or QQ_REMOTE_SOURCES

    @property
    def chat_gateway(self) -> Any:
        return self._chat_gateway_getter()

    async def cleanup_voice_file(self, path: str, delay_sec: float = 45.0):
        file_path = str(path or "").strip()
        if not file_path:
            return
        try:
            await asyncio.sleep(max(0.0, float(delay_sec)))
        except Exception:
            pass
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except Exception:
            pass

    async def cleanup_image_file(self, path: str, delay_sec: float = 45.0):
        file_path = str(path or "").strip()
        if not file_path:
            return
        if not self.is_managed_temp_file(file_path):
            return
        try:
            await asyncio.sleep(max(0.0, float(delay_sec)))
        except Exception:
            pass
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except Exception:
            pass

    def is_managed_temp_file(self, path: str) -> bool:
        file_path = str(path or "").strip()
        if not file_path:
            return False
        try:
            resolved = os.path.realpath(file_path)
        except Exception:
            resolved = os.path.abspath(file_path)
        try:
            temp_root = os.path.realpath(tempfile.gettempdir())
        except Exception:
            temp_root = tempfile.gettempdir()
        return resolved.startswith(os.path.join(temp_root, ""))

    def prepare_image_transport_path(self, path: str) -> tuple[str, str]:
        file_path = str(path or "").strip()
        if not file_path:
            return "", ""
        if not os.path.isfile(file_path):
            return file_path, ""
        try:
            file_path.encode("ascii")
            return file_path, ""
        except UnicodeEncodeError:
            pass
        temp_dir = os.path.join(tempfile.gettempdir(), "live2d_llm_gateway_media")
        os.makedirs(temp_dir, exist_ok=True)
        suffix = os.path.splitext(file_path)[1] or ".jpg"
        staged_path = os.path.join(temp_dir, f"gateway_img_{uuid.uuid4().hex}{suffix}")
        shutil.copyfile(file_path, staged_path)
        return staged_path, staged_path

    def resolve_target(
        self, ctx: Optional[Dict[str, Any]], *, warning_label: str
    ) -> Optional[Dict[str, Any]]:
        return resolve_gateway_target(
            ctx,
            chat_gateway=self.chat_gateway,
            warning_label=warning_label,
            logger=self.logger,
            session_label_fn=self.session_label_fn,
            remote_sources=self.remote_sources,
        )

    def is_action_success(self, result: Any) -> bool:
        if not isinstance(result, dict):
            return False
        if bool(result.get("ok")):
            return True

        response = result.get("response")
        if isinstance(response, dict):
            status = str(response.get("status") or "").strip().lower()
            if status in {"ok", "success"}:
                return True
            try:
                retcode = int(response.get("retcode", 0) or 0)
            except Exception:
                retcode = None
            if retcode == 0:
                return True
            data = response.get("data")
            if data not in (None, "", [], {}):
                return True
            if response.get("message_id") not in (None, "", 0, "0"):
                return True

        if str(result.get("transport") or "").strip().lower() == "http":
            status_code = result.get("status")
            try:
                status_num = int(status_code)
            except Exception:
                status_num = None
            if status_num is not None and 200 <= status_num < 300:
                return True
        return False

    async def send_text_parts(
        self,
        adapter_name: str,
        session_id: str,
        text: str,
        *,
        metadata: Optional[Dict[str, Any]] = None,
        source: str = "",
        session_label: str = "",
        log_suffix: str = "text_sent",
    ) -> bool:
        parts = split_gateway_text_parts(text)
        if not parts:
            return True
        ok = True
        for idx, part in enumerate(parts):
            try:
                result = await self.chat_gateway.send_text(
                    adapter_name,
                    session_id,
                    part,
                    metadata=metadata,
                    source=source,
                )
                if isinstance(result, dict) and not result.get("ok"):
                    ok = False
                    self.logger.warning(f"Gateway text reply failed: {result}")
                else:
                    suffix = log_suffix
                    if len(parts) > 1:
                        suffix = f"{log_suffix}_{idx + 1}/{len(parts)}"
                    self.logger.info(
                        f"[QQ-OUT-OK][{session_label}][{session_id}] {suffix}"
                    )
            except Exception as e:
                ok = False
                self.logger.error(f"Gateway reply failed: {e}")
            if idx < len(parts) - 1:
                await asyncio.sleep(0.35)
        return ok

    async def send_file_reply(
        self, file_path: str, ctx: Optional[Dict[str, Any]] = None, file_name: str = ""
    ) -> bool:
        path_text = str(file_path or "").strip()
        if not path_text:
            return False
        target = self.resolve_target(ctx, warning_label="file reply")
        if target is None:
            return False
        source = target["source"]
        channel_meta = target["channel_meta"]
        adapter_name = target["adapter_name"]
        session_id = target["session_id"]
        sender_name = target["sender_name"]
        session_label = target["session_label"]
        self.logger.info(
            f"[QQ-OUT-FILE][{session_label}][{session_id}][to={sender_name or 'unknown'}] file={file_name or path_text}"
        )
        try:
            result = await self.chat_gateway.send_file(
                adapter_name,
                session_id,
                path_text,
                name=file_name,
                metadata=channel_meta,
                source=source,
            )
            if self.is_action_success(result):
                self.logger.info(
                    f"[QQ-OUT-FILE-OK][{session_label}][{session_id}] file_sent"
                )
                return True
            self.logger.warning(f"Gateway file reply failed: {result}")
            return False
        except Exception as e:
            self.logger.error(f"Gateway file reply failed: {e}")
            return False

    async def send_voice_reply(
        self, voice_path: str, ctx: Optional[Dict[str, Any]] = None
    ) -> bool:
        path_text = str(voice_path or "").strip()
        if not path_text:
            return False
        target = self.resolve_target(ctx, warning_label="voice reply")
        if target is None:
            return False
        source = target["source"]
        channel_meta = target["channel_meta"]
        adapter_name = target["adapter_name"]
        session_id = target["session_id"]
        sender_name = target["sender_name"]
        session_label = target["session_label"]
        self.logger.info(
            f"[QQ-OUT-VOICE][{session_label}][{session_id}][to={sender_name or 'unknown'}] voice={path_text}"
        )
        try:
            result = await self.chat_gateway.send_voice(
                adapter_name,
                session_id,
                path_text,
                metadata=channel_meta,
                source=source,
            )
            if self.is_action_success(result):
                self.logger.info(
                    f"[QQ-OUT-VOICE-OK][{session_label}][{session_id}] voice_sent"
                )
                return True
            self.logger.warning(f"Gateway voice reply failed: {result}")
            return False
        except Exception as e:
            self.logger.error(f"Gateway voice reply failed: {e}")
            return False

    async def send_image_reply(
        self, image_path: str, ctx: Optional[Dict[str, Any]] = None, caption: str = ""
    ) -> bool:
        path_text = str(image_path or "").strip()
        if not path_text:
            return False
        target = self.resolve_target(ctx, warning_label="image reply")
        if target is None:
            return False
        source = target["source"]
        channel_meta = target["channel_meta"]
        adapter_name = target["adapter_name"]
        session_id = target["session_id"]
        sender_name = target["sender_name"]
        session_label = target["session_label"]
        caption_preview = (
            str(caption or "").replace("\r", " ").replace("\n", " ").strip()
        )
        if len(caption_preview) > 160:
            caption_preview = caption_preview[:160] + "..."
        self.logger.info(
            f"[QQ-OUT-IMAGE][{session_label}][{session_id}][to={sender_name or 'unknown'}] path={path_text} caption={caption_preview}"
        )
        transport_path = path_text
        staged_path = ""
        try:
            transport_path, staged_path = self.prepare_image_transport_path(path_text)
        except Exception as exc:
            self.logger.warning(
                f"Gateway image staging failed, fallback to original path: {exc}"
            )
            transport_path = path_text
            staged_path = ""
        if staged_path:
            self.logger.info(
                f"[QQ-OUT-IMAGE-STAGED][{session_label}][{session_id}] transport_path={transport_path}"
            )
        try:
            result = await self.chat_gateway.send_image(
                adapter_name,
                session_id,
                transport_path,
                caption=caption,
                metadata=channel_meta,
                source=source,
            )
            if self.is_action_success(result):
                self.logger.info(
                    f"[QQ-OUT-IMAGE-OK][{session_label}][{session_id}] image_sent"
                )
                return True
            self.logger.warning(f"Gateway image reply failed: {result}")
            return False
        except Exception as e:
            self.logger.error(f"Gateway image reply failed: {e}")
            return False
        finally:
            if staged_path:
                asyncio.create_task(self.cleanup_image_file(staged_path))

    async def send_reply(
        self,
        text: str,
        ctx: Optional[Dict[str, Any]] = None,
        emotion: Optional[str] = None,
    ):
        text = self.prepare_reply_for_output(text, ctx, scene="chat")
        if not text:
            return
        target = self.resolve_target(ctx, warning_label="reply")
        if target is None:
            return
        source = target["source"]
        channel_meta = target["channel_meta"]
        adapter_name = target["adapter_name"]
        session_id = target["session_id"]
        sender_name = target["sender_name"]
        session_label = target["session_label"]
        preview = text.replace("\r", " ").replace("\n", " ").strip()
        if len(preview) > 240:
            preview = preview[:240] + "..."
        self.logger.info(
            f"[QQ-OUT][{session_label}][{session_id}][to={sender_name or 'unknown'}] {preview}"
        )

        user_text = str((ctx or {}).get("user_text") or "")
        link_request = text_utils.is_link_request(user_text)
        contains_url = bool(text_utils.extract_first_url(text))
        skip_voice = link_request or contains_url
        sent_share = False

        if link_request:
            url = text_utils.extract_first_url(
                text
            ) or text_utils.extract_url_from_tool_results(ctx)
            if url:
                title = text_utils.build_share_title(text, url)
                content = text_utils.build_share_content(text, title)
                try:
                    share_result = await self.chat_gateway.send_share(
                        adapter_name,
                        session_id,
                        url,
                        title=title,
                        content=content,
                        metadata=channel_meta,
                        source=source,
                    )
                    if isinstance(share_result, dict) and share_result.get("ok"):
                        sent_share = True
                except Exception as e:
                    self.logger.warning(f"Gateway share send failed: {e}")

                if sent_share:
                    cleaned = text_utils.strip_urls(text)
                    text = cleaned or "已发送链接卡片。"

        voice_path = ""
        voice_enabled = bool(self.voice_enabled_getter())
        try:
            voice_probability = int(self.voice_probability_getter() or 0)
        except Exception:
            voice_probability = 0
        voice_probability = max(0, min(100, voice_probability))
        voice_renderer = self.voice_renderer_getter()
        should_voice = (
            (not skip_voice)
            and voice_enabled
            and callable(voice_renderer)
            and voice_probability > 0
            and source in self.remote_sources
        )
        if should_voice:
            clean_voice_text = self.clean_text_for_tts(
                self.strip_cmd(self.strip_emo_tags(text))
            )
            if len(clean_voice_text) >= 2:
                import random as _random

                if _random.random() * 100 < voice_probability:
                    try:
                        voice_path = str(
                            await voice_renderer(
                                clean_voice_text,
                                emotion=emotion,
                                source=source,
                                channel_meta=channel_meta,
                            )
                            or ""
                        ).strip()
                    except Exception as e:
                        self.logger.warning(f"Gateway voice render failed: {e}")
                        voice_path = ""
                    if voice_path:
                        try:
                            result = await self.chat_gateway.send_voice(
                                adapter_name,
                                session_id,
                                voice_path,
                                metadata=channel_meta,
                                source=source,
                            )
                            if self.is_action_success(result):
                                await self.send_text_parts(
                                    adapter_name,
                                    session_id,
                                    text,
                                    metadata=channel_meta,
                                    source=source,
                                    session_label=session_label,
                                    log_suffix="voice_sent_with_text",
                                )
                                return
                            self.logger.warning(
                                f"Gateway voice send fallback to text: {result}"
                            )
                        except Exception as e:
                            self.logger.warning(
                                f"Gateway voice send failed, fallback to text: {e}"
                            )
                        finally:
                            asyncio.create_task(
                                self.cleanup_voice_file(voice_path)
                            )
        await self.send_text_parts(
            adapter_name,
            session_id,
            text,
            metadata=channel_meta,
            source=source,
            session_label=session_label,
            log_suffix="text_sent",
        )
