"""Diary and transcript formatting helpers used by ChatService."""

from __future__ import annotations

import re
import json
from datetime import datetime
from typing import Any, Callable, Dict, Iterable, List, Optional


def normalize_diary_text_block(text: Any) -> str:
    normalized = str(text or "").strip()
    if normalized in {"(none)", "(no chat history)", "(no owner chat history)"}:
        return ""
    return normalized


def is_invalid_diary_output(text: str) -> bool:
    content = str(text or "").strip()
    if not content:
        return True
    lowered = content.lower()
    error_markers = (
        "the model does not exist",
        "openai_responses http",
        "error code:",
        "traceback",
        "connection error",
        "invalid api key",
        "not implemented",
        "bad_response_status_code",
        "无法连接 ai",
        "系统繁忙",
    )
    if any(marker in lowered for marker in error_markers):
        return True
    prompt_markers = (
        "[任务]",
        "[输出要求]",
        "[数据源",
        "系统时间:",
        "不要输出标题",
        "必须从“我”的视角",
    )
    return sum(1 for marker in prompt_markers if marker in content) >= 2


def build_diary_failure_text(date_str: str, is_makeup: bool) -> str:
    if is_makeup:
        return f"{date_str} 的补写日记这次没写成，我已经拦住异常返回，没有把坏内容归档。"
    return "今天的日记这次没写成，我已经拦住异常返回，没有把坏内容归档。"


def is_diary_heading_line(line: str, date_str: str) -> bool:
    text = str(line or "").strip()
    if not text:
        return False
    normalized = text.replace("（", "(").replace("）", ")")
    if normalized.startswith(f"【日记 {date_str}】"):
        return True
    if normalized in {
        f"{date_str} 日记",
        f"{date_str}日记",
        f"{date_str} 日记 (补)",
        f"{date_str}日记(补)",
        f"{date_str} 日记(补)",
    }:
        return True
    if re.fullmatch(r"\d{4}年\d{1,2}月\d{1,2}日", normalized):
        return True
    return False


def polish_diary_output(text: str, date_str: str, is_makeup: bool = False) -> str:
    raw = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not raw:
        return ""

    lines = [line.rstrip() for line in raw.split("\n")]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and is_diary_heading_line(lines[0], date_str):
        lines.pop(0)
        while lines and not lines[0].strip():
            lines.pop(0)

    paragraphs: List[str] = []
    bucket: List[str] = []
    for raw_line in lines:
        line = re.sub(r"[ \t]+", " ", raw_line).strip()
        if not line:
            if bucket:
                paragraphs.append("".join(bucket).strip())
                bucket = []
            continue
        bucket.append(line)
    if bucket:
        paragraphs.append("".join(bucket).strip())

    cleaned: List[str] = []
    for paragraph in paragraphs:
        for part in split_diary_paragraph(paragraph):
            if part:
                cleaned.append(part)

    if not cleaned:
        return ""

    if len(cleaned) > 3:
        merged = cleaned[:2]
        merged.append("".join(cleaned[2:]).strip())
        cleaned = merged

    content = "\n\n".join(cleaned).strip()
    if is_makeup and not content.startswith("这是补写"):
        makeup_markers = ("补写", "补记", "补上一笔")
        if not any(marker in cleaned[0] for marker in makeup_markers):
            cleaned[0] = f"这是补写的内容。{cleaned[0]}"
            content = "\n\n".join(cleaned).strip()
    return content


def extract_report_hours(report_text: str) -> float:
    text = str(report_text or "").strip()
    if not text:
        return 0.0
    match = re.search(r"活跃时长:\s*([\d.]+)\s*小时", text)
    if not match:
        return 0.0
    try:
        return float(match.group(1))
    except Exception:
        return 0.0


def is_suspicious_daily_stats(
    date_str: str, stats_payload: Optional[Dict[str, Any]], report_text: str
) -> bool:
    if isinstance(stats_payload, dict):
        payload_date = str(stats_payload.get("date") or "").strip()
        if payload_date and payload_date != date_str:
            return True
        total_hours = stats_payload.get("total_hours")
        try:
            if total_hours is not None and float(total_hours) > 24.0:
                return True
        except Exception:
            pass
    return extract_report_hours(report_text) > 24.0


def build_diary_focus_digest(
    date_str: str,
    raw_stats: Optional[Dict[str, Any]],
    owner_local_history: str,
    owner_qq_private_history: str,
    owner_qq_group_history: str,
) -> str:
    lines = [f"- 日期: {date_str}"]
    stats = raw_stats if isinstance(raw_stats, dict) else {}

    durations = stats.get("durations")
    if isinstance(durations, dict) and durations:
        top_apps = sorted(
            (
                (str(name or "").strip(), float(seconds or 0.0))
                for name, seconds in durations.items()
                if str(name or "").strip()
            ),
            key=lambda item: item[1],
            reverse=True,
        )[:5]
        if top_apps:
            app_text = "、".join(
                f"{name}({format_duration_short(seconds)})"
                for name, seconds in top_apps
            )
            lines.append(f"- 当天主要应用: {app_text}")

    category_totals = stats.get("category_totals")
    if isinstance(category_totals, dict) and category_totals:
        top_categories = sorted(
            (
                (str(name or "").strip(), float(seconds or 0.0))
                for name, seconds in category_totals.items()
                if str(name or "").strip()
            ),
            key=lambda item: item[1],
            reverse=True,
        )[:4]
        if top_categories:
            category_text = "、".join(
                f"{name}({format_duration_short(seconds)})"
                for name, seconds in top_categories
            )
            lines.append(f"- 当天主要活动类别: {category_text}")

    observation_compact = stats.get("observation_compact")
    if isinstance(observation_compact, list) and observation_compact:
        highlights = [
            str(item).strip() for item in observation_compact[:4] if str(item).strip()
        ]
        if highlights:
            lines.append(f"- 屏幕观察摘要: {' | '.join(highlights)}")

    for label, block in (
        ("本地互动", owner_local_history),
        ("QQ私聊互动", owner_qq_private_history),
        ("QQ群互动", owner_qq_group_history),
    ):
        snippet = extract_history_focus_lines(block, limit=4)
        if snippet:
            lines.append(f"- {label}: {' | '.join(snippet)}")

    return "\n".join(lines)


def is_diary_artifact_row(row: Dict[str, Any], target_date: str = "") -> bool:
    if not isinstance(row, dict):
        return False
    meta = row_meta(row)
    meta_type = str(meta.get("type") or "").strip().lower()
    content = str(row.get("content") or "").strip()
    if meta_type == "episodic_memory":
        return True
    if re.match(r"^【日记\s+\d{4}-\d{2}-\d{2}】", content):
        return True
    if target_date and content.startswith(f"【日记 {target_date}】"):
        return True
    return False


def split_diary_paragraph(paragraph: str, max_len: int = 140) -> List[str]:
    text = str(paragraph or "").strip()
    if len(text) <= max_len:
        return [text] if text else []
    sentences = [
        part.strip()
        for part in re.split(r"(?<=[。！？])", text)
        if str(part).strip()
    ]
    if len(sentences) < 2:
        return [text]

    parts: List[str] = []
    current = ""
    remaining = len(sentences)
    for sentence in sentences:
        remaining -= 1
        candidate = f"{current}{sentence}" if current else sentence
        should_flush = current and len(candidate) > max_len and remaining >= 1
        if should_flush:
            parts.append(current.strip())
            current = sentence
            continue
        current = candidate
    if current.strip():
        parts.append(current.strip())
    return parts or [text]


def extract_history_focus_lines(text: str, limit: int = 4) -> List[str]:
    lines: List[str] = []
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line in {"(none)", "(no chat history)"}:
            continue
        lines.append(line)
        if len(lines) >= limit:
            break
    return lines


def format_duration_short(seconds: Any) -> str:
    try:
        total_seconds = max(0, int(float(seconds)))
    except Exception:
        return "0分钟"
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    if hours and minutes:
        return f"{hours}小时{minutes}分钟"
    if hours:
        return f"{hours}小时"
    return f"{max(1, minutes)}分钟"


def row_meta(row: Dict[str, Any]) -> Dict[str, Any]:
    meta = row.get("meta") if isinstance(row, dict) else {}
    return meta if isinstance(meta, dict) else {}


def row_source(row: Dict[str, Any]) -> str:
    return str(row_meta(row).get("source") or "").strip().lower()


def row_message_type(row: Dict[str, Any]) -> str:
    meta = row_meta(row)
    message_type = str(meta.get("message_type") or "").strip().lower()
    if message_type:
        return message_type
    session_id = str(row.get("session_id") or "").strip().lower()
    if session_id.startswith("group:"):
        return "group"
    if session_id.startswith("private:"):
        return "private"
    return ""


def row_sender(row: Dict[str, Any]) -> Dict[str, Any]:
    sender = row_meta(row).get("sender")
    return sender if isinstance(sender, dict) else {}


def is_owner_shared_row(
    row: Dict[str, Any],
    *,
    owner_shared_session_id: str,
    legacy_owner_private_session_ids: Iterable[str],
    owner_shared_local_sources: Iterable[str],
    qq_remote_sources: Iterable[str],
) -> bool:
    if not isinstance(row, dict):
        return False
    session_id = str(row.get("session_id") or "").strip()
    meta = row_meta(row)
    source = str(meta.get("source") or "").strip().lower()
    if session_id == owner_shared_session_id:
        return True
    if session_id and session_id in set(legacy_owner_private_session_ids or []):
        return True
    if source in set(owner_shared_local_sources or []):
        return True
    if source in set(qq_remote_sources or []) and bool(meta.get("is_owner")):
        return True
    return False


def format_day_transcript_line(
    row: Dict[str, Any],
    *,
    owner_shared_session_id: str,
    legacy_owner_private_session_ids: Iterable[str],
    owner_shared_local_sources: Iterable[str],
    qq_remote_sources: Iterable[str],
    assistant_name: str = "当前角色",
    owner_label: str = "主人",
) -> str:
    if not isinstance(row, dict):
        return ""
    content = str(row.get("content") or "").strip()
    if not content:
        return ""
    ts = int(row.get("ts") or 0)
    time_str = datetime.fromtimestamp(ts).strftime("%H:%M")
    role = str(row.get("role") or "").strip().lower()
    session_id = str(row.get("session_id") or "").strip()
    meta = row_meta(row)
    source = str(meta.get("source") or "").strip().lower()
    sender_name = str(meta.get("sender_name") or meta.get("user_id") or "").strip()
    message_type = str(meta.get("message_type") or "private").strip().lower() or "private"

    owner_row = is_owner_shared_row(
        row,
        owner_shared_session_id=owner_shared_session_id,
        legacy_owner_private_session_ids=legacy_owner_private_session_ids,
        owner_shared_local_sources=owner_shared_local_sources,
        qq_remote_sources=qq_remote_sources,
    )
    qq_sources = set(qq_remote_sources or [])
    legacy_sessions = set(legacy_owner_private_session_ids or [])
    assistant_label = str(assistant_name or "当前角色").strip() or "当前角色"
    subject_label = str(owner_label or "主人").strip() or "主人"

    if role == "assistant":
        if owner_row:
            speaker = f"{assistant_label}（对 {subject_label}）"
        elif source in qq_sources:
            speaker = (
                f"{assistant_label}（对 QQ 群）"
                if message_type == "group"
                else f"{assistant_label}（对 QQ 联系人）"
            )
        else:
            speaker = assistant_label
    elif role == "system":
        speaker = "System"
    elif owner_row:
        if source in qq_sources or session_id in legacy_sessions:
            speaker = f"{subject_label}（QQ）"
        else:
            speaker = f"{subject_label}（本地）"
    elif source in qq_sources:
        if message_type == "group":
            speaker = f"OtherGroupMember({sender_name or 'Unknown'})"
        else:
            speaker = f"OtherQQContact({sender_name or 'Unknown'})"
    else:
        speaker = "User"

    return f"[{time_str}] {speaker}: {content}"


def load_day_transcript_rows(
    store: Any,
    date_str: str,
    *,
    on_error: Optional[Callable[[Exception], None]] = None,
) -> list[Dict[str, Any]]:
    if not store:
        return []

    try:
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            dt = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

        start_ts = int(dt.timestamp())
        end_ts = start_ts + 86400

        with store._connect() as conn:
            cursor = conn.execute(
                "SELECT ts, role, content, session_id, meta_json FROM transcript WHERE ts >= ? AND ts < ? ORDER BY ts ASC",
                (start_ts, end_ts),
            )
            rows = cursor.fetchall()

        result: list[Dict[str, Any]] = []
        for row in rows:
            meta: Dict[str, Any] = {}
            raw_meta = row["meta_json"]
            if raw_meta:
                try:
                    meta = json.loads(raw_meta)
                except Exception:
                    meta = {}
            result.append(
                {
                    "ts": int(row["ts"]),
                    "role": str(row["role"] or ""),
                    "content": str(row["content"] or ""),
                    "session_id": str(row["session_id"] or ""),
                    "meta": meta,
                }
            )
        return result
    except Exception as exc:
        if on_error is not None:
            on_error(exc)
        return []


def fetch_day_chat_history(
    rows: Iterable[Dict[str, Any]],
    date_str: str,
    *,
    owner_shared_session_id: str,
    legacy_owner_private_session_ids: Iterable[str],
    owner_shared_local_sources: Iterable[str],
    qq_remote_sources: Iterable[str],
    assistant_name: str = "当前角色",
    owner_label: str = "主人",
) -> str:
    filtered_rows = [
        row for row in rows if not is_diary_artifact_row(row, target_date=date_str)
    ]
    if not filtered_rows:
        return "(no chat history)"
    lines = [
        format_day_transcript_line(
            row,
            owner_shared_session_id=owner_shared_session_id,
            legacy_owner_private_session_ids=legacy_owner_private_session_ids,
            owner_shared_local_sources=owner_shared_local_sources,
            qq_remote_sources=qq_remote_sources,
            assistant_name=assistant_name,
            owner_label=owner_label,
        )
        for row in filtered_rows
    ]
    lines = [line for line in lines if line]
    return "\n".join(lines) if lines else "(no chat history)"


def fetch_day_owner_chat_history(
    rows: Iterable[Dict[str, Any]],
    date_str: str,
    *,
    mode: str = "all",
    owner_shared_session_id: str,
    legacy_owner_private_session_ids: Iterable[str],
    owner_shared_local_sources: Iterable[str],
    qq_remote_sources: Iterable[str],
    assistant_name: str = "当前角色",
    owner_label: str = "主人",
) -> str:
    filtered_rows = [
        row for row in rows if not is_diary_artifact_row(row, target_date=date_str)
    ]
    if not filtered_rows:
        return ""

    qq_sources = set(qq_remote_sources or [])
    owner_rows = []
    for row in filtered_rows:
        source = row_source(row)
        session_id = str(row.get("session_id") or "").strip().lower()
        message_type = row_message_type(row)
        if is_owner_shared_row(
            row,
            owner_shared_session_id=owner_shared_session_id,
            legacy_owner_private_session_ids=legacy_owner_private_session_ids,
            owner_shared_local_sources=owner_shared_local_sources,
            qq_remote_sources=qq_remote_sources,
        ):
            if mode == "local" and source in qq_sources:
                continue
            if mode == "qq_private" and not (
                source in qq_sources and message_type == "private"
            ):
                continue
            if mode == "qq_group" and not (
                source in qq_sources and message_type == "group"
            ):
                continue
            owner_rows.append(row)
            continue

        if source in qq_sources:
            sender = row_sender(row)
            is_owner = bool(sender.get("is_owner")) if isinstance(sender, dict) else False
            if not is_owner:
                meta = row_meta(row)
                is_owner = bool(meta.get("is_owner"))
            if is_owner:
                if mode == "local":
                    continue
                if mode == "qq_private" and not (
                    session_id.startswith("private:") or message_type == "private"
                ):
                    continue
                if mode == "qq_group" and not (
                    session_id.startswith("group:") or message_type == "group"
                ):
                    continue
                owner_rows.append(row)

    if not owner_rows:
        return ""
    lines = [
        format_day_transcript_line(
            row,
            owner_shared_session_id=owner_shared_session_id,
            legacy_owner_private_session_ids=legacy_owner_private_session_ids,
            owner_shared_local_sources=owner_shared_local_sources,
            qq_remote_sources=qq_remote_sources,
            assistant_name=assistant_name,
            owner_label=owner_label,
        )
        for row in owner_rows
    ]
    lines = [line for line in lines if line]
    return "\n".join(lines)
