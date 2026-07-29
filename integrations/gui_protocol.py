from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence


PROTOCOL_VERSION = 1
MAX_CLIENT_LEN = 64
MAX_CAPABILITIES = 32
MAX_CAPABILITY_LEN = 64
DEFAULT_CAPABILITIES = frozenset({"gui.v1"})


@dataclass(frozen=True)
class GuiHello:
    client: str
    protocol_version: int
    capabilities: frozenset[str]


def parse_gui_hello(payload: Mapping[str, Any] | None) -> GuiHello:
    data = dict(payload or {})
    message_type = str(data.get("type") or "").strip().lower()
    if message_type != "hello":
        raise ValueError("hello type must be 'hello'")

    protocol_version = data.get("protocol_version")
    try:
        version = int(protocol_version)
    except (TypeError, ValueError) as exc:
        raise ValueError("protocol_version must be an integer") from exc
    if version != PROTOCOL_VERSION:
        raise ValueError(f"unsupported protocol_version: {version}")

    client = str(data.get("client") or "").strip()
    if not client:
        raise ValueError("client is required")
    if len(client) > MAX_CLIENT_LEN:
        raise ValueError("client is too long")

    raw_capabilities = data.get("capabilities") or []
    if not isinstance(raw_capabilities, Sequence) or isinstance(raw_capabilities, (str, bytes)):
        raise ValueError("capabilities must be a list")
    if len(raw_capabilities) > MAX_CAPABILITIES:
        raise ValueError("too many capabilities")

    capabilities: set[str] = set()
    for item in raw_capabilities:
        capability = str(item or "").strip()
        if not capability:
            continue
        if len(capability) > MAX_CAPABILITY_LEN:
            raise ValueError("capability is too long")
        capabilities.add(capability)
    if not capabilities:
        capabilities.update(DEFAULT_CAPABILITIES)

    return GuiHello(
        client=client,
        protocol_version=version,
        capabilities=frozenset(capabilities),
    )


def build_live2d_envelope(
    command_id: str,
    message: dict[str, object],
) -> dict[str, object]:
    return {
        "type": "live2d_protocol",
        "version": PROTOCOL_VERSION,
        "command_id": str(command_id or "").strip(),
        "message": dict(message or {}),
    }
