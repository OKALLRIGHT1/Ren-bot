import json
import logging
import re
from datetime import datetime
from typing import Any, Dict, List


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


def retrieve_knowledge_chunks(collection, search_text: str, k: int = 2) -> List[str]:
    chunks: List[str] = []
    try:
        res = collection.query(
            query_texts=[search_text],
            n_results=k,
            include=["documents", "metadatas", "distances"],
        )
        docs = (res.get("documents") or [[]])[0]
        for item in docs:
            cleaned = clean_injected_context(item)
            if cleaned:
                chunks.append(cleaned)
    except Exception as exc:
        logger.warning("Knowledge vector retrieval failed: %s", exc)
        raise
    return chunks
