from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Dict, Optional

from PySide6 import QtCore, QtWidgets

from modules.plugin_secret_store import PluginSecretStore
from services.info_sources.config_manager import InfoSourceConfigManager


PROJECT_ROOT = Path(__file__).resolve().parents[3]
INFO_SOURCE_ROOT = PROJECT_ROOT / "data" / "info_sources"
ALAPI_ENDPOINT_DIR = PROJECT_ROOT / "data" / "info_sources" / "alapi"
ISUZU_CONFIG_PATH = PROJECT_ROOT / "plugins" / "Isuzu_news" / "config.json"
ALAPI_SECRET_PLUGIN_TRIGGER = "magic_daily"
ALAPI_SECRET_KEY = "api_token"


def read_alapi_token(secret_store: Optional[Any] = None) -> str:
    if secret_store is not None:
        try:
            value = secret_store.get_secret(ALAPI_SECRET_PLUGIN_TRIGGER, ALAPI_SECRET_KEY)
        except Exception:
            value = ""
        if value:
            return str(value or "").strip()
    try:
        config = json.loads(ISUZU_CONFIG_PATH.read_text(encoding="utf-8-sig"))
    except Exception:
        return ""
    raw = (config.get("settings") or {}).get("api_token", "")
    if isinstance(raw, dict):
        raw = raw.get("value", raw.get("default", ""))
    return str(raw or "").strip()


def write_alapi_token(token: str, secret_store: Optional[Any] = None) -> None:
    if secret_store is None:
        raise RuntimeError("ALAPI Token 存储不可用")
    secret_store.set_secret(
        ALAPI_SECRET_PLUGIN_TRIGGER,
        ALAPI_SECRET_KEY,
        str(token or "").strip(),
    )


class _EndpointTestWorker(QtCore.QObject):
    finished = QtCore.Signal(str)

    def __init__(
        self,
        manager: InfoSourceConfigManager,
        endpoint: Dict[str, Any],
        token: str,
        params: Dict[str, Any],
    ):
        super().__init__()
        self.manager = manager
        self.endpoint = endpoint
        self.token = token
        self.params = params

    @QtCore.Slot()
    def run(self):
        try:
            result = asyncio.run(
                self.manager.test_endpoint_config(
                    self.endpoint,
                    token=self.token,
                    params=self.params,
                )
            )
            payload = {
                "ok": result.ok,
                "provider": result.provider,
                "capability": result.capability,
                "summary": result.summary,
                "error": result.error,
                "data": result.data,
            }
            self.finished.emit(json.dumps(payload, ensure_ascii=False, indent=2))
        except Exception as exc:
            self.finished.emit(f"测试失败: {exc}")


class InfoSourcesSettingsPage(QtWidgets.QWidget):
    def __init__(
        self,
        parent=None,
        endpoint_dir: Path | str | None = ALAPI_ENDPOINT_DIR,
        source_root: Path | str | None = None,
        alapi_secret_store: Optional[Any] = None,
    ):
        super().__init__(parent)
        if source_root is not None:
            self.manager = InfoSourceConfigManager.for_root(source_root)
        else:
            self.manager = InfoSourceConfigManager(endpoint_dir or ALAPI_ENDPOINT_DIR)
        self.alapi_secret_store = (
            alapi_secret_store
            if alapi_secret_store is not None
            else self._create_secret_store()
        )
        self._test_thread = None
        self._test_worker = None
        self._build_ui()
        self.refresh_providers()
        self.refresh_list()

    def _create_secret_store(self):
        try:
            return PluginSecretStore()
        except Exception:
            return None

    def _build_ui(self):
        # 内容区给出合理内容最小宽，窄窗时由外层 scroll 横向兜底，而不是把
        # 设置中心整窗顶死在 720。
        self.setMinimumWidth(640)
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)

        left_panel = QtWidgets.QWidget()
        left = QtWidgets.QVBoxLayout(left_panel)
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(8)
        title = QtWidgets.QLabel("信息源接口")
        title.setObjectName("header")
        left.addWidget(title)

        self.provider_combo = QtWidgets.QComboBox()
        self.provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        left.addWidget(self.provider_combo)

        self.endpoint_list = QtWidgets.QListWidget()
        self.endpoint_list.currentRowChanged.connect(self._on_select_endpoint)
        self.endpoint_list.setMinimumWidth(140)
        left.addWidget(self.endpoint_list, 1)

        left_actions = QtWidgets.QHBoxLayout()
        btn_new = QtWidgets.QPushButton("新建")
        btn_new.clicked.connect(self.new_endpoint)
        btn_refresh = QtWidgets.QPushButton("刷新")
        btn_refresh.clicked.connect(self.refresh_list)
        left_actions.addWidget(btn_new)
        left_actions.addWidget(btn_refresh)
        left.addLayout(left_actions)
        splitter.addWidget(left_panel)

        right_panel = QtWidgets.QWidget()
        right = QtWidgets.QVBoxLayout(right_panel)
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(10)

        token_group = QtWidgets.QGroupBox("Provider 凭据")
        token_group.setMinimumWidth(280)
        token_layout = QtWidgets.QFormLayout(token_group)
        token_layout.setLabelAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
        token_layout.setFieldGrowthPolicy(
            QtWidgets.QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow
        )
        token_row = QtWidgets.QHBoxLayout()
        token_row.setContentsMargins(0, 0, 0, 0)
        token_row.setSpacing(8)
        self.alapi_token_input = QtWidgets.QLineEdit()
        self.alapi_token_input.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
        self.alapi_token_input.setPlaceholderText("ALAPI Token")
        self.alapi_token_input.setMinimumWidth(140)
        self.alapi_token_input.setText(read_alapi_token(self.alapi_secret_store))
        self.btn_toggle_alapi_token = QtWidgets.QPushButton("显示")
        self.btn_toggle_alapi_token.setMinimumWidth(56)
        self.btn_toggle_alapi_token.clicked.connect(self.toggle_alapi_token_visible)
        self.btn_save_alapi_token = QtWidgets.QPushButton("保存 Token")
        self.btn_save_alapi_token.setMinimumWidth(80)
        self.btn_save_alapi_token.clicked.connect(self.save_alapi_token)
        token_row.addWidget(self.alapi_token_input, 1)
        token_row.addWidget(self.btn_toggle_alapi_token)
        token_row.addWidget(self.btn_save_alapi_token)
        token_layout.addRow("ALAPI Token:", token_row)
        right.addWidget(token_group)

        form_group = QtWidgets.QGroupBox("接口配置")
        form_group.setMinimumWidth(280)
        form = QtWidgets.QFormLayout(form_group)
        form.setLabelAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
        form.setFieldGrowthPolicy(
            QtWidgets.QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow
        )
        self.inp_id = QtWidgets.QLineEdit()
        self.inp_id.setMinimumWidth(120)
        self.inp_name = QtWidgets.QLineEdit()
        self.inp_name.setMinimumWidth(120)
        self.inp_method = QtWidgets.QComboBox()
        self.inp_method.addItems(["GET", "POST"])
        self.inp_path = QtWidgets.QLineEdit()
        self.inp_path.setMinimumWidth(120)
        self.inp_cache = QtWidgets.QSpinBox()
        self.inp_cache.setRange(0, 86400)
        self.inp_cache.setSuffix(" 秒")
        self.inp_cache.setValue(600)
        form.addRow("ID:", self.inp_id)
        form.addRow("名称:", self.inp_name)
        form.addRow("方法:", self.inp_method)
        form.addRow("路径:", self.inp_path)
        form.addRow("缓存:", self.inp_cache)
        right.addWidget(form_group)

        self.params_edit = QtWidgets.QPlainTextEdit()
        self.params_edit.setPlaceholderText(
            '{\n  "city": {"type": "string", "required": false},\n  "format": {"type": "string", "default": "json"}\n}'
        )
        self.params_edit.setMinimumHeight(80)
        right.addWidget(QtWidgets.QLabel("参数 JSON"))
        right.addWidget(self.params_edit, 1)

        draft_group = QtWidgets.QGroupBox("AI 生成草稿")
        draft_layout = QtWidgets.QVBoxLayout(draft_group)
        self.doc_edit = QtWidgets.QPlainTextEdit()
        self.doc_edit.setPlaceholderText("粘贴接口说明、URL 或截图 OCR 文本。生成后只填表，不会自动保存。")
        self.doc_edit.setMinimumHeight(60)
        btn_draft = QtWidgets.QPushButton("从说明生成草稿")
        btn_draft.clicked.connect(self.generate_draft)
        draft_layout.addWidget(self.doc_edit)
        draft_layout.addWidget(btn_draft, alignment=QtCore.Qt.AlignmentFlag.AlignRight)
        right.addWidget(draft_group)

        test_group = QtWidgets.QGroupBox("测试")
        test_layout = QtWidgets.QVBoxLayout(test_group)
        self.test_params_edit = QtWidgets.QPlainTextEdit()
        self.test_params_edit.setPlaceholderText('{"city": "上海"}')
        self.test_params_edit.setMaximumHeight(70)
        self.test_result = QtWidgets.QPlainTextEdit()
        self.test_result.setReadOnly(True)
        self.test_result.setMinimumHeight(80)
        self.btn_test = QtWidgets.QPushButton("测试接口")
        self.btn_test.clicked.connect(self.test_endpoint)
        test_layout.addWidget(self.test_params_edit)
        test_layout.addWidget(self.btn_test, alignment=QtCore.Qt.AlignmentFlag.AlignRight)
        test_layout.addWidget(self.test_result)
        right.addWidget(test_group)

        footer = QtWidgets.QHBoxLayout()
        footer.addStretch()
        btn_save = QtWidgets.QPushButton("保存")
        btn_save.setObjectName("primaryAction")
        btn_save.clicked.connect(self.save_endpoint)
        footer.addWidget(btn_save)
        right.addLayout(footer)

        splitter.addWidget(right_panel)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        splitter.setSizes([220, 520])
        layout.addWidget(splitter)

    def refresh_providers(self):
        current = self.manager.provider_id
        self.provider_combo.blockSignals(True)
        self.provider_combo.clear()
        for provider in self.manager.list_providers():
            label = str(provider.get("name") or provider.get("id"))
            provider_id = str(provider.get("id") or "").strip()
            self.provider_combo.addItem(label, provider_id)
            if provider_id == current:
                self.provider_combo.setCurrentIndex(self.provider_combo.count() - 1)
        self.provider_combo.blockSignals(False)

    def _on_provider_changed(self, index: int):
        if index < 0:
            return
        provider_id = self.provider_combo.itemData(index)
        if not provider_id:
            return
        self.manager.set_provider(str(provider_id))
        self.refresh_list()

    def refresh_list(self):
        selected = self.inp_id.text().strip() if hasattr(self, "inp_id") else ""
        self.endpoint_list.clear()
        for item in self.manager.list_endpoints():
            widget_item = QtWidgets.QListWidgetItem(f"{item['id']}  ·  {item['method']} {item['path']}")
            widget_item.setData(QtCore.Qt.ItemDataRole.UserRole, item["id"])
            self.endpoint_list.addItem(widget_item)
            if item["id"] == selected:
                self.endpoint_list.setCurrentItem(widget_item)
        if self.endpoint_list.count() and self.endpoint_list.currentRow() < 0:
            self.endpoint_list.setCurrentRow(0)

    def new_endpoint(self):
        self._load_endpoint_to_form(
            {
                "id": "new_endpoint",
                "name": "new endpoint",
                "method": "POST",
                "path": "/api/endpoint",
                "cache_ttl_sec": 600,
                "params": {"format": {"type": "string", "required": False, "default": "json"}},
            }
        )

    def _on_select_endpoint(self, row: int):
        if row < 0:
            return
        item = self.endpoint_list.item(row)
        endpoint_id = item.data(QtCore.Qt.ItemDataRole.UserRole)
        try:
            endpoint = self.manager.load_endpoint(str(endpoint_id))
        except Exception as exc:
            self._show_error(f"加载失败: {exc}")
            return
        self._load_endpoint_to_form(endpoint)

    def _load_endpoint_to_form(self, endpoint: Dict[str, Any]):
        self.inp_id.setText(str(endpoint.get("id") or ""))
        self.inp_name.setText(str(endpoint.get("name") or ""))
        self.inp_method.setCurrentText(str(endpoint.get("method") or "GET").upper())
        self.inp_path.setText(str(endpoint.get("path") or ""))
        self.inp_cache.setValue(int(endpoint.get("cache_ttl_sec") or 0))
        self.params_edit.setPlainText(
            json.dumps(endpoint.get("params") or {}, ensure_ascii=False, indent=2)
        )
        self.test_result.clear()

    def collect_endpoint(self) -> Dict[str, Any]:
        params_text = self.params_edit.toPlainText().strip() or "{}"
        try:
            params = json.loads(params_text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"参数 JSON 格式错误: {exc}") from exc
        return {
            "id": self.inp_id.text().strip(),
            "name": self.inp_name.text().strip(),
            "method": self.inp_method.currentText(),
            "path": self.inp_path.text().strip(),
            "cache_ttl_sec": int(self.inp_cache.value()),
            "params": params,
        }

    def generate_draft(self):
        text = self.doc_edit.toPlainText().strip()
        if not text:
            self._show_error("请先粘贴接口说明。")
            return
        try:
            draft = self.manager.build_alapi_draft_from_text(text)
            self._load_endpoint_to_form(draft)
        except Exception as exc:
            self._show_error(f"生成草稿失败: {exc}")

    def save_endpoint(self):
        try:
            path = self.manager.save_endpoint(self.collect_endpoint())
        except Exception as exc:
            self._show_error(f"保存失败: {exc}")
            return
        self.test_result.setPlainText(f"已保存: {path}")
        self.refresh_providers()
        self.refresh_list()

    def test_endpoint(self):
        if self._test_thread is not None and self._test_thread.isRunning():
            self.test_result.setPlainText("测试仍在进行中，请等当前请求结束。")
            return
        try:
            endpoint = self.manager.validate_endpoint(self.collect_endpoint())
            params_text = self.test_params_edit.toPlainText().strip() or "{}"
            params = json.loads(params_text)
        except Exception as exc:
            self._show_error(f"测试准备失败: {exc}")
            return
        if not isinstance(params, dict):
            self._show_error("测试参数必须是 JSON 对象。")
            return
        self.test_result.setPlainText("测试中...")
        self.btn_test.setEnabled(False)
        self._test_thread = QtCore.QThread(self)
        self._test_worker = _EndpointTestWorker(
            self.manager,
            endpoint,
            self.alapi_token_input.text().strip()
            or read_alapi_token(self.alapi_secret_store),
            params,
        )
        self._test_worker.moveToThread(self._test_thread)
        self._test_thread.started.connect(self._test_worker.run)
        self._test_worker.finished.connect(self._on_test_finished)
        self._test_worker.finished.connect(self._test_thread.quit)
        self._test_worker.finished.connect(self._test_worker.deleteLater)
        self._test_thread.finished.connect(self._test_thread.deleteLater)
        self._test_thread.start()

    @QtCore.Slot(str)
    def _on_test_finished(self, text: str):
        self.test_result.setPlainText(text)
        self._test_thread = None
        self._test_worker = None
        self.btn_test.setEnabled(True)

    def _show_error(self, text: str):
        self.test_result.setPlainText(str(text))
        QtWidgets.QMessageBox.warning(self, "信息源 API", str(text))

    def toggle_alapi_token_visible(self):
        hidden = self.alapi_token_input.echoMode() == QtWidgets.QLineEdit.EchoMode.Password
        self.alapi_token_input.setEchoMode(
            QtWidgets.QLineEdit.EchoMode.Normal
            if hidden
            else QtWidgets.QLineEdit.EchoMode.Password
        )
        self.btn_toggle_alapi_token.setText("隐藏" if hidden else "显示")

    def save_alapi_token(self):
        try:
            write_alapi_token(
                self.alapi_token_input.text().strip(),
                self.alapi_secret_store,
            )
        except Exception as exc:
            self._show_error(f"保存 ALAPI Token 失败: {exc}")
            return
        QtWidgets.QMessageBox.information(self, "信息源 API", "ALAPI Token 已保存")
