import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtWidgets

from modules.gui.dialogs.settings import SettingsDialog
from modules.gui.settings_pages import catalog_io, dependency_page
from modules.gui.settings_pages.asr_page import AsrSettingsPage
from modules.gui.settings_pages.color_page import ColorSettingsPage
from modules.gui.settings_pages.dependency_page import DependencySettingsPage
from modules.gui.settings_pages.llm_page import LlmSettingsPage
from modules.gui.settings_pages.provider_page import ProviderSettingsPage
from modules.gui.settings_pages.sensor_prompts_page import SensorPromptsSettingsPage
from modules.sensor_prompts import default_sensor_prompts


def _app():
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    return app


def test_asr_settings_page_collects_engine():
    _app()
    page = AsrSettingsPage()
    assert page.engine.count() == 2
    state = page.collect_state()
    assert state["asr_engine"] in {"qwen3", "zipformer"}
    assert "asr_hotwords_from_wake" in state
    page.close()


def test_color_settings_page_collects_current_palette(monkeypatch):
    _app()
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning", lambda *args, **kwargs: None)
    page = ColorSettingsPage()
    assert "accent" in page._ui_color_inputs
    assert page._ui_color_inputs["accent"].text().startswith("#")
    for inp in page._ui_color_inputs.values():
        if not page._is_valid_color(inp.text().strip()):
            inp.setText("#112233")
    palette = page.collect_settings()
    assert palette is not None
    assert palette["accent"].startswith("#")
    assert "console_main" in palette
    page.close()


def test_dependency_page_shows_ok_when_nothing_missing(monkeypatch):
    _app()
    monkeypatch.setattr(dependency_page, "scan_missing_dependencies", lambda *_a, **_k: [])
    page = DependencySettingsPage(plugins_dir=".")
    assert page.dep_title.text() == "依赖体检（无缺失）"
    assert page.dep_table.item(0, 3).text() == "OK"
    page.close()


def test_dependency_page_lists_missing_rows(monkeypatch):
    _app()
    monkeypatch.setattr(
        dependency_page,
        "scan_missing_dependencies",
        lambda *_a, **_k: [
            {"module": "foo", "package": "foo-pkg", "plugins": "demo"},
        ],
    )
    page = DependencySettingsPage(plugins_dir=".")
    assert "缺失 1" in page.dep_title.text()
    assert page.dep_table.item(0, 0).text() == "foo"
    assert page.dep_table.item(0, 1).text() == "foo-pkg"
    page.close()


def test_sensor_prompts_page_collects_and_restores(monkeypatch):
    _app()
    loaded = default_sensor_prompts()
    loaded["self_scene"] = "看见 {clean_title}。"
    monkeypatch.setattr(
        "modules.gui.settings_pages.sensor_prompts_page.load_sensor_prompts",
        lambda: dict(loaded),
    )
    page = SensorPromptsSettingsPage()
    keys = [
        page.list.item(row).data(QtCore.Qt.ItemDataRole.UserRole)
        for row in range(page.list.count())
    ]
    assert "self_scene" in keys
    page.list.setCurrentRow(keys.index("self_scene"))
    assert page.editor.toPlainText() == "看见 {clean_title}。"
    page.editor.setPlainText("新稿 {clean_title}")
    assert page.collect_state()["self_scene"] == "新稿 {clean_title}"
    page._restore_current()
    assert page.editor.toPlainText() == default_sensor_prompts()["self_scene"]
    page.close()


def test_provider_and_llm_pages_construct():
    _app()
    provider = ProviderSettingsPage()
    llm = LlmSettingsPage()
    assert provider.prov_table.columnCount() == 3
    assert llm.llm_table.columnCount() == 6
    assert llm.llm_table.horizontalHeaderItem(2).text() == "供应商"
    assert "default" in llm.router_ui
    assert "codex" not in llm.router_ui
    provider.close()
    llm.close()


def test_settings_dialog_wires_extracted_pages(monkeypatch):
    _app()
    keep = {"模型与路由", "提供商", "颜色主题", "依赖体检"}
    orig = SettingsDialog._safe_init_page

    def filtered(self, fn, label):
        if label in keep:
            orig(self, fn, label)
        else:
            self.stack.addWidget(QtWidgets.QWidget())

    monkeypatch.setattr(SettingsDialog, "_safe_init_page", filtered)
    monkeypatch.setattr(dependency_page, "scan_missing_dependencies", lambda *_a, **_k: [])
    dialog = SettingsDialog(parent=None, main_app=None)
    dialog._ensure_page("providers")
    dialog._ensure_page("llm")
    assert dialog._provider_page is not None
    assert dialog._llm_page is not None
    dialog._reload_catalog_from_disk()
    dialog.close()


def test_save_model_catalog_writes_tmp_json(tmp_path, monkeypatch):
    path = tmp_path / "custom_models.json"
    monkeypatch.setattr("config.CUSTOM_MODELS_PATH", str(path))
    assert catalog_io.save_model_catalog() is True
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert set(payload) >= {"models", "router", "providers"}


def test_retarget_model_id_rewrites_router_plugins_and_runtime(tmp_path, monkeypatch):
    models = {
        "old": {"model": "m", "provider": "pool-a"},
        "other": {"model": "n"},
    }
    router = {"default": ["old", "other"], "summary": ["old"]}
    plugin_dir = tmp_path / "plugins" / "demo"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "config.json").write_text(
        json.dumps(
            {
                "settings": {
                    "model_queue": {
                        "type": "model_queue",
                        "default": ["old", "other"],
                    },
                    "cli_path": {"type": "text", "default": "x"},
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    runtime_path = tmp_path / "runtime_settings.json"
    runtime_path.write_text(
        json.dumps(
            {
                "embedding_model_id": "old",
                "embedding_model_ids": ["old", "other"],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "modules.runtime_settings.RUNTIME_SETTINGS_PATH", runtime_path
    )

    class Plugin:
        def __init__(self):
            self.settings = {
                "model_queue": {"type": "model_queue", "default": ["old"]}
            }

    plugin = Plugin()

    class Manager:
        plugin_configs = {
            "demo": {"settings": plugin.settings},
        }
        plugins = {"demo": plugin}

    result = catalog_io.retarget_model_id(
        "old",
        "new",
        models=models,
        router=router,
        plugins_dir=tmp_path / "plugins",
        update_runtime=True,
        plugin_manager=Manager(),
    )
    assert result["ok"] is True
    assert result["changed"] is True
    assert "old" not in models
    assert models["new"]["model"] == "m"
    assert router["default"] == ["new", "other"]
    assert router["summary"] == ["new"]
    saved = json.loads((plugin_dir / "config.json").read_text(encoding="utf-8"))
    assert saved["settings"]["model_queue"]["default"] == ["new", "other"]
    assert saved["settings"]["cli_path"]["default"] == "x"
    assert plugin.settings["model_queue"]["default"] == ["new"]
    runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
    assert runtime["embedding_model_id"] == "new"
    assert runtime["embedding_model_ids"] == ["new", "other"]


def test_retarget_model_id_rejects_collision():
    models = {"old": {"model": "m"}, "taken": {"model": "n"}}
    result = catalog_io.retarget_model_id(
        "old",
        "taken",
        models=models,
        router={"default": ["old"]},
        plugins_dir=None,
        update_runtime=False,
    )
    assert result["ok"] is False
    assert result["error"] == "id_exists"
    assert "old" in models


def test_retarget_provider_name_updates_models():
    providers = {
        "pool-a": {"base_url": "https://a.example/v1"},
        "pool-b": {"base_url": "https://b.example/v1"},
    }
    models = {
        "chat-a": {"model": "m", "provider": "pool-a"},
        "chat-b": {"model": "n", "provider": "pool-b"},
    }
    result = catalog_io.retarget_provider_name(
        "pool-a",
        "pool-renamed",
        models=models,
        providers=providers,
    )
    assert result["ok"] is True
    assert "pool-a" not in providers
    assert "pool-renamed" in providers
    assert models["chat-a"]["provider"] == "pool-renamed"
    assert models["chat-b"]["provider"] == "pool-b"


def test_model_edit_dialog_keeps_provider_and_allows_id_rename(monkeypatch):
    _app()
    monkeypatch.setattr(
        "modules.gui.dialogs.settings.PROVIDERS",
        {
            "DemoPool": {
                "base_url": "https://demo.example/v1",
                "api_key": "provider-key",
            },
            "OtherPool": {
                "base_url": "https://other.example/v1",
                "api_key": "other-key",
            },
        },
        raising=False,
    )
    from modules.gui.dialogs.settings import ModelEditDialog

    dlg = ModelEditDialog(
        model_id="old-id",
        model_data={
            "model": "m1",
            "base_url": "https://custom.example/v1",
            "api_key": "model-key",
            "provider": "DemoPool",
            "purposes": ["chat"],
        },
    )
    assert dlg.inp_id.isReadOnly() is False
    assert dlg.combo_provider.currentData() == "DemoPool"
    assert dlg.inp_url.text() == "https://custom.example/v1"
    assert dlg.inp_key.text() == "model-key"
    dlg.inp_id.setText("new-id")
    dlg._on_save()
    assert dlg.result_data["id"] == "new-id"
    assert dlg.result_data["original_id"] == "old-id"
    assert dlg.result_data["config"]["provider"] == "DemoPool"
    assert dlg.result_data["config"]["base_url"] == "https://custom.example/v1"
    dlg.close()


def test_llm_page_edit_renames_model_id(monkeypatch):
    _app()
    models = {"old": {"model": "m", "provider": "p"}, "keep": {"model": "n"}}
    router = {"default": ["old", "keep"]}
    monkeypatch.setattr("modules.gui.settings_pages.llm_page.MODELS", models)
    monkeypatch.setattr("modules.gui.settings_pages.llm_page.LLM_ROUTER", router)
    monkeypatch.setattr(
        "modules.gui.settings_pages.llm_page.save_model_catalog",
        lambda **_kwargs: True,
    )
    calls = []

    def fake_retarget(old_id, new_id, **_kwargs):
        calls.append((old_id, new_id))
        models[new_id] = models.pop(old_id)
        return {"ok": True, "changed": True}

    monkeypatch.setattr(
        "modules.gui.settings_pages.llm_page.retarget_model_id", fake_retarget
    )

    class FakeDialog:
        result_data = {
            "id": "new",
            "config": {
                "model": "m2",
                "provider": "p",
                "base_url": "https://x.example/v1",
            },
        }

        def exec(self):
            return QtWidgets.QDialog.DialogCode.Accepted

    page = LlmSettingsPage()
    page._model_dialog = lambda *_args, **_kwargs: FakeDialog()
    page._on_edit_model("old")
    assert calls == [("old", "new")]
    assert "old" not in models
    assert models["new"]["model"] == "m2"
    assert models["new"]["provider"] == "p"
    page.close()


def test_model_edit_dialog_switching_provider_applies_url(monkeypatch):
    _app()
    monkeypatch.setattr(
        "modules.gui.dialogs.settings.PROVIDERS",
        {
            "DemoPool": {
                "base_url": "https://demo.example/v1",
                "api_key": "provider-key",
            },
            "OtherPool": {
                "base_url": "https://other.example/v1",
                "api_key": "other-key",
            },
        },
        raising=False,
    )
    from modules.gui.dialogs.settings import ModelEditDialog

    dlg = ModelEditDialog(
        model_id="chat-a",
        model_data={
            "model": "m1",
            "base_url": "https://custom.example/v1",
            "api_key": "model-key",
            "provider": "DemoPool",
            "purposes": ["chat"],
        },
    )
    other_idx = dlg.combo_provider.findData("OtherPool")
    assert other_idx >= 0
    dlg.combo_provider.setCurrentIndex(other_idx)
    assert dlg.inp_url.text() == "https://other.example/v1"
    assert dlg.inp_key.text() == "other-key"
    dlg._on_save()
    assert dlg.result_data["config"]["provider"] == "OtherPool"
    dlg.close()
