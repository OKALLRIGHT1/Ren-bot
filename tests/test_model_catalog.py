from modules.model_catalog import (
    ROUTE_TASK_OPTIONS,
    embedding_fields_from_model,
    format_purposes_label,
    get_model_purposes,
    has_api_version_suffix,
    join_endpoint_url,
    join_openai_compat_url,
    list_model_options,
    list_models_by_purpose,
    model_has_purpose,
    normalize_model_selection,
    normalize_purposes,
    provider_fields_from_model,
    attach_images_to_last_user_message,
    chat_fallback_task,
    chat_route_has_vision,
    image_router_chain,
    resolve_router_model_ids,
    vision_router_chain,
    route_task_allow_untagged,
    route_task_ids,
    route_task_purposes,
    vision_capable_chat_model_ids,
)


def test_embedding_model_fields_are_normalized():
    fields = embedding_fields_from_model(
        "local-bge",
        {
            "model": "bge-m3",
            "base_url": "http://127.0.0.1:11434/v1",
            "purposes": ["embedding"],
            "embedding_endpoint_path": "/embeddings",
            "embedding_dimension": "1024",
            "embedding_provider": "ollama",
        },
    )

    assert fields["api_url"] == "http://127.0.0.1:11434/v1/embeddings"
    assert fields["expected_dimension"] == 1024
    assert fields["provider"] == "ollama"


def test_join_endpoint_url_dedupes_trailing_v1():
    assert (
        join_endpoint_url("https://host/v1", "/v1/images/generations")
        == "https://host/v1/images/generations"
    )
    assert (
        join_endpoint_url("https://host/v1/", "/v1/images/edits")
        == "https://host/v1/images/edits"
    )
    assert (
        join_endpoint_url("https://host", "/v1/images/generations")
        == "https://host/v1/images/generations"
    )
    assert (
        join_endpoint_url("http://20.214.141.16:3000/", "v1/images/generations")
        == "http://20.214.141.16:3000/v1/images/generations"
    )
    assert join_endpoint_url("https://host/v1", "/v1") == "https://host/v1"


def test_join_endpoint_url_respects_non_v1_version_roots():
    """Zhipu/BigModel uses /api/paas/v4 — must not become .../v4/v1/..."""
    assert has_api_version_suffix("https://open.bigmodel.cn/api/paas/v4")
    assert has_api_version_suffix("https://open.bigmodel.cn/api/paas/v4/")
    assert not has_api_version_suffix("https://api.example.com")
    assert (
        join_endpoint_url(
            "https://open.bigmodel.cn/api/paas/v4", "/v1/chat/completions"
        )
        == "https://open.bigmodel.cn/api/paas/v4/chat/completions"
    )
    assert (
        join_endpoint_url(
            "https://open.bigmodel.cn/api/paas/v4", "chat/completions"
        )
        == "https://open.bigmodel.cn/api/paas/v4/chat/completions"
    )
    assert (
        join_openai_compat_url(
            "https://open.bigmodel.cn/api/paas/v4", "chat/completions"
        )
        == "https://open.bigmodel.cn/api/paas/v4/chat/completions"
    )
    assert (
        join_openai_compat_url("https://api.deepseek.com", "chat/completions")
        == "https://api.deepseek.com/v1/chat/completions"
    )
    assert (
        join_openai_compat_url("https://api.deepseek.com/v1", "chat/completions")
        == "https://api.deepseek.com/v1/chat/completions"
    )


def test_normalize_purposes_accepts_chinese_aliases():
    assert normalize_purposes(["画图", "聊天", "image_edit"]) == [
        "image_gen",
        "chat",
    ]
    assert normalize_purposes("画图,推理") == ["image_gen", "chat"]


def test_normalize_purposes_accepts_web_search_aliases():
    assert normalize_purposes(["web_search", "search", "联网搜索"]) == [
        "web_search"
    ]


def test_get_model_purposes_from_purpose_or_purposes():
    assert get_model_purposes({"purpose": "画图"}) == ["image_gen"]
    assert get_model_purposes({"purposes": ["chat", "code", "代码"]}) == ["chat"]
    assert get_model_purposes({"purposes": ["chat", "vision"]}) == ["chat", "vision"]
    assert get_model_purposes({}) == []


def test_list_models_by_purpose_filters_and_orders():
    catalog = {
        "a": {"purposes": ["chat"]},
        "b": {"purposes": ["image_gen"]},
        "c": {"purposes": ["画图", "image_edit"]},
        "d": {"model": "untagged"},
    }
    matched = list_models_by_purpose(
        catalog, "image_gen", preferred_order=["c", "missing", "b"]
    )
    assert matched == ["c", "b"]
    assert list_models_by_purpose(catalog, "chat", allow_untagged=True) == [
        "a",
        "d",
    ]


def test_list_model_options_filters_any_declared_purpose():
    catalog = {
        "chat-a": {"model": "chat-upstream", "purposes": ["chat"]},
        "search-a": {"model": "search-upstream", "purposes": ["web_search"]},
        "untagged": {"model": "plain"},
    }

    options = list_model_options(catalog, purposes=["web_search"])

    assert [item["id"] for item in options] == ["search-a"]
    assert "search-upstream" in options[0]["label"]


def test_normalize_model_selection_deduplicates_and_honors_limit():
    assert normalize_model_selection(["a", "a", " b ", "c"], max_items=2) == [
        "a",
        "b",
    ]
    assert normalize_model_selection("a,b,c", max_items=1) == ["a"]


def test_model_has_purpose_and_labels():
    cfg = {"purposes": ["image_gen"]}
    assert model_has_purpose(cfg, "画图")
    assert not model_has_purpose({"purposes": ["chat"]}, "image_gen")
    assert format_purposes_label(["image_gen", "chat"]) == "画图、聊天"


def test_provider_fields_from_image_model_defaults_endpoint():
    fields = provider_fields_from_model(
        "draw-x",
        {
            "base_url": "http://img.example/v1",
            "api_key": "k",
            "model": "gpt-image-2",
            "purposes": ["image_gen"],
        },
    )
    assert fields["model_ref"] == "draw-x"
    assert fields["model_name"] == "gpt-image-2"
    assert fields["endpoint_path"] == "/v1/images/generations"
    assert fields["api_mode"] == "images"


def test_list_image_providers_uses_only_explicit_selection():
    from modules.model_catalog import list_image_model_options, list_image_providers

    catalog = {
        "chat": {
            "base_url": "http://chat",
            "api_key": "c",
            "model": "chat",
            "purposes": ["chat"],
        },
        "draw-a": {
            "base_url": "http://a",
            "api_key": "a",
            "model": "img-a",
            "purposes": ["image_gen"],
        },
        "draw-b": {
            "base_url": "http://b",
            "api_key": "b",
            "model": "img-b",
            "purposes": ["画图"],
        },
    }
    # Plugin selected ids take priority over router.
    providers = list_image_providers(
        catalog,
        selected_ids=["draw-b"],
        router={"image_gen": ["draw-a"]},
        request_defaults={"size_value": "512x512"},
    )
    assert [p["name"] for p in providers] == ["draw-b"]
    assert providers[0]["size_value"] == "512x512"
    assert providers[0]["endpoint_path"] == "/v1/images/generations"

    # Empty selection/route => no auto-select.
    assert list_image_providers(catalog, selected_ids=[], router={"image_gen": []}) == []

    # Chat model is rejected by purpose check.
    assert (
        list_image_providers(catalog, selected_ids=["chat", "draw-a"])[0]["name"]
        == "draw-a"
    )

    options = list_image_model_options(catalog)
    assert [item["id"] for item in options] == ["draw-a", "draw-b"]


def test_route_task_options_keep_capability_routes_only():
    ids = route_task_ids()
    assert ids == [
        "default",
        "tool_reasoning",
        "summary",
        "vision",
        "web_search",
        "image_gen",
    ]
    assert "image_edit" not in ids
    assert "sensor_vision_talk" not in ids
    assert "memory_writeback" not in ids
    assert "embedding" not in ids
    assert route_task_purposes("vision") == ("vision",)
    assert route_task_purposes("tool_reasoning") == ("chat",)
    assert route_task_allow_untagged("vision") is False
    assert route_task_allow_untagged("default") is True
    labels = {item[0]: item[1] for item in ROUTE_TASK_OPTIONS}
    assert labels["vision"] == "识图"
    assert chat_fallback_task("tool_reasoning") == "default"
    assert chat_fallback_task("sensor_vision_talk") == "default"
    assert chat_fallback_task("vision") == ""
    assert chat_fallback_task("web_search") == ""
    assert chat_fallback_task("image_edit") == "image_gen"
    assert chat_fallback_task("image_gen") == ""


def test_resolve_router_model_ids_uses_explicit_chain_then_named_fallback():
    catalog = {
        "vl-a": {"purposes": ["vision"]},
        "vl-b": {"purposes": ["vision"]},
        "chat-a": {"purposes": ["chat"]},
    }
    assert resolve_router_model_ids(
        "vision",
        catalog=catalog,
        router={"vision": ["vl-b", "missing", "vl-a"]},
        fallbacks=["vl-a"],
        required_purpose="vision",
        allow_untagged=False,
    ) == ["vl-b", "vl-a"]
    assert resolve_router_model_ids(
        "vision",
        catalog=catalog,
        router={"vision": []},
        fallbacks=["vl-a"],
        required_purpose="vision",
        allow_untagged=False,
    ) == ["vl-a"]
    assert (
        resolve_router_model_ids(
            "vision",
            catalog=catalog,
            router={"vision": ["chat-a"]},
            fallbacks=["vl-a"],
            required_purpose="vision",
            allow_untagged=False,
        )
        == []
    )
    assert (
        resolve_router_model_ids(
            "vision",
            catalog=catalog,
            router={"vision": []},
            fallbacks=["gone"],
        )
        == []
    )


def test_empty_reasoning_route_falls_back_to_chat():
    catalog = {
        "chat-a": {"purposes": ["chat"]},
        "vl-a": {"purposes": ["chat", "vision"]},
    }
    assert resolve_router_model_ids(
        "tool_reasoning",
        catalog=catalog,
        router={"default": ["chat-a"], "tool_reasoning": []},
        fallback_task="default",
    ) == ["chat-a"]


def test_chat_route_has_vision_uses_default_chain():
    catalog = {
        "chat-a": {"purposes": ["chat", "vision"]},
        "vl-only": {"purposes": ["vision"]},
    }
    assert vision_capable_chat_model_ids(
        catalog=catalog,
        router={"default": ["chat-a"], "vision": ["vl-only"]},
    ) == ["chat-a"]
    assert chat_route_has_vision(
        catalog=catalog,
        router={"default": ["chat-a"]},
    )
    assert not chat_route_has_vision(
        catalog={"plain": {"purposes": ["chat"]}},
        router={"default": ["plain"]},
    )


def test_attach_images_to_last_user_message():
    messages = [
        {"role": "system", "content": "persona"},
        {"role": "user", "content": "看看这张图"},
    ]
    out = attach_images_to_last_user_message(messages, ["abc123"])
    content = out[-1]["content"]
    assert content[0]["text"] == "看看这张图"
    assert content[1]["type"] == "image_url"
    assert "abc123" in content[1]["image_url"]["url"]
    assert messages[-1]["content"] == "看看这张图"


def test_list_image_providers_uses_merged_image_route():
    from modules.model_catalog import list_image_providers

    catalog = {
        "draw-a": {
            "base_url": "http://a",
            "api_key": "a",
            "model": "img-a",
            "purposes": ["image_gen"],
        },
        "draw-b": {
            "base_url": "http://b",
            "api_key": "b",
            "model": "img-b",
            "purposes": ["图生图"],
        },
    }
    providers = list_image_providers(
        catalog,
        image_base64="abc",
        router={"image_gen": ["draw-a"], "image_edit": ["draw-b"]},
    )
    assert [p["name"] for p in providers] == ["draw-a"]
    assert image_router_chain({"image_edit": ["draw-b"]}) == ["draw-b"]
    legacy = list_image_providers(
        catalog,
        image_base64="abc",
        router={"image_gen": [], "image_edit": ["draw-b"]},
    )
    assert [p["name"] for p in legacy] == ["draw-b"]
    assert resolve_router_model_ids(
        "image_gen",
        catalog=catalog,
        router={"image_gen": [], "image_edit": ["draw-b"]},
        required_purpose="image_gen",
        allow_untagged=False,
    ) == ["draw-b"]


def test_vision_router_chain_keeps_legacy_sensor_model():
    catalog = {
        "GLM-4V-Flash": {"purposes": ["vision"]},
        "vl-a": {"purposes": ["vision"]},
    }
    assert vision_router_chain(
        {"vision": ["vl-a"]},
        catalog=catalog,
        fallback_model="GLM-4V-Flash",
    ) == ["vl-a"]
    assert vision_router_chain(
        {"vision": []},
        catalog=catalog,
        fallback_model="GLM-4V-Flash",
    ) == ["GLM-4V-Flash"]
    assert vision_router_chain(
        {"vision": []},
        catalog={},
        fallback_model="GLM-4V-Flash",
    ) == []
