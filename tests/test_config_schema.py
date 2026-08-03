from modules.config_schema import infer_field_schema


def test_model_queue_schema_preserves_filter_and_limit_metadata():
    field = infer_field_schema(
        "model_queue",
        {
            "type": "model_queue",
            "default": [],
            "purpose": ["web_search"],
            "max_items": 3,
        },
    )

    assert field["ui_type"] == "model_queue"
    assert field["purposes"] == ["web_search"]
    assert field["max_items"] == 3
