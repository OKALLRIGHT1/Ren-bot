import os

from modules.gui.user_ia import (
    CAPABILITY_GROUPS,
    SETTINGS_PAGES,
    capability_group,
    capability_visible,
    model_has_credentials,
    model_looks_local,
    visible_settings_pages,
)


def test_settings_pages_have_unique_ids():
    ids = [page["id"] for page in SETTINGS_PAGES]
    assert len(ids) == len(set(ids))
    assert ids[0] == "start"
    assert "character" in ids
    assert "llm" in ids
    assert "plugins" in ids


def test_visible_pages_hide_developer_items():
    normal = {page["id"] for page in visible_settings_pages(developer=False)}
    full = {page["id"] for page in visible_settings_pages(developer=True)}
    assert "start" in normal
    assert "character" in normal
    assert "plugins" in normal
    assert "qq" in normal
    assert "providers" not in normal
    assert "dependency" not in normal
    assert "status_screen" not in normal
    assert "info_sources" not in normal
    assert full > normal
    assert "providers" in full
    assert "dependency" in full


def test_capability_groups_cover_known_triggers():
    assert capability_group("search_web") == "companion"
    assert capability_group("打开") == "desktop"
    assert capability_group("qq_music") == "qq"
    assert capability_group("code_agent") == "developer"
    assert capability_group("unknown_plugin") == "companion"
    assert capability_visible("search_web", developer=False) is True
    assert capability_visible("code_agent", developer=False) is False
    assert capability_visible("code_agent", developer=True) is True
    assert "code_agent" in CAPABILITY_GROUPS


def test_model_credentials_ignore_placeholders(monkeypatch):
    assert model_has_credentials({"api_key": ""}) is False
    assert model_has_credentials({"api_key": "sk-xxxxxx"}) is False
    assert model_has_credentials({"api_key": "real-key"}) is True
    assert model_has_credentials(
        {"provider": "pool"},
        providers={"pool": {"api_key": "from-provider"}},
    )
    monkeypatch.setenv("TEST_MODEL_KEY", "from-env")
    assert model_has_credentials({"api_key_env": "TEST_MODEL_KEY"}) is True
    local = {"base_url": "http://127.0.0.1:11434/v1", "api_key": "sk-no-key-needed"}
    assert model_has_credentials(local) is True
    assert model_looks_local(local) is True
    os.environ.pop("TEST_MODEL_KEY", None)


def test_chat_setup_status_requires_character_and_model(monkeypatch):
    class FakeManager:
        def get_active_character(self):
            return {"name": "测试角色", "prompt": "hi"}

    monkeypatch.setattr(
        "modules.character_manager.character_manager", FakeManager(), raising=False
    )
    monkeypatch.setattr(
        "config.MODELS",
        {"chat-a": {"api_key": "sk-live", "model": "m"}},
        raising=False,
    )
    monkeypatch.setattr("config.LLM_ROUTER", {"default": ["chat-a"]}, raising=False)
    monkeypatch.setattr("config.PROVIDERS", {}, raising=False)

    from modules.gui import user_ia

    status = user_ia.chat_setup_status(runtime_health=None)
    assert status["character_ok"] is True
    assert status["model_ok"] is True
    assert status["ready"] is True
    assert status["model_label"] == "chat-a"
