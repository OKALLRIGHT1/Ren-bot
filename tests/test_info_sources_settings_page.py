import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets

from modules.gui.settings_pages.info_sources_page import InfoSourcesSettingsPage


def _app():
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    return app


def test_info_sources_settings_page_collects_manual_endpoint(tmp_path):
    _app()
    page = InfoSourcesSettingsPage(endpoint_dir=tmp_path)

    page.inp_id.setText("weather_now")
    page.inp_name.setText("天气实况")
    page.inp_method.setCurrentText("POST")
    page.inp_path.setText("/api/tianqi")
    page.inp_cache.setValue(600)
    page.params_edit.setPlainText('{"format": {"type": "string", "default": "json"}}')

    endpoint = page.collect_endpoint()

    assert endpoint["id"] == "weather_now"
    assert endpoint["name"] == "天气实况"
    assert endpoint["method"] == "POST"
    assert endpoint["path"] == "/api/tianqi"
    assert endpoint["cache_ttl_sec"] == 600
    assert endpoint["params"]["format"]["default"] == "json"


def test_info_sources_settings_page_lists_provider_categories(tmp_path):
    _app()
    (tmp_path / "alapi").mkdir()
    (tmp_path / "weatherapi").mkdir()
    (tmp_path / "alapi" / "provider.json").write_text(
        '{"id": "alapi", "name": "ALAPI"}',
        encoding="utf-8",
    )
    (tmp_path / "weatherapi" / "provider.json").write_text(
        '{"id": "weatherapi", "name": "天气 API"}',
        encoding="utf-8",
    )

    page = InfoSourcesSettingsPage(source_root=tmp_path)

    assert [
        page.provider_combo.itemData(index)
        for index in range(page.provider_combo.count())
    ] == ["alapi", "weatherapi"]


def test_info_sources_settings_page_loads_and_saves_alapi_token(tmp_path, monkeypatch):
    _app()
    monkeypatch.setattr(QtWidgets.QMessageBox, "information", lambda *args, **kwargs: None)
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning", lambda *args, **kwargs: None)

    class SecretStore:
        def __init__(self):
            self.values = {("magic_daily", "api_token"): "saved-token"}
            self.saved = []

        def get_secret(self, plugin_trigger, secret_key):
            return self.values.get((plugin_trigger, secret_key), "")

        def set_secret(self, plugin_trigger, secret_key, secret_value):
            self.saved.append((plugin_trigger, secret_key, secret_value))
            self.values[(plugin_trigger, secret_key)] = secret_value

    store = SecretStore()
    page = InfoSourcesSettingsPage(endpoint_dir=tmp_path, alapi_secret_store=store)

    assert page.alapi_token_input.text() == "saved-token"

    page.alapi_token_input.setText("new-token")
    page.save_alapi_token()

    assert store.saved == [("magic_daily", "api_token", "new-token")]


def test_info_sources_settings_page_switches_provider_for_save(tmp_path, monkeypatch):
    _app()
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning", lambda *args, **kwargs: None)
    (tmp_path / "alapi").mkdir()
    (tmp_path / "weatherapi").mkdir()
    (tmp_path / "alapi" / "provider.json").write_text(
        '{"id": "alapi", "name": "ALAPI"}',
        encoding="utf-8",
    )
    (tmp_path / "weatherapi" / "provider.json").write_text(
        '{"id": "weatherapi", "name": "天气 API"}',
        encoding="utf-8",
    )
    page = InfoSourcesSettingsPage(source_root=tmp_path)

    page.provider_combo.setCurrentIndex(1)
    page.inp_id.setText("now")
    page.inp_name.setText("实况")
    page.inp_method.setCurrentText("GET")
    page.inp_path.setText("/now")
    page.params_edit.setPlainText("{}")

    page.save_endpoint()

    assert (tmp_path / "weatherapi" / "now.json").exists()
    assert not (tmp_path / "alapi" / "now.json").exists()


def test_info_sources_settings_page_ai_draft_fills_form(tmp_path, monkeypatch):
    _app()
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning", lambda *args, **kwargs: None)
    page = InfoSourcesSettingsPage(endpoint_dir=tmp_path)

    page.doc_edit.setPlainText(
        "POST https://v3.alapi.cn/api/tianqi/seven 参数: token 必填; city 可选; format 默认 json"
    )

    page.generate_draft()

    assert page.inp_id.text() == "tianqi_seven"
    assert page.inp_method.currentText() == "POST"
    assert page.inp_path.text() == "/api/tianqi/seven"
    assert page.collect_endpoint()["params"]["format"]["default"] == "json"
    assert not list(tmp_path.glob("*.json"))


def test_info_sources_settings_page_save_writes_endpoint_json(tmp_path, monkeypatch):
    _app()
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning", lambda *args, **kwargs: None)
    page = InfoSourcesSettingsPage(endpoint_dir=tmp_path)
    page.inp_id.setText("weather_now")
    page.inp_name.setText("天气实况")
    page.inp_method.setCurrentText("POST")
    page.inp_path.setText("/api/tianqi")
    page.params_edit.setPlainText('{"format": {"type": "string", "default": "json"}}')

    page.save_endpoint()

    saved = (tmp_path / "weather_now.json").read_text(encoding="utf-8")
    assert '"id": "weather_now"' in saved
    assert '"path": "/api/tianqi"' in saved
    assert page.endpoint_list.count() == 1


def test_info_sources_settings_page_rejects_non_object_test_params(tmp_path, monkeypatch):
    _app()
    warnings = []
    monkeypatch.setattr(
        QtWidgets.QMessageBox,
        "warning",
        lambda *args, **kwargs: warnings.append(args),
    )
    page = InfoSourcesSettingsPage(endpoint_dir=tmp_path)
    page.inp_id.setText("weather_now")
    page.inp_name.setText("天气实况")
    page.inp_method.setCurrentText("POST")
    page.inp_path.setText("/api/tianqi")
    page.params_edit.setPlainText("{}")
    page.test_params_edit.setPlainText('["not", "object"]')

    page.test_endpoint()

    assert "测试参数必须是 JSON 对象" in page.test_result.toPlainText()
    assert warnings
    assert not list(tmp_path.glob("*.json"))
