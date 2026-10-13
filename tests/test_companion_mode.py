from modules.companion_mode import apply_preset, resolve_preset


class _Flags:
    def __init__(self):
        self.tts = None
        self.dnd = None

    def set_tts_enabled(self, enabled: bool) -> None:
        self.tts = bool(enabled)

    def set_dnd_mode(self, enabled: bool) -> None:
        self.dnd = bool(enabled)


def test_quiet_turns_voice_off_and_dnd_on():
    preset = resolve_preset("quiet")
    assert preset is not None
    assert preset.tts_enabled is False
    assert preset.dnd_mode is True


def test_old_codex_name_lands_on_quiet():
    preset = resolve_preset("codex")
    assert preset is not None
    assert preset.key == "quiet"


def test_apply_preset_writes_both_host_switches():
    app = _Flags()
    text = apply_preset(app, "companion")
    assert app.tts is True
    assert app.dnd is False
    assert "陪伴" in text


def test_unknown_preset_does_not_touch_switches():
    app = _Flags()
    text = apply_preset(app, "work")
    assert app.tts is None
    assert app.dnd is None
    assert "未知模式" in text
