from services.chat_support.input_context import build_chat_input_context


def test_hud_sources_bypass_gatekeeper():
    for source in ("text_input", "tauri_gui", "qt_gui", "gui", "hud", "voice"):
        ctx = build_chat_input_context(
            {"source": source},
            is_qq_source=lambda _ctx: False,
            get_memory_session_id=lambda _ctx: "",
        )
        assert ctx.should_bypass_gatekeeper is True, source
