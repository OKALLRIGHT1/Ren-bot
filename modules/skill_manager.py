from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class SkillRecord:
    skill_id: str
    name: str
    path: str
    root: str
    content: str
    description: str
    aliases: List[str]


class SkillManager:
    def __init__(
        self,
        *,
        search_paths: Optional[List[str]] = None,
        enabled: bool = True,
        active_skills: Optional[List[str]] = None,
        logger: Optional[Any] = None,
    ) -> None:
        self.logger = logger
        self.enabled = bool(enabled)
        self.search_paths = self._normalize_search_paths(
            search_paths or ["./skills", "~/.codex/skills"]
        )
        self.active_skills: List[str] = []
        self.skills: Dict[str, SkillRecord] = {}
        self._alias_map: Dict[str, List[str]] = {}
        self.reload()
        self.set_active_skills(active_skills or [])

    def configure(
        self,
        *,
        enabled: Optional[bool] = None,
        search_paths: Optional[List[str]] = None,
        active_skills: Optional[List[str]] = None,
    ) -> None:
        if enabled is not None:
            self.enabled = bool(enabled)
        if search_paths is not None:
            self.search_paths = self._normalize_search_paths(search_paths)
            self.reload()
        if active_skills is not None:
            self.set_active_skills(active_skills)

    def reload(self) -> int:
        records: Dict[str, SkillRecord] = {}
        alias_map: Dict[str, List[str]] = {}
        for root in self.search_paths:
            root_path = Path(root).expanduser()
            try:
                root_path = root_path.resolve()
            except Exception:
                root_path = root_path.absolute()
            if not root_path.exists():
                continue
            for skill_file in self._find_skill_files(root_path):
                record = self._build_record(skill_file, root_path)
                if record is None:
                    continue
                records[record.skill_id] = record
                for alias in record.aliases:
                    alias_key = self._norm(alias)
                    if not alias_key:
                        continue
                    alias_map.setdefault(alias_key, [])
                    if record.skill_id not in alias_map[alias_key]:
                        alias_map[alias_key].append(record.skill_id)
        self.skills = records
        self._alias_map = alias_map
        self.active_skills = [sid for sid in self.active_skills if sid in self.skills]
        if self.logger:
            self.logger.info(
                f"SkillManager loaded skills={len(self.skills)} active={len(self.active_skills)}"
            )
        return len(self.skills)

    def list_skills(self) -> List[SkillRecord]:
        return [self.skills[key] for key in sorted(self.skills.keys())]

    def get_active_records(self) -> List[SkillRecord]:
        return [self.skills[sid] for sid in self.active_skills if sid in self.skills]

    def set_active_skills(self, skill_names: List[str]) -> List[str]:
        resolved: List[str] = []
        seen = set()
        for item in skill_names or []:
            skill_id = self.resolve_skill_id(item)
            if not skill_id or skill_id in seen:
                continue
            seen.add(skill_id)
            resolved.append(skill_id)
        self.active_skills = resolved
        return list(self.active_skills)

    def resolve_skill_id(self, value: str) -> str:
        raw = str(value or "").strip()
        if not raw:
            return ""
        if raw in self.skills:
            return raw
        norm = self._norm(raw)
        if raw in self.skills:
            return raw
        matches = self._alias_map.get(norm, [])
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            for sid in matches:
                if sid.endswith(raw):
                    return sid
        for sid, record in self.skills.items():
            if self._norm(record.name) == norm:
                return sid
        return ""

    def enable_skill(self, value: str) -> Optional[SkillRecord]:
        skill_id = self.resolve_skill_id(value)
        if not skill_id or skill_id not in self.skills:
            return None
        if skill_id not in self.active_skills:
            self.active_skills.append(skill_id)
        return self.skills[skill_id]

    def disable_skill(self, value: str) -> Optional[SkillRecord]:
        skill_id = self.resolve_skill_id(value)
        if not skill_id or skill_id not in self.skills:
            return None
        self.active_skills = [sid for sid in self.active_skills if sid != skill_id]
        return self.skills[skill_id]

    def build_prompt_addition(self) -> str:
        if not self.enabled:
            return ""
        active = self.get_active_records()
        if not active:
            return ""
        blocks = [
            "【已启用 Skills】",
            "以下内容来自用户启用的 skill 指南。它们是附加工作流与约束；若与安全规则、明确用户要求或系统能力限制冲突，以更高优先级规则为准。",
        ]
        for record in active:
            blocks.append(f"[Skill: {record.name} | id={record.skill_id}]")
            blocks.append(record.content.strip())
        return "\n".join(blocks).strip()

    def describe_skill(self, value: str) -> Optional[SkillRecord]:
        skill_id = self.resolve_skill_id(value)
        if not skill_id:
            return None
        return self.skills.get(skill_id)

    def runtime_payload(self) -> Dict[str, Any]:
        return {
            "skill_enabled": bool(self.enabled),
            "skill_search_paths": list(self.search_paths),
            "active_skills": list(self.active_skills),
        }

    def _find_skill_files(self, root_path: Path) -> List[Path]:
        out: List[Path] = []
        if root_path.is_file() and root_path.name.upper() == "SKILL.MD":
            return [root_path]
        skip_dirs = {
            ".git",
            "__pycache__",
            "node_modules",
            ".venv",
            "venv",
            "dist",
            "build",
            ".idea",
            ".vscode",
        }
        for current_root, dirs, files in os.walk(root_path):
            rel_parts = Path(current_root).relative_to(root_path).parts
            if len(rel_parts) > 5:
                dirs[:] = []
                continue
            dirs[:] = [d for d in dirs if d not in skip_dirs]
            if "SKILL.md" in files:
                out.append(Path(current_root) / "SKILL.md")
        return out

    def _build_record(self, skill_file: Path, root_path: Path) -> Optional[SkillRecord]:
        try:
            raw = skill_file.read_text(encoding="utf-8")
        except Exception:
            try:
                raw = skill_file.read_text(encoding="utf-8-sig")
            except Exception as exc:
                if self.logger:
                    self.logger.warning(f"Skill load failed: {skill_file} error={exc}")
                return None
        content = raw.strip()
        if not content:
            return None
        rel_dir = skill_file.parent.relative_to(root_path)
        rel_parts = [part for part in rel_dir.parts if part]
        clean_parts = [self._clean_id_part(part) for part in rel_parts if self._clean_id_part(part)]
        skill_id = ":".join(clean_parts) if clean_parts else self._clean_id_part(skill_file.parent.name)
        if not skill_id:
            skill_id = self._clean_id_part(root_path.name) or skill_file.parent.name
        name = self._extract_title(content) or skill_file.parent.name
        desc = self._extract_description(content)
        aliases = self._build_aliases(skill_id, name, rel_parts, skill_file.parent.name)
        return SkillRecord(
            skill_id=skill_id,
            name=name,
            path=str(skill_file),
            root=str(root_path),
            content=content,
            description=desc,
            aliases=aliases,
        )

    def _extract_title(self, content: str) -> str:
        for line in content.splitlines():
            text = line.strip()
            if text.startswith("#"):
                return text.lstrip("#").strip()
        return ""

    def _extract_description(self, content: str) -> str:
        lines = []
        started = False
        for line in content.splitlines():
            text = line.strip()
            if not text:
                if started:
                    break
                continue
            if text.startswith("#"):
                continue
            if text.startswith(("```", "- ", "* ", "1. ")):
                if started:
                    break
                continue
            started = True
            lines.append(text)
            if len(" ".join(lines)) >= 180:
                break
        return " ".join(lines)[:180].strip()

    def _build_aliases(
        self, skill_id: str, name: str, rel_parts: List[str], folder_name: str
    ) -> List[str]:
        candidates = [
            skill_id,
            name,
            folder_name,
            skill_id.replace(":", "/"),
            skill_id.replace(":", "\\"),
        ]
        if rel_parts:
            candidates.append("/".join(rel_parts))
            candidates.append(":".join(rel_parts))
            candidates.append(rel_parts[-1])
        out: List[str] = []
        seen = set()
        for item in candidates:
            text = str(item or "").strip()
            key = self._norm(text)
            if not text or not key or key in seen:
                continue
            seen.add(key)
            out.append(text)
        return out

    def _normalize_search_paths(self, paths: List[str]) -> List[str]:
        out: List[str] = []
        seen = set()
        for item in paths or []:
            text = str(item or "").strip()
            if not text:
                continue
            key = self._norm(text)
            if key in seen:
                continue
            seen.add(key)
            out.append(text)
        return out

    def _norm(self, text: str) -> str:
        return re.sub(r"[\s/\\:_-]+", "", str(text or "").strip().lower())

    def _clean_id_part(self, text: str) -> str:
        value = str(text or "").strip().replace("\\", "/")
        value = value.strip("/.")
        value = re.sub(r"\s+", "-", value)
        value = re.sub(r"[^0-9A-Za-z._:-]+", "-", value)
        value = re.sub(r"-{2,}", "-", value).strip("-")
        return value
