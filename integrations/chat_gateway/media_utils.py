from __future__ import annotations

import base64
from pathlib import Path
from typing import Any, Dict
from urllib import parse, request


def _coerce_base64(raw: str) -> str:
    text = str(raw or '').strip()
    if not text:
        return ''
    if text.startswith('data:image/') and ',' in text:
        return text.split(',', 1)[1].strip()
    if text.startswith('base64://'):
        return text.split('://', 1)[1].strip()
    return text


def _read_bytes(path_value: str) -> bytes:
    path = Path(path_value)
    if path.exists() and path.is_file():
        return path.read_bytes()
    return b''


def _path_from_file_uri(uri: str) -> str:
    parsed = parse.urlparse(uri)
    path = parse.unquote(parsed.path or '')
    if path.startswith('/') and len(path) > 2 and path[2] == ':':
        path = path[1:]
    return path


def load_image_base64(image_meta: Dict[str, Any], timeout: float = 12.0) -> str:
    meta = image_meta if isinstance(image_meta, dict) else {}

    for key in ('base64', 'image_base64', 'data'):
        value = meta.get(key)
        if isinstance(value, str):
            normalized = _coerce_base64(value)
            if normalized:
                return normalized

    url = str(meta.get('url') or meta.get('src') or '').strip()
    if url:
        if url.startswith('data:image/'):
            return _coerce_base64(url)
        if url.startswith('http://') or url.startswith('https://'):
            with request.urlopen(url, timeout=timeout) as resp:
                return base64.b64encode(resp.read()).decode('ascii')
        if url.startswith('file://'):
            path_value = _path_from_file_uri(url)
            raw = _read_bytes(path_value)
            if raw:
                return base64.b64encode(raw).decode('ascii')

    file_value = str(meta.get('file') or meta.get('path') or '').strip()
    if file_value:
        normalized = _coerce_base64(file_value)
        if normalized != file_value and normalized:
            return normalized
        if file_value.startswith('file://'):
            file_value = _path_from_file_uri(file_value)
        raw = _read_bytes(file_value)
        if raw:
            return base64.b64encode(raw).decode('ascii')

    return ''
