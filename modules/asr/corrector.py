"""ASR post-recognition correction layer.

Inserted between speech recognition and the downstream consumer (the LLM
chat service).  Two cooperating components raise recognition accuracy:

* :class:`CorrectionRules` — a fixed ``pattern → replacement`` table applied
  to every accepted utterance.  Covers systematic recognition errors such
  as a proper name always being misheard ("沈迪" → "沈頔").
* :class:`LearnedPairs` — a buffer of ``(src, dst)`` pairs recorded when the
  user manually fixes a recognition mistake.  A pair that recurs at least
  ``promote_threshold`` times is auto-promoted into :class:`CorrectionRules`,
  adopting the "record-twice-then-promote, idempotent" idea from upstream
  dictation engines without needing any OS accessibility / keyboard hooks.

Both stores persist to JSON and tolerate absence (a missing file reads as
empty), so the package works out-of-the-box.  All disk I/O is wrapped so a
read/write failure never blocks recognition.

CJK has no word boundaries, so matching is exact whole-substring replace.
Rules are applied in descending ``pattern`` length to stop a short rule
from consuming the prefix of a longer one ("迪" before "沈迪").
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from typing import Any, Dict, List, Optional, Tuple

from .ports import LoggerPort

__all__ = ["CorrectionRules", "LearnedPairs", "AsrCorrector"]


def _ensure_parent(path: str) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    try:
        os.makedirs(parent, exist_ok=True)
    except OSError:
        pass


def _atomic_write(path: str, payload: Dict[str, Any]) -> bool:
    _ensure_parent(path)
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(tmp, path)
        return True
    except OSError:
        return False


def _safe_read(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


class CorrectionRules:
    """Fixed ``pattern → replacement`` correction table."""

    def __init__(self, path: str, *, logger: Optional[LoggerPort] = None) -> None:
        self.path = path
        self.logger = logger
        self._lock = threading.Lock()
        self._rules: List[Dict[str, Any]] = []
        self.reload()

    def reload(self) -> None:
        data = _safe_read(self.path)
        rules = data.get("rules") if isinstance(data, dict) else None
        if not isinstance(rules, list):
            rules = []
        cleaned: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for entry in rules:
            if not isinstance(entry, dict):
                continue
            rule_id = str(entry.get("id") or uuid.uuid4().hex[:8])
            pattern = str(entry.get("pattern") or "")
            replacement = str(entry.get("replacement") or "")
            if not pattern or pattern == replacement or rule_id in seen:
                continue
            seen.add(rule_id)
            cleaned.append(
                {
                    "id": rule_id,
                    "pattern": pattern,
                    "replacement": replacement,
                    "enabled": bool(entry.get("enabled", True)),
                }
            )
        with self._lock:
            self._rules = cleaned

    def list(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(rule) for rule in self._rules]

    def add(self, pattern: str, replacement: str, *, enabled: bool = True) -> Dict[str, Any]:
        pattern = str(pattern or "").strip()
        replacement = str(replacement or "").strip()
        if not pattern or pattern == replacement:
            raise ValueError("纠错规则无效：pattern 与 replacement 不能为空或相同")
        with self._lock:
            for rule in self._rules:
                if rule["pattern"] == pattern:
                    rule["replacement"] = replacement
                    rule["enabled"] = enabled
                    self._persist_locked()
                    return dict(rule)
            rule = {"id": uuid.uuid4().hex[:8], "pattern": pattern, "replacement": replacement, "enabled": enabled}
            self._rules.append(rule)
            self._persist_locked()
            return dict(rule)

    def remove(self, rule_id: str) -> bool:
        with self._lock:
            before = len(self._rules)
            self._rules = [rule for rule in self._rules if rule["id"] != rule_id]
            removed = len(self._rules) < before
            if removed:
                self._persist_locked()
            return removed

    def set_enabled(self, rule_id: str, enabled: bool) -> bool:
        with self._lock:
            for rule in self._rules:
                if rule["id"] == rule_id:
                    rule["enabled"] = bool(enabled)
                    self._persist_locked()
                    return True
            return False

    def apply(self, text: str) -> Tuple[str, List[str]]:
        """Apply enabled rules (longest pattern first); return (result, applied_ids)."""
        if not text:
            return text, []
        with self._lock:
            active = [(rule["pattern"], rule["replacement"], rule["id"]) for rule in self._rules if rule["enabled"]]
        # Longest pattern first so "沈迪" wins over a bare "迪".
        active.sort(key=lambda triple: len(triple[0]), reverse=True)
        applied: List[str] = []
        result = text
        for pattern, replacement, rule_id in active:
            if pattern in result:
                result = result.replace(pattern, replacement)
                applied.append(rule_id)
        return result, applied

    def _persist_locked(self) -> None:
        ok = _atomic_write(self.path, {"rules": self._rules})
        if not ok and self.logger:
            self.logger.warning("🎤 [ASR] 纠错规则持久化失败: %s", self.path)


class LearnedPairs:
    """Buffer of user-recorded ``(src, dst)`` fixes; auto-promotes recurring ones."""

    def __init__(
        self,
        path: str,
        *,
        promote_threshold: int = 2,
        logger: Optional[LoggerPort] = None,
    ) -> None:
        self.path = path
        self.promote_threshold = max(1, int(promote_threshold))
        self.logger = logger
        self._lock = threading.Lock()
        self._entries: Dict[str, Dict[str, Any]] = {}
        self.reload()

    def reload(self) -> None:
        data = _safe_read(self.path)
        entries = data.get("pairs") if isinstance(data, dict) else None
        if not isinstance(entries, list):
            entries = []
        with self._lock:
            self._entries = {}
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                src = str(entry.get("src") or "").strip()
                dst = str(entry.get("dst") or "").strip()
                if not src or src == dst:
                    continue
                self._entries[src] = {
                    "src": src,
                    "dst": dst,
                    "count": int(entry.get("count", 0) or 0),
                    "promoted": bool(entry.get("promoted", False)),
                    "note": str(entry.get("note") or ""),
                }

    def list(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(entry) for entry in self._entries.values()]

    def record(self, src: str, dst: str, *, note: str = "") -> Dict[str, Any]:
        """Increment the count for ``(src, dst)``; flip ``promoted`` at threshold."""
        src = str(src or "").strip()
        dst = str(dst or "").strip()
        if not src or src == dst:
            raise ValueError("学习对无效：src 与 dst 不能为空或相同")
        with self._lock:
            existing = self._entries.get(src)
            if existing and existing["dst"] == dst:
                existing["count"] += 1
                if note and not existing.get("note"):
                    existing["note"] = note
            else:
                self._entries[src] = {"src": src, "dst": dst, "count": 1, "promoted": False, "note": note}
            entry = dict(self._entries[src])
            self._persist_locked()
        return entry

    def mark_promoted(self, src: str) -> bool:
        with self._lock:
            entry = self._entries.get(src)
            if entry and not entry["promoted"]:
                entry["promoted"] = True
                self._persist_locked()
                return True
            return False

    def remove(self, src: str) -> bool:
        with self._lock:
            removed = self._entries.pop(src, None) is not None
            if removed:
                self._persist_locked()
            return removed

    def clear_promoted(self) -> None:
        with self._lock:
            for entry in self._entries.values():
                entry["promoted"] = False
            self._persist_locked()

    def _persist_locked(self) -> None:
        ok = _atomic_write(self.path, {"pairs": list(self._entries.values())})
        if not ok and self.logger:
            self.logger.warning("🎤 [ASR] 学习对持久化失败: %s", self.path)


class AsrCorrector:
    """Coordinates :class:`CorrectionRules` and :class:`LearnedPairs` promotion."""

    def __init__(
        self,
        rules_path: str,
        learned_path: str,
        *,
        promote_threshold: int = 2,
        logger: Optional[LoggerPort] = None,
    ) -> None:
        self.logger = logger
        self.rules = CorrectionRules(rules_path, logger=logger)
        self.learned = LearnedPairs(learned_path, promote_threshold=promote_threshold, logger=logger)

    def correct(self, text: str) -> Tuple[str, List[str]]:
        """Apply enabled rules; return (corrected_text, applied_rule_ids)."""
        return self.rules.apply(text)

    def learn(self, src: str, dst: str, *, note: str = "") -> Dict[str, Any]:
        """Record a manual fix; auto-promote into the rule table at threshold."""
        entry = self.learned.record(src, dst, note=note)
        if entry.get("count", 0) >= self.learned.promote_threshold and not entry.get("promoted"):
            try:
                self.rules.add(src, dst)
                self.learned.mark_promoted(src)
                entry = dict(entry)
                entry["promoted"] = True
            except ValueError:
                pass
            if self.logger:
                self.logger.info("🎤 [ASR] 学习对已晋升为规则: %s → %s", src, dst)
        elif self.logger:
            self.logger.debug(
                "🎤 [ASR] 记录学习对(%d/%d): %s → %s",
                entry.get("count", 0),
                self.learned.promote_threshold,
                src,
                dst,
            )
        return entry

    def add_rule(self, pattern: str, replacement: str) -> Dict[str, Any]:
        return self.rules.add(pattern, replacement)

    def remove_rule(self, rule_id: str) -> bool:
        return self.rules.remove(rule_id)

    def list_rules(self) -> List[Dict[str, Any]]:
        return self.rules.list()

    def list_learned(self) -> List[Dict[str, Any]]:
        return self.learned.list()

    def reload(self) -> None:
        self.rules.reload()
        self.learned.reload()
