import pytest

from tests.helpers import optional_plugins


def test_missing_external_plugin_has_explicit_skip_reason(tmp_path, monkeypatch):
    monkeypatch.setattr(optional_plugins, "PLUGIN_ROOT", tmp_path)
    with pytest.raises(pytest.skip.Exception, match="Optional plugins not installed: extra"):
        optional_plugins.require_optional_plugins("extra")


def test_present_but_incomplete_plugin_is_not_silently_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(optional_plugins, "PLUGIN_ROOT", tmp_path)
    (tmp_path / "extra").mkdir()
    optional_plugins.require_optional_plugins("extra")
    # The subsequent plugin import/config read must fail normally.


def test_missing_core_plugin_cannot_be_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(optional_plugins, "PLUGIN_ROOT", tmp_path)
    with pytest.raises(ValueError, match="Core plugin must not be skipped"):
        optional_plugins.require_optional_plugins("search")
