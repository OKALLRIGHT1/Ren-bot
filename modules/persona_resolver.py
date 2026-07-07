from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple


CharacterProvider = Callable[[], Dict[str, Dict[str, Any]]]


@dataclass(frozen=True)
class PersonaMatch:
    character_id: str
    name: str
    prompt: str
    matched_text: str
    matched_by: str
    character: Dict[str, Any] = field(default_factory=dict)
    ambiguous: bool = False
    candidates: List[Dict[str, str]] = field(default_factory=list)

    def to_context(self) -> Dict[str, Any]:
        return {
            "character_id": self.character_id,
            "name": self.name,
            "prompt": self.prompt,
            "matched_text": self.matched_text,
            "matched_by": self.matched_by,
            "ambiguous": self.ambiguous,
            "candidates": list(self.candidates),
        }


class PersonaResolver:
    def __init__(self, character_provider: Optional[CharacterProvider] = None):
        self._character_provider = character_provider or self._default_character_provider

    def resolve(self, text: str) -> Optional[PersonaMatch]:
        query = self._normalize(text)
        if not query:
            return None

        matches: List[PersonaMatch] = []
        for character_id, character in self._characters().items():
            for matched_by, candidate in self._candidate_names(character):
                if self._normalize(candidate) != query:
                    continue
                matches.append(
                    PersonaMatch(
                        character_id=str(character_id),
                        name=str(character.get("name") or character_id),
                        prompt=str(character.get("prompt") or ""),
                        matched_text=str(candidate),
                        matched_by=matched_by,
                        character=dict(character),
                    )
                )
                break

        if not matches:
            return None
        if len(matches) == 1:
            return matches[0]
        return PersonaMatch(
            character_id="",
            name="",
            prompt="",
            matched_text=str(text or "").strip(),
            matched_by="ambiguous",
            ambiguous=True,
            candidates=[
                {"character_id": item.character_id, "name": item.name}
                for item in matches
            ],
        )

    def extract_leading_actor(self, text: str) -> Tuple[Optional[PersonaMatch], str]:
        raw = str(text or "").strip()
        if not raw.startswith("让"):
            return None, raw

        remainder = raw[1:].lstrip()
        for actor_text, command_text in self._leading_actor_candidates(remainder):
            match = self.resolve(actor_text)
            if match:
                return match, command_text.strip()
        return None, raw

    def _characters(self) -> Dict[str, Dict[str, Any]]:
        data = self._character_provider() or {}
        return data if isinstance(data, dict) else {}

    def _candidate_names(self, character: Dict[str, Any]) -> List[Tuple[str, str]]:
        candidates: List[Tuple[str, str]] = []
        name = str(character.get("name") or "").strip()
        if name:
            candidates.append(("name", name))

        aliases = character.get("aliases") or []
        if isinstance(aliases, str):
            aliases = [line.strip() for line in aliases.splitlines()]
        if isinstance(aliases, list):
            for alias in aliases:
                value = str(alias or "").strip()
                if value:
                    candidates.append(("alias", value))

        qq_profile = character.get("qq_profile") or {}
        if isinstance(qq_profile, dict):
            nickname = str(qq_profile.get("nickname") or "").strip()
            if nickname:
                candidates.append(("qq_nickname", nickname))
        return candidates

    def _leading_actor_candidates(self, text: str) -> List[Tuple[str, str]]:
        action_markers = (
            "发送邮件",
            "发送一封邮件",
            "发邮件",
            "发一封邮件",
            "给",
            "回复邮件",
            "回复",
            "回邮件",
            "转发邮件",
            "转发",
            "删除邮件",
            "删除",
        )
        candidates: List[Tuple[str, str]] = []
        for marker in action_markers:
            index = text.find(marker)
            if index <= 0:
                continue
            actor = text[:index].strip(" ，,。:")
            command = text[index:].strip()
            if actor and command:
                candidates.append((actor, command))

        simple = re.match(r"^(\S{1,24})\s+(.+)$", text)
        if simple:
            candidates.append((simple.group(1).strip(), simple.group(2).strip()))
        return candidates

    def _normalize(self, text: str) -> str:
        return re.sub(r"\s+", "", str(text or "").strip()).lower()

    def _default_character_provider(self) -> Dict[str, Dict[str, Any]]:
        from modules.character_manager import character_manager

        return character_manager.get_all_characters()
