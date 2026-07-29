from __future__ import annotations

import asyncio
import json
import random
import re
import sys
import time
from pathlib import Path
from typing import Any, Optional

from modules.model_catalog import normalize_model_selection
from modules.plugin_model_gateway import get_plugin_model_gateway

PLUGIN_DIR = Path(__file__).resolve().parent
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

from meme_store import MemeStore, _split_tags


QQ_REMOTE_SOURCES = {"qq_gateway", "napcat_qq"}
TEASE_WORDS = ("狡猾", "坏", "嘴硬", "笨", "可爱", "怜酱", "偷笑", "好耶")
AFFECTION_WORDS = ("喜欢", "想你", "抱", "亲", "老婆", "陪我", "贴贴")
COMFORT_WORDS = ("难受", "烦", "累", "崩", "害怕", "不想", "委屈")
AWKWARD_WORDS = ("啊", "呃", "草", "绷", "尴尬", "沉默", "坏了")


class Plugin:
    name = "表情包库"
    description = "数据库版表情包系统"
    type = "direct"
    aliases = ["/表情包", "/发表情", "/随机表情", "/导入表情包", "/表情包统计"]
    timeout_sec = 20

    def __init__(self):
        self.settings: dict[str, Any] = {}
        self._store: Optional[MemeStore] = None
        self._last_auto_sent: dict[str, float] = {}

    def reload_config(self):
        self._store = None

    def _setting(self, key: str, default: Any) -> Any:
        value = self.settings.get(key, default) if isinstance(self.settings, dict) else default
        if isinstance(value, dict):
            return value.get("default", default)
        return value

    def _int_setting(self, key: str, default: int, minimum: int, maximum: int) -> int:
        try:
            value = int(self._setting(key, default) or default)
        except Exception:
            value = default
        return max(minimum, min(maximum, value))

    def _path_setting(self, key: str, default: str) -> Path:
        path = Path(str(self._setting(key, default) or default))
        return path if path.is_absolute() else Path.cwd() / path

    def _store_obj(self) -> MemeStore:
        if self._store is None:
            self._store = MemeStore(
                self._path_setting("database_path", "plugins/meme_pack/data/memes.sqlite"),
                self._path_setting("assets_dir", "plugins/meme_pack/assets"),
            )
        return self._store

    def should_handle_direct(self, text: str, context: dict, key: str) -> bool:
        clean = str(text or "").strip()
        return clean.startswith(tuple(self.aliases))

    def _is_qq_context(self, ctx: dict) -> bool:
        return str((ctx or {}).get("source") or "").strip().lower() in QQ_REMOTE_SOURCES

    def _session_id(self, ctx: dict) -> str:
        meta = (ctx or {}).get("channel_meta") or {}
        return str(meta.get("session_id") or "").strip()

    def _message_type(self, ctx: dict) -> str:
        meta = (ctx or {}).get("channel_meta") or {}
        return str(meta.get("message_type") or "private").strip().lower()

    def _strip_command(self, text: str) -> str:
        clean = str(text or "").strip()
        for alias in sorted(self.aliases, key=len, reverse=True):
            if clean.startswith(alias):
                return clean[len(alias) :].strip()
        return clean

    def _parse_import_args(self, args: str) -> tuple[str, list[str]]:
        rest = self._strip_command(args)
        tags: list[str] = []
        if " 标签=" in rest:
            rest, tag_text = rest.split(" 标签=", 1)
            tags = _split_tags(tag_text)
        elif " tags=" in rest:
            rest, tag_text = rest.split(" tags=", 1)
            tags = _split_tags(tag_text)
        return rest.strip().strip('"'), tags

    def _infer_emotion(self, user_text: str, reply_text: str, emotion: str = "") -> str:
        if emotion and emotion != "neutral":
            return str(emotion).strip()
        text = f"{user_text}\n{reply_text}"
        if any(w in text for w in TEASE_WORDS):
            return "调侃"
        if any(w in text for w in AFFECTION_WORDS):
            return "亲近"
        if any(w in text for w in COMFORT_WORDS):
            return "安慰"
        if any(w in text for w in AWKWARD_WORDS):
            return "尴尬"
        if "？" in text or "?" in text:
            return "疑问"
        return "日常"

    def _extract_json_object(self, text: str) -> dict[str, Any]:
        raw = str(text or "").strip()
        if not raw:
            return {}
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
            raw = re.sub(r"\s*```$", "", raw)
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except Exception:
            pass
        match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
        if not match:
            return {}
        try:
            data = json.loads(match.group(0))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    async def _select_auto_meme_with_llm(
        self,
        *,
        candidates: list[tuple[float, Any]],
        user_text: str,
        reply_text: str,
        inferred_emotion: str,
        ctx: Optional[dict] = None,
    ) -> tuple[Optional[Any], str]:
        if not candidates:
            return None, ""
        candidate_lines: list[str] = []
        id_map: dict[int, Any] = {}
        for score, asset in candidates:
            id_map[int(asset.id)] = asset
            tags = ", ".join(asset.tags) if asset.tags else ""
            desc = asset.description.strip() or "无描述"
            candidate_lines.append(
                "\n".join(
                    [
                        f"- id: {asset.id}",
                        f"  emotion: {asset.emotion or '未标注'}",
                        f"  tags: {tags or '无'}",
                        f"  description: {desc[:180]}",
                        f"  usage_count: {asset.usage_count}",
                        f"  local_score: {score:.2f}",
                    ]
                )
            )

        system_prompt = (
            "你是 QQ 私聊里的表情包选择器。你只判断这轮回复是否适合补发一张表情包，"
            "以及候选里哪张最贴合当前语气。\n"
            "规则：\n"
            "1) 表情包要像真人顺手补一张，不要每轮都发；\n"
            "2) 只有调侃、疑惑、亲近、尴尬、安慰等明显氛围时才 send=true；\n"
            "3) 如果文字回复已经足够完整、话题严肃、解释性强、或候选描述不贴合，就 send=false；\n"
            "4) 选择时重点看候选的 description、tags、emotion，可以按描述大意匹配，不要求关键词完全一致；\n"
            "5) 你看不到图片本身，描述为空或描述不足时要保守，不要脑补图片内容；\n"
            "6) 只输出 JSON，不要解释。"
        )
        user_prompt = (
            "【当前对话】\n"
            f"用户：{str(user_text or '').strip()[:500]}\n"
            f"怜酱回复：{str(reply_text or '').strip()[:500]}\n"
            f"初步情绪：{inferred_emotion}\n\n"
            "【候选表情包】\n"
            + "\n".join(candidate_lines)
            + "\n\n"
            '请输出：{"send":true,"meme_id":123,"emotion":"疑问","reason":"简短理由"} '
            '或 {"send":false,"meme_id":null,"emotion":"","reason":"简短理由"}'
        )
        print(
            f"[MemePack] LLM selector start: emotion={inferred_emotion}, "
            f"candidates={len(candidates)}"
        )
        gateway = (ctx or {}).get("model_gateway") or get_plugin_model_gateway()
        result = await gateway.invoke_text(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            selected_ids=normalize_model_selection(
                self._setting("model_queue", [])
            ),
            required_purpose="chat",
            task_type="default",
            caller="meme_pack_selector",
            timeout_sec=18,
        )
        if not result.ok:
            print(f"[MemePack] LLM selector failed: {result.error_message}")
            return None, result.error_code
        response = result.text

        data = self._extract_json_object(response)
        if not data:
            print("[MemePack] LLM selector returned invalid JSON")
            return None, "llm_invalid_json"
        if not bool(data.get("send")):
            print(f"[MemePack] LLM selector skip: {data.get('reason') or 'llm_skip'}")
            return None, str(data.get("reason") or "llm_skip")
        try:
            meme_id = int(data.get("meme_id"))
        except Exception:
            meme_id = 0
        asset = id_map.get(meme_id)
        if asset is None:
            print(f"[MemePack] LLM selector invalid meme_id: {meme_id}")
            return None, str(data.get("reason") or "llm_invalid_id")
        reason = str(data.get("reason") or "").strip() or "llm_selected"
        selected_emotion = str(data.get("emotion") or "").strip()
        print(f"[MemePack] LLM selector picked: id={asset.id}, reason={reason[:80]}")
        return asset, f"llm:{selected_emotion or inferred_emotion}:{reason}"

    async def run(self, args: str, context: dict):
        text = str(args or "").strip()
        store = self._store_obj()

        if text.startswith("/表情包统计"):
            stats = store.stats()
            return (
                f"表情包库：共 {stats['total']} 张，可用 {stats['enabled']} 张，"
                f"禁用 {stats['banned']} 张，累计使用 {stats['usage_count']} 次"
            )

        if text.startswith("/导入表情包"):
            path_text, tags = self._parse_import_args(text)
            if not path_text:
                return "用法：/导入表情包 D:\\memes 标签=调侃,可爱"
            path = Path(path_text).expanduser()
            if path.is_dir():
                stats = store.import_directory(path, tags=tags)
                return f"导入完成：新增 {stats['imported']}，跳过 {stats['skipped']}，失败 {stats['failed']}"
            ok, message = store.import_file(path, tags=tags)
            return f"导入{'成功' if ok else '失败'}：{message}"

        query = self._strip_command(text)
        emotion = "" if text.startswith("/随机表情") else query
        asset = store.pick(query=query, emotion=emotion, limit=int(self._setting("max_candidates", 8) or 8))
        if asset is None:
            return "表情包库还是空的，先用 /导入表情包 导入一点图"

        store.mark_used(
            asset,
            session_id=self._session_id(context),
            trigger_text=text,
            reason="manual",
            event_type="manual_sent",
        )
        if self._is_qq_context(context):
            return {
                "__type__": "gateway_image",
                "image_path": asset.file_path,
                "success_text": "",
                "cleanup": False,
            }
        return f"选中表情包：{asset.file_path}\n标签：{', '.join(asset.tags) or asset.emotion or '无'}"

    async def maybe_send_auto_meme(
        self,
        *,
        chat_service: Any,
        user_text: str,
        reply_text: str,
        emotion: str = "neutral",
        ctx: Optional[dict] = None,
    ) -> bool:
        selection = await self._select_auto_meme_asset(
            user_text=user_text,
            reply_text=reply_text,
            emotion=emotion,
            ctx=ctx,
            require_qq_context=True,
            enforce_auto_filters=True,
            enforce_cooldown=True,
        )
        asset = selection.get("asset")
        if asset is None:
            return False

        ctx = selection["ctx"]
        session_id = selection["session_id"]
        now = selection["now"]
        reason = selection["reason"]
        ok = await chat_service._send_gateway_image_reply(asset.file_path, ctx, caption="")
        if ok:
            self._last_auto_sent[session_id] = now
            self._store_obj().mark_used(
                asset,
                session_id=session_id,
                trigger_text=user_text,
                reply_text=reply_text,
                reason=reason,
                event_type="auto_sent",
            )
        return bool(ok)

    async def select_meme_image_path(
        self,
        *,
        user_text: str,
        reply_text: str,
        emotion: str = "neutral",
        ctx: Optional[dict] = None,
        mark_used: bool = False,
        force_pick: bool = False,
    ) -> dict[str, Any]:
        selection = await self._select_auto_meme_asset(
            user_text=user_text,
            reply_text=reply_text,
            emotion=emotion,
            ctx=ctx,
            require_qq_context=False,
            enforce_auto_filters=False,
            enforce_cooldown=False,
            force_pick=force_pick,
        )
        asset = selection.get("asset")
        if asset is None:
            return {}
        if mark_used:
            self._store_obj().mark_used(
                asset,
                session_id=selection["session_id"],
                trigger_text=user_text,
                reply_text=reply_text,
                reason=selection["reason"],
                event_type="select_only",
            )
        return {
            "image_path": str(asset.file_path or ""),
            "reason": str(selection["reason"] or ""),
            "emotion": str(selection["inferred_emotion"] or ""),
            "asset_id": int(getattr(asset, "id", 0) or 0),
        }

    async def select_qq_meme_image_path(
        self,
        *,
        user_text: str,
        reply_text: str,
        emotion: str = "neutral",
        ctx: Optional[dict] = None,
        mark_used: bool = False,
        force_pick: bool = True,
    ) -> dict[str, Any]:
        return await self.select_meme_image_path(
            user_text=user_text,
            reply_text=reply_text,
            emotion=emotion,
            ctx=ctx,
            mark_used=mark_used,
            force_pick=force_pick,
        )

    async def _select_auto_meme_asset(
        self,
        *,
        user_text: str,
        reply_text: str,
        emotion: str = "neutral",
        ctx: Optional[dict] = None,
        require_qq_context: bool,
        enforce_auto_filters: bool,
        enforce_cooldown: bool,
        force_pick: bool = False,
    ) -> dict[str, Any]:
        ctx = ctx if isinstance(ctx, dict) else {}
        if not bool(self._setting("auto_enabled", True)):
            return {}
        if require_qq_context and not self._is_qq_context(ctx):
            return {}
        if (
            enforce_auto_filters
            and bool(self._setting("auto_private_only", True))
            and self._message_type(ctx) != "private"
        ):
            return {}
        if enforce_auto_filters:
            if not reply_text or len(str(reply_text)) > 120:
                return {}
            if re.search(r"https?://|```|\[CMD:", str(reply_text)):
                return {}

        session_id = self._session_id(ctx) or "unknown"
        cooldown = float(self._setting("session_cooldown_seconds", 120) or 120)
        now = time.time()
        if (
            enforce_cooldown
            and now - float(self._last_auto_sent.get(session_id, 0.0)) < cooldown
        ):
            return {}

        inferred_emotion = self._infer_emotion(user_text, reply_text, emotion)

        store = self._store_obj()
        query = f"{user_text} {reply_text} {inferred_emotion}"
        max_candidates = self._int_setting("max_candidates", 8, 1, 64)
        llm_selector_enabled = bool(self._setting("llm_selector_enabled", True))
        if force_pick:
            llm_selector_enabled = False
        # Give the selector a wider pool so description-based matches can survive local recall.
        recall_limit = max_candidates if not llm_selector_enabled else max(max_candidates, 24)
        candidates = store.rank_assets(
            query=query, emotion=inferred_emotion, limit=recall_limit
        )
        reason = f"auto:{inferred_emotion}"
        asset = None
        if llm_selector_enabled:
            asset, reason = await self._select_auto_meme_with_llm(
                candidates=candidates,
                user_text=user_text,
                reply_text=reply_text,
                inferred_emotion=inferred_emotion,
                ctx=ctx,
            )
        elif force_pick:
            asset = candidates[0][1] if candidates else None
            reason = f"force_pick:{inferred_emotion}"
        else:
            probability = max(
                0.0, min(1.0, float(self._setting("auto_probability", 0.12) or 0.0))
            )
            if inferred_emotion in {"调侃", "亲近", "安慰", "尴尬", "疑问"}:
                probability = min(1.0, probability * 1.6)
            if random.random() <= probability:
                asset = store.pick(
                    query=query,
                    emotion=inferred_emotion,
                    limit=max_candidates,
                )
        if asset is None:
            return {}
        return {
            "asset": asset,
            "reason": reason,
            "ctx": ctx,
            "session_id": session_id,
            "now": now,
            "inferred_emotion": inferred_emotion,
        }
