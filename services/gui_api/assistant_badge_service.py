from __future__ import annotations

import base64
import hashlib
import mimetypes
import re
import shutil
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


MAX_BADGE_BYTES = 10 * 1024 * 1024
MIN_SCALE = 0.5
MAX_SCALE = 3.0
MIN_OFFSET = -1.0
MAX_OFFSET = 1.0

_IMAGE_SIGNATURES: tuple[tuple[bytes, str, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", ".png", "image/png"),
    (b"\xff\xd8\xff", ".jpg", "image/jpeg"),
)


def _number(value: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def normalize_badge(value: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(value, dict):
        return None
    path = str(value.get("path") or "").strip().replace("\\", "/")
    if not path:
        return None
    return {
        "path": path,
        "scale": _number(value.get("scale"), 1.0, MIN_SCALE, MAX_SCALE),
        "offset_x": _number(value.get("offset_x"), 0.0, MIN_OFFSET, MAX_OFFSET),
        "offset_y": _number(value.get("offset_y"), 0.0, MIN_OFFSET, MAX_OFFSET),
        "updated_at": int(value.get("updated_at") or 0),
    }


def _image_kind(path: Path) -> Optional[Tuple[str, str]]:
    try:
        payload = path.read_bytes()
    except OSError:
        return None
    if payload.startswith(_IMAGE_SIGNATURES[0][0]):
        if (
            len(payload) < 33
            or payload[12:16] != b"IHDR"
            or int.from_bytes(payload[16:20], "big") <= 0
            or int.from_bytes(payload[20:24], "big") <= 0
            or not payload.endswith(b"IEND\xaeB`\x82")
        ):
            return None
        return ".png", "image/png"
    if payload.startswith(_IMAGE_SIGNATURES[1][0]):
        return (".jpg", "image/jpeg") if payload.endswith(b"\xff\xd9") else None
    if (
        len(payload) >= 20
        and payload.startswith(b"RIFF")
        and payload[8:12] == b"WEBP"
        and payload[12:16] in {b"VP8 ", b"VP8L", b"VP8X"}
        and int.from_bytes(payload[4:8], "little") + 8 <= len(payload)
    ):
        return ".webp", "image/webp"
    return None


def _safe_key(value: str) -> str:
    source = str(value or "").strip()
    readable = re.sub(r"[^A-Za-z0-9._-]+", "_", source).strip("._") or "item"
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()[:10]
    return f"{readable[:48]}-{digest}"


class AssistantBadgeStore:
    def __init__(self, characters_path: Path) -> None:
        self.characters_path = Path(characters_path)
        self.data_dir = self.characters_path.parent
        self.root = self.data_dir.parent
        self.badges_dir = self.data_dir / "assistant_badges"

    def import_image(
        self, character_id: str, source_path: str, costume_name: str = ""
    ) -> Dict[str, Any]:
        source = Path(str(source_path or "").strip()).expanduser()
        try:
            size = source.stat().st_size
        except OSError:
            return {"ok": False, "error": "image_not_found"}
        if not source.is_file() or size <= 0 or size > MAX_BADGE_BYTES:
            return {"ok": False, "error": "invalid_image"}
        kind = _image_kind(source)
        if kind is None:
            return {"ok": False, "error": "invalid_image"}
        extension, _ = kind
        target_dir = self.badges_dir / _safe_key(character_id)
        if costume_name:
            target_dir = target_dir / "costumes" / _safe_key(costume_name)
        else:
            target_dir = target_dir / "default"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"source{extension}"
        temporary = target.with_suffix(target.suffix + ".tmp")
        try:
            shutil.copyfile(source, temporary)
            temporary.replace(target)
            relative = target.relative_to(self.root).as_posix()
        except (OSError, ValueError):
            temporary.unlink(missing_ok=True)
            return {"ok": False, "error": "image_copy_failed"}
        return {"ok": True, "path": relative}

    def image_data_url(self, relative_path: str) -> str:
        candidate = (self.root / str(relative_path or "")).resolve()
        try:
            candidate.relative_to(self.badges_dir.resolve())
        except ValueError:
            return ""
        kind = _image_kind(candidate)
        if kind is None:
            return ""
        _, detected_type = kind
        media_type = mimetypes.guess_type(candidate.name)[0] or detected_type
        try:
            encoded = base64.b64encode(candidate.read_bytes()).decode("ascii")
        except OSError:
            return ""
        return f"data:{media_type};base64,{encoded}"

    @staticmethod
    def record(path: str, scale: Any, offset_x: Any, offset_y: Any) -> Dict[str, Any]:
        return {
            "path": str(path).replace("\\", "/"),
            "scale": _number(scale, 1.0, MIN_SCALE, MAX_SCALE),
            "offset_x": _number(offset_x, 0.0, MIN_OFFSET, MAX_OFFSET),
            "offset_y": _number(offset_y, 0.0, MIN_OFFSET, MAX_OFFSET),
            "updated_at": int(time.time() * 1000),
        }
