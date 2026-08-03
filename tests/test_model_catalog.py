from modules.model_catalog import (
    embedding_fields_from_model,
    format_purposes_label,
    get_model_purposes,
    join_endpoint_url,
    list_model_options,
    list_models_by_purpose,
    model_has_purpose,
    normalize_model_selection,
    normalize_purposes,
    provider_fields_from_model,
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


def test_normalize_purposes_accepts_chinese_aliases():
    assert normalize_purposes(["画图", "聊天", "image_edit"]) == [
        "image_gen",
        "chat",
        "image_edit",
    ]
    assert normalize_purposes("画图,推理") == ["image_gen", "tool_reasoning"]


def test_normalize_purposes_accepts_web_search_aliases():
    assert normalize_purposes(["web_search", "search", "联网搜索"]) == [
        "web_search"
    ]


def test_get_model_purposes_from_purpose_or_purposes():
    assert get_model_purposes({"purpose": "画图"}) == ["image_gen"]
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


def test_list_image_providers_edit_falls_back_to_image_gen_route():
    from modules.model_catalog import list_image_providers

    catalog = {
        "draw-a": {
            "base_url": "http://a",
            "api_key": "a",
            "model": "img-a",
            "purposes": ["image_gen", "image_edit"],
        }
    }
    providers = list_image_providers(
        catalog,
        image_base64="abc",
        router={"image_gen": ["draw-a"], "image_edit": []},
    )
    assert [p["name"] for p in providers] == ["draw-a"]
