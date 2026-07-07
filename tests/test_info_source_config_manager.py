import json

import pytest


def test_info_source_config_manager_lists_and_saves_endpoint(tmp_path):
    from services.info_sources.config_manager import InfoSourceConfigManager

    endpoint_dir = tmp_path / "alapi"
    endpoint_dir.mkdir()
    (endpoint_dir / "weather_7d.json").write_text(
        json.dumps(
            {
                "id": "weather_7d",
                "name": "7-day weather",
                "method": "POST",
                "path": "/api/tianqi/seven",
                "params": {"city": {"type": "string", "required": False}},
            }
        ),
        encoding="utf-8",
    )

    manager = InfoSourceConfigManager(endpoint_dir)

    assert [item["id"] for item in manager.list_endpoints()] == ["weather_7d"]

    endpoint = manager.load_endpoint("weather_7d")
    endpoint["params"]["format"] = {"type": "string", "required": False, "default": "json"}
    saved_path = manager.save_endpoint(endpoint)

    saved = json.loads(saved_path.read_text(encoding="utf-8"))
    assert saved["id"] == "weather_7d"
    assert saved["params"]["format"]["default"] == "json"


def test_info_source_config_manager_lists_provider_categories(tmp_path):
    root = tmp_path / "info_sources"
    alapi_dir = root / "alapi"
    custom_dir = root / "weatherapi"
    alapi_dir.mkdir(parents=True)
    custom_dir.mkdir()
    (alapi_dir / "provider.json").write_text(
        json.dumps({"id": "alapi", "name": "ALAPI", "base_url": "https://v3.alapi.cn"}),
        encoding="utf-8",
    )
    (custom_dir / "provider.json").write_text(
        json.dumps({"id": "weatherapi", "name": "天气 API", "base_url": "https://weather.example"}),
        encoding="utf-8",
    )
    (custom_dir / "now.json").write_text(
        json.dumps({"id": "now", "name": "实况", "method": "GET", "path": "/now"}),
        encoding="utf-8",
    )

    from services.info_sources.config_manager import InfoSourceConfigManager

    manager = InfoSourceConfigManager.for_root(root)

    assert manager.list_providers() == [
            {
                "id": "alapi",
                "name": "ALAPI",
                "base_url": "https://v3.alapi.cn",
                "token_param": "token",
                "file": str(alapi_dir / "provider.json"),
            },
            {
                "id": "weatherapi",
                "name": "天气 API",
                "base_url": "https://weather.example",
                "token_param": "token",
                "file": str(custom_dir / "provider.json"),
            },
    ]

    manager.set_provider("weatherapi")

    assert [item["id"] for item in manager.list_endpoints()] == ["now"]


def test_info_source_config_manager_saves_provider_config(tmp_path):
    from services.info_sources.config_manager import InfoSourceConfigManager

    manager = InfoSourceConfigManager.for_root(tmp_path / "info_sources", provider_id="weatherapi")

    saved_path = manager.save_provider_config(
        {
            "id": "weatherapi",
            "name": "天气 API",
            "base_url": "https://weather.example",
            "token_param": "key",
        }
    )

    saved = json.loads(saved_path.read_text(encoding="utf-8"))
    assert saved == {
        "id": "weatherapi",
        "name": "天气 API",
        "base_url": "https://weather.example",
        "token_param": "key",
    }
    assert manager.provider_id == "weatherapi"


def test_info_source_config_manager_builds_ai_draft_from_doc():
    from services.info_sources.config_manager import InfoSourceConfigManager

    manager = InfoSourceConfigManager("unused")

    draft = manager.build_alapi_draft_from_text(
        """
        天气预报接口
        POST https://v3.alapi.cn/api/tianqi/seven
        参数: token 必填; city 可选; city_id 可选; format 默认 json
        """
    )

    assert draft["method"] == "POST"
    assert draft["path"] == "/api/tianqi/seven"
    assert draft["params"]["format"]["default"] == "json"
    assert draft["params"]["city"]["required"] is False
    assert "token" not in draft["params"]


def test_info_source_config_manager_rejects_unsafe_endpoint_id(tmp_path):
    from services.info_sources.config_manager import InfoSourceConfigManager

    manager = InfoSourceConfigManager(tmp_path)

    with pytest.raises(ValueError, match="id must start"):
        manager.load_endpoint("../outside")


@pytest.mark.asyncio
async def test_info_source_config_manager_tests_endpoint(tmp_path):
    from services.info_sources.config_manager import InfoSourceConfigManager

    endpoint_dir = tmp_path / "alapi"
    endpoint_dir.mkdir()
    (endpoint_dir / "zaobao.json").write_text(
        json.dumps(
            {
                "id": "zaobao",
                "name": "morning news",
                "method": "POST",
                "path": "/api/zaobao",
                "params": {"format": {"type": "string", "default": "json"}},
            }
        ),
        encoding="utf-8",
    )
    calls = []

    async def fake_request(method, url, params, timeout_sec):
        calls.append((method, url, params, timeout_sec))
        return {"code": 200, "data": {"news": ["ok"]}}

    manager = InfoSourceConfigManager(endpoint_dir)
    result = await manager.test_endpoint(
        "zaobao",
        token="tok",
        request_func=fake_request,
    )

    assert result.ok is True
    assert result.data == {"news": ["ok"]}
    assert calls[0][2]["token"] == "tok"


@pytest.mark.asyncio
async def test_info_source_config_manager_tests_unsaved_endpoint_config(tmp_path):
    from services.info_sources.config_manager import InfoSourceConfigManager

    endpoint_dir = tmp_path / "alapi"
    endpoint_dir.mkdir()
    calls = []

    async def fake_request(method, url, params, timeout_sec):
        calls.append((method, url, params, timeout_sec))
        return {"code": 200, "data": {"city": "上海", "weather": "多云"}}

    manager = InfoSourceConfigManager(endpoint_dir)
    result = await manager.test_endpoint_config(
        {
            "id": "weather_now",
            "name": "天气实况",
            "method": "POST",
            "path": "/api/tianqi",
            "params": {"format": {"type": "string", "default": "json"}},
        },
        token="tok",
        params={"city": "上海"},
        request_func=fake_request,
    )

    assert result.ok is True
    assert result.data == {"city": "上海", "weather": "多云"}
    assert calls == [
        (
            "POST",
            "https://v3.alapi.cn/api/tianqi",
            {"format": "json", "token": "tok", "city": "上海"},
            10.0,
        )
    ]
    assert list(endpoint_dir.glob("*.json")) == []
