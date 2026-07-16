from integrations.gui_protocol import (
    build_live2d_envelope,
    parse_gui_hello,
)


def test_parse_hello_accepts_enhanced_capabilities():
    hello = parse_gui_hello(
        {
            "type": "hello",
            "client": "live2d-enhanced",
            "protocol_version": 1,
            "capabilities": ["gui.v1", "live2d.protocol.v1"],
        }
    )
    assert hello.client == "live2d-enhanced"
    assert "live2d.protocol.v1" in hello.capabilities


def test_parse_hello_rejects_invalid_protocol_version():
    try:
        parse_gui_hello(
            {
                "type": "hello",
                "client": "live2d-enhanced",
                "protocol_version": 2,
                "capabilities": ["gui.v1"],
            }
        )
    except ValueError as exc:
        assert "protocol_version" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_build_live2d_envelope_shape():
    envelope = build_live2d_envelope(
        "cmd-1",
        {"type": "motion", "name": "idle"},
    )
    assert envelope == {
        "type": "live2d_protocol",
        "version": 1,
        "command_id": "cmd-1",
        "message": {"type": "motion", "name": "idle"},
    }
