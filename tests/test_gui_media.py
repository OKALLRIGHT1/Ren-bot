from pathlib import Path

import pytest

from integrations.gui_media import GuiMediaRegistry, MediaTicketError


def test_registry_only_serves_registered_file_once(tmp_path: Path):
    audio = tmp_path / "reply.wav"
    audio.write_bytes(b"RIFFtest")
    registry = GuiMediaRegistry(ttl_seconds=60, max_bytes=1024)
    ticket = registry.register(audio, media_type="audio/wav")
    opened = registry.consume(ticket)
    assert opened.path == audio.resolve()
    with pytest.raises(MediaTicketError, match="已使用"):
        registry.consume(ticket)


def test_registry_rejects_directory_and_oversized_file(tmp_path: Path):
    registry = GuiMediaRegistry(ttl_seconds=60, max_bytes=4)
    with pytest.raises(MediaTicketError):
        registry.register(tmp_path, media_type="audio/wav")
    big = tmp_path / "big.wav"
    big.write_bytes(b"12345")
    with pytest.raises(MediaTicketError, match="过大"):
        registry.register(big, media_type="audio/wav")


def test_registry_rejects_unknown_media_type(tmp_path: Path):
    audio = tmp_path / "reply.bin"
    audio.write_bytes(b"1234")
    registry = GuiMediaRegistry(ttl_seconds=60, max_bytes=1024)
    with pytest.raises(MediaTicketError, match="类型"):
        registry.register(audio, media_type="application/octet-stream")


def test_registry_expires_ticket(tmp_path: Path):
    from integrations.gui_media import MediaTicketEntry

    audio = tmp_path / "reply.wav"
    audio.write_bytes(b"RIFFtest")
    registry = GuiMediaRegistry(ttl_seconds=1, max_bytes=1024)
    ticket = registry.register(audio, media_type="audio/wav")
    entry = registry._entries[ticket]
    registry._entries[ticket] = MediaTicketEntry(
        path=entry.path,
        media_type=entry.media_type,
        size=entry.size,
        expires_at=0,
        used=False,
    )
    with pytest.raises(MediaTicketError, match="过期|不存在"):
        registry.consume(ticket)
