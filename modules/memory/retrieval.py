import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


logger = logging.getLogger(__name__)


INJECTED_SECTIONS = [
    "【相关知识库】",
    "【回忆片段】",
    "【工具使用记录】",
    "【用户档案与自我认知】",
    "【当前待办/承诺】",
    "【重要笔记 (Memory Items)】",
    "【近期对话摘要 (Episodes)】",
]

KNOWLEDGE_INJECT_MAX_ITEMS = 2
KNOWLEDGE_INJECT_MAX_BODY_CHARS = 800
KNOWLEDGE_INJECT_MAX_ITEM_CHARS = 400
KNOWLEDGE_SOURCE_NAME_MAX = 48
KNOWLEDGE_VECTOR_FETCH_CAP = 8
KNOWLEDGE_ALIAS_MIN_CHARS = 2
KNOWLEDGE_INJECT_CONSTRAINTS = (
    "这些内容只来自资料文件，不是用户原话，也不要说成「你说过 / 我记得你」。\n"
    "若与用户当前消息冲突，以用户为准。"
)


@dataclass(frozen=True)
class KnowledgeHit:
    id: str
    content: str
    source: str
    source_path: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    score: Optional[float] = None


def _source_label(source: str, source_path: str = "") -> str:
    name = str(source or "").strip()
    if not name:
        path = str(source_path or "").replace("\\", "/").rstrip("/")
        name = path.rsplit("/", 1)[-1] if path else ""
    name = name.replace("《", "").replace("》", "").strip() or "资料"
    if len(name) > KNOWLEDGE_SOURCE_NAME_MAX:
        name = name[: KNOWLEDGE_SOURCE_NAME_MAX - 1] + "…"
    return name


def trim_knowledge_body(text: str, max_chars: int) -> str:
    value = clean_injected_context(str(text or "")).strip()
    value = value.replace("<knowledge_data>", "").replace("</knowledge_data>", "")
    if not value:
        return ""
    limit = max(0, int(max_chars))
    if len(value) <= limit:
        return value
    window = value[:limit]
    cut = -1
    for index in range(len(window) - 1, -1, -1):
        if window[index] in "。！？!?\n":
            cut = index + 1
            break
    if cut >= 1 and window[:cut].strip():
        return window[:cut].strip()
    return window.strip()


def coerce_knowledge_hit(item: Any) -> Optional[KnowledgeHit]:
    if isinstance(item, KnowledgeHit):
        content = clean_injected_context(item.content)
        if not content:
            return None
        return KnowledgeHit(
            id=str(item.id or ""),
            content=content,
            source=_source_label(item.source, item.source_path),
            source_path=str(item.source_path or ""),
            metadata=dict(item.metadata or {}),
            score=item.score,
        )
    if isinstance(item, str):
        content = clean_injected_context(item)
        if not content:
            return None
        return KnowledgeHit(
            id="",
            content=content,
            source="资料",
            source_path="",
            metadata={},
        )
    return None


def format_knowledge_hit_for_display(item: Any) -> str:
    hit = coerce_knowledge_hit(item)
    if hit is None:
        return ""
    return f"《{hit.source}》\n{hit.content}"


def format_knowledge_hits_for_display(items: Any) -> List[str]:
    rows = []
    for item in items or []:
        text = format_knowledge_hit_for_display(item)
        if text:
            rows.append(text)
    return rows


def format_knowledge_hits_for_prompt(
    items: Any,
    *,
    max_items: int = KNOWLEDGE_INJECT_MAX_ITEMS,
    max_item_chars: int = KNOWLEDGE_INJECT_MAX_ITEM_CHARS,
    max_total_chars: int = KNOWLEDGE_INJECT_MAX_BODY_CHARS,
) -> str:
    lines: List[str] = []
    used = 0
    for item in items or []:
        if len(lines) >= max(0, int(max_items)) or used >= max_total_chars:
            break
        hit = coerce_knowledge_hit(item)
        if hit is None:
            continue
        limit = min(int(max_item_chars), int(max_total_chars) - used)
        if limit < 8:
            break
        body = trim_knowledge_body(hit.content, limit)
        if not body:
            continue
        lines.append(f"· 《{hit.source}》: {body}")
        used += len(body)
    if not lines:
        return ""
    return (
        f"{KNOWLEDGE_INJECT_CONSTRAINTS}\n"
        f"<knowledge_data>\n"
        + "\n".join(lines)
        + "\n</knowledge_data>"
    )


def clean_injected_context(text: str) -> str:
    raw = str(text or "")
    if not raw:
        return ""
    cleaned = raw
    for marker in INJECTED_SECTIONS:
        idx = cleaned.find(marker)
        if idx != -1:
            cleaned = cleaned[:idx].strip()
    return cleaned.strip()


def _extract_keywords(extractor, text: str) -> List[str]:
    try:
        return list(extractor(text) or [])
    except Exception:
        return []


def build_memory_metadata(
    content: str, base_meta: Dict[str, Any] | None = None, *, extractor=None
) -> Dict[str, Any]:
    meta = dict(base_meta or {})
    text = str(content or "").strip()
    speaker_id = str(meta.get("user_id") or meta.get("speaker_id") or "").strip()
    if speaker_id:
        meta["speaker_id"] = speaker_id
        meta["participants"] = [speaker_id]
    else:
        meta.setdefault("participants", [])

    entities = _extract_keywords(extractor, text)[:20]
    relations: List[List[str]] = []
    relation_seen = set()
    for sentence in re.split(r"[。！？!?；;\n]+", text):
        sentence_entities = _extract_keywords(extractor, sentence)[:8]
        for i in range(len(sentence_entities)):
            for j in range(i + 1, len(sentence_entities)):
                a = sentence_entities[i]
                b = sentence_entities[j]
                if not a or not b or a == b:
                    continue
                edge = (a, b) if a < b else (b, a)
                if edge in relation_seen:
                    continue
                relation_seen.add(edge)
                relations.append([edge[0], edge[1]])
                if len(relations) >= 40:
                    break
            if len(relations) >= 40:
                break
        if len(relations) >= 40:
            break
    meta["entities"] = entities
    meta["relations"] = relations
    meta["recorded_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    return meta


def serialize_vector_metadata(meta: Dict[str, Any]) -> Dict[str, Any]:
    serialized: Dict[str, Any] = {}
    for key, value in (meta or {}).items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            serialized[key] = value
        elif isinstance(value, (list, dict)):
            serialized[key] = json.dumps(value, ensure_ascii=False)
        else:
            serialized[key] = str(value)
    return serialized


def deserialize_vector_metadata(meta: Dict[str, Any]) -> Dict[str, Any]:
    restored: Dict[str, Any] = {}
    for key, value in (meta or {}).items():
        if not isinstance(value, str):
            restored[key] = value
            continue
        text = value.strip()
        if not text:
            restored[key] = value
            continue
        if (text.startswith("[") and text.endswith("]")) or (
            text.startswith("{") and text.endswith("}")
        ):
            try:
                restored[key] = json.loads(text)
                continue
            except Exception:
                pass
        restored[key] = value
    return restored


def _expand_graph_keywords(
    base_keywords: List[str], detailed_results: List[Dict[str, Any]]
) -> List[str]:
    if not base_keywords:
        return []
    expanded: List[str] = []
    seen = set(base_keywords)
    for result in detailed_results:
        meta = result.get("meta", {}) or result.get("_meta", {})
        if not isinstance(meta, dict):
            continue
        relations = meta.get("relations", [])
        if not isinstance(relations, list):
            continue
        for pair in relations:
            if not (isinstance(pair, list) and len(pair) == 2):
                continue
            a = str(pair[0] or "").strip().lower()
            b = str(pair[1] or "").strip().lower()
            if not a or not b:
                continue
            if a in seen and b not in seen:
                expanded.append(b)
                seen.add(b)
            elif b in seen and a not in seen:
                expanded.append(a)
                seen.add(a)
    return expanded


def post_process_memory_candidates(
    plugin, candidates: List[Dict[str, Any]], query_text: str, *, sender_id: str = ""
) -> List[Dict[str, Any]]:
    if not candidates:
        return []

    normalized_sender_id = str(sender_id or "").strip()
    prepared = list(candidates)

    if getattr(plugin, "participant_filtering_enabled", False) and normalized_sender_id:
        filtered = []
        for result in prepared:
            meta = result.get("meta", {}) or result.get("_meta", {})
            participants = (
                meta.get("participants", []) if isinstance(meta, dict) else []
            )
            if not isinstance(participants, list) or not participants:
                filtered.append(result)
                continue
            normalized = {str(x).strip() for x in participants if str(x).strip()}
            if normalized_sender_id in normalized:
                filtered.append(result)
        if filtered:
            prepared = filtered

    keywords = _extract_keywords(
        getattr(plugin, "_extract_keywords", lambda _t: []), query_text
    )
    if not keywords:
        return prepared
    expanded = (
        _expand_graph_keywords(keywords, prepared)
        if getattr(plugin, "graph_rerank_enabled", True)
        else []
    )
    all_terms = keywords + [term for term in expanded if term not in keywords]

    def _semantic_score(item: Dict[str, Any]) -> float:
        dist = item.get("sim")
        try:
            return float(dist)
        except Exception:
            return 0.0

    scored = []
    for item in prepared:
        content = str(item.get("doc") or item.get("content") or "")
        content_l = content.lower()
        keyword_hits = sum(1 for term in all_terms if str(term).lower() in content_l)
        scored.append((keyword_hits, _semantic_score(item), item))

    if any(hit > 0 for hit, _, _ in scored):
        scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
        return [item for _, _, item in scored]
    return prepared


def _knowledge_fetch_size(k: int) -> int:
    want = max(1, int(k or 1))
    return min(KNOWLEDGE_VECTOR_FETCH_CAP, max(want, want * 3))


def _entity_terms(meta: Dict[str, Any] | None) -> List[str]:
    payload = meta or {}
    terms: List[str] = []
    name = str(payload.get("entity_name") or "").strip()
    if name:
        terms.append(name)
    aliases = payload.get("aliases")
    if isinstance(aliases, str):
        parts = aliases.split("|")
    elif isinstance(aliases, (list, tuple)):
        parts = aliases
    else:
        parts = []
    for item in parts:
        value = str(item or "").strip()
        if value:
            terms.append(value)
    seen = set()
    unique: List[str] = []
    for term in terms:
        if term in seen:
            continue
        seen.add(term)
        unique.append(term)
    return unique


def knowledge_alias_boost(query: str, meta: Dict[str, Any] | None) -> int:
    text = str(query or "")
    if not text:
        return 0
    boost = 0
    for term in _entity_terms(meta):
        if len(term) >= KNOWLEDGE_ALIAS_MIN_CHARS and term in text:
            boost += 1
    return boost


def _normalized_knowledge_text(text: str) -> str:
    return "".join(str(text or "").split())


def dedup_knowledge_hits(hits: List[KnowledgeHit]) -> List[KnowledgeHit]:
    kept: List[KnowledgeHit] = []
    for hit in hits:
        if hit.id and any(item.id == hit.id for item in kept):
            continue
        incoming = _normalized_knowledge_text(hit.content)
        if not incoming:
            continue
        drop = False
        replace_at: List[int] = []
        for index, existing in enumerate(kept):
            current = _normalized_knowledge_text(existing.content)
            if incoming == current:
                drop = True
                break
            if incoming in current and len(current) > len(incoming):
                drop = True
                break
            if current in incoming and len(incoming) > len(current):
                replace_at.append(index)
        if drop:
            continue
        for index in reversed(replace_at):
            kept.pop(index)
        kept.append(hit)
    return kept


def rerank_knowledge_hits(
    hits: List[KnowledgeHit], query: str
) -> List[KnowledgeHit]:
    scored = []
    for index, hit in enumerate(hits):
        boost = knowledge_alias_boost(query, hit.metadata)
        distance = hit.score if hit.score is not None else float("inf")
        scored.append((boost, -distance, -index, hit))
    scored.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
    return [item[3] for item in scored]


def retrieve_knowledge_chunks(
    collection, search_text: str, k: int = 2
) -> List[KnowledgeHit]:
    want = max(1, int(k or 1))
    fetch = _knowledge_fetch_size(want)
    hits: List[KnowledgeHit] = []
    try:
        res = collection.query(
            query_texts=[search_text],
            n_results=fetch,
            include=["documents", "metadatas", "distances"],
        )
        docs = (res.get("documents") or [[]])[0]
        metas = (res.get("metadatas") or [[]])[0]
        ids = (res.get("ids") or [[]])[0]
        distances = (res.get("distances") or [[]])[0]
        for index, item in enumerate(docs):
            cleaned = clean_injected_context(item)
            if not cleaned:
                continue
            meta = metas[index] if index < len(metas) and isinstance(metas[index], dict) else {}
            source_path = str(meta.get("source_path") or "")
            source = _source_label(str(meta.get("source") or ""), source_path)
            raw_id = ids[index] if index < len(ids) else ""
            score = None
            if index < len(distances):
                try:
                    score = float(distances[index])
                except Exception:
                    score = None
            hits.append(
                KnowledgeHit(
                    id=str(raw_id or ""),
                    content=cleaned,
                    source=source,
                    source_path=source_path,
                    metadata=dict(meta),
                    score=score,
                )
            )
    except Exception as exc:
        logger.warning("Knowledge vector retrieval failed: %s", exc)
        raise
    ranked = rerank_knowledge_hits(hits, search_text)
    return dedup_knowledge_hits(ranked)[:want]
