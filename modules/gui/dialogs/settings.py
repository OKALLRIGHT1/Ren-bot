import json

import os

import re

import config


from PySide6 import QtCore, QtGui, QtWidgets


from modules.gui.styles import get_settings_styles
from modules.gui.utils import apply_embedded_mode, wrap_in_scroll_area
from modules.gui.settings_pages.sedentary_page import SedentarySettingsPage
from modules.gui.settings_pages.info_sources_page import InfoSourcesSettingsPage
from modules.gui.settings_pages.asr_page import AsrSettingsPage
from modules.gui.settings_pages.color_page import ColorSettingsPage
from modules.gui.settings_pages.dependency_page import DependencySettingsPage
from modules.gui.settings_pages.provider_page import ProviderSettingsPage
from modules.gui.settings_pages.llm_page import LlmSettingsPage
from modules.gui.settings_pages.sensor_prompts_page import SensorPromptsSettingsPage
from modules.gui.settings_pages.external_services_page import ExternalServicesPage
from modules.gui.user_ia import (
    SETTINGS_GROUP_LABELS,
    SETTINGS_PAGE_BY_ID,
    SETTINGS_PAGES,
    is_developer_mode,
    set_developer_mode,
)
from modules.llm_probe import probe_model_endpoint


class _ProbeWorker(QtCore.QThread):
    result_ready = QtCore.Signal(dict)

    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self._cfg = dict(cfg or {})

    def run(self):
        try:
            result = probe_model_endpoint(self._cfg)
        except Exception as exc:
            result = {
                "ok": False,
                "latency_ms": 0,
                "status": 0,
                "message": str(exc),
            }
        self.result_ready.emit(result)


try:
    from modules.runtime_settings import load_runtime_settings, update_runtime_settings

except Exception:

    def load_runtime_settings():
        return {}

    def update_runtime_settings(patch):
        return patch or {}


try:
    from config import (
        COSTUME_MAP,
        CUSTOM_MODELS_PATH,
        LLM_ROUTER,
        MODELS,
        NAPCAT_ACCESS_TOKEN,
        NAPCAT_ALLOW_GROUP,
        NAPCAT_ALLOW_PRIVATE,
        NAPCAT_API_BASE,
        NAPCAT_API_TOKEN,
        NAPCAT_ENABLED,
        NAPCAT_GROUP_REQUIRE_AT,
        NAPCAT_REPLY_ENABLED,
        NAPCAT_VOICE_REPLY_ENABLED,
        NAPCAT_VOICE_REPLY_PROBABILITY,
        NAPCAT_WEBHOOK_HOST,
        NAPCAT_WEBHOOK_PATH,
        NAPCAT_WEBHOOK_PORT,
        PROVIDERS,
        REMOTE_CHAT_UI_APPEND,
    )

except ImportError:
    MODELS = {}

    COSTUME_MAP = {}

    LLM_ROUTER = {}

    PROVIDERS = {}

    CUSTOM_MODELS_PATH = "data/custom_models.json"

    NAPCAT_ENABLED = False

    NAPCAT_WEBHOOK_HOST = "127.0.0.1"

    NAPCAT_WEBHOOK_PORT = 8095

    NAPCAT_WEBHOOK_PATH = "/chat/napcat"

    NAPCAT_ACCESS_TOKEN = ""

    NAPCAT_API_BASE = "http://127.0.0.1:3000"

    NAPCAT_API_TOKEN = ""

    NAPCAT_REPLY_ENABLED = True

    NAPCAT_ALLOW_PRIVATE = True

    NAPCAT_ALLOW_GROUP = False

    NAPCAT_GROUP_REQUIRE_AT = True

    NAPCAT_VOICE_REPLY_ENABLED = False

    NAPCAT_VOICE_REPLY_PROBABILITY = 25

    REMOTE_CHAT_UI_APPEND = True


try:
    from modules.model_catalog import (
        MODEL_PURPOSE_OPTIONS,
        format_purposes_label,
        get_model_purposes,
        list_models_by_purpose,
        normalize_purposes,
    )
except Exception:
    MODEL_PURPOSE_OPTIONS = [
        ("chat", "聊天"),
        ("tool_reasoning", "推理/工具"),
        ("summary", "总结"),
        ("vision", "视觉理解"),
        ("image_gen", "画图"),
        ("image_edit", "图生图"),
        ("code", "代码"),
        ("translation", "翻译"),
        ("embedding", "向量"),
    ]

    def get_model_purposes(model_cfg):
        if not isinstance(model_cfg, dict):
            return []
        raw = model_cfg.get("purposes") or model_cfg.get("purpose") or []
        if isinstance(raw, str):
            return [x.strip() for x in raw.replace("，", ",").split(",") if x.strip()]
        if isinstance(raw, (list, tuple, set)):
            return [str(x).strip() for x in raw if str(x).strip()]
        return []

    def normalize_purposes(values):
        return get_model_purposes({"purposes": values})

    def format_purposes_label(purposes):
        label_map = {k: v for k, v in MODEL_PURPOSE_OPTIONS}
        return "、".join(label_map.get(p, p) for p in purposes) if purposes else "未设置"

    def list_models_by_purpose(catalog=None, purpose="", **kwargs):
        return []


class ProviderEditDialog(QtWidgets.QDialog):
    def __init__(self, parent=None, provider_name="", data=None):
        super().__init__(parent)

        self.setWindowTitle("编辑提供商" if provider_name else "添加提供商")

        self.setFixedWidth(460)

        self.result_data = None

        data = data or {}

        layout = QtWidgets.QVBoxLayout(self)

        form = QtWidgets.QFormLayout()

        self.inp_name = QtWidgets.QLineEdit(provider_name)

        self.inp_name.setPlaceholderText("例如: SiliconFlow, DeepSeek-Official")

        form.addRow("提供商名称:", self.inp_name)

        self.inp_url = QtWidgets.QLineEdit(str(data.get("base_url", "")))

        self.inp_url.setPlaceholderText(
            "https://api.example.com/v1 或 .../api/paas/v4"
        )

        form.addRow("Base URL:", self.inp_url)

        key_layout = QtWidgets.QHBoxLayout()

        self.inp_key = QtWidgets.QLineEdit(str(data.get("api_key", "")))

        self.inp_key.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)

        self.inp_key.setPlaceholderText("sk-...")

        btn_eye = QtWidgets.QToolButton()

        btn_eye.setText("显示")

        btn_eye.pressed.connect(
            lambda: self.inp_key.setEchoMode(QtWidgets.QLineEdit.EchoMode.Normal)
        )

        btn_eye.released.connect(
            lambda: self.inp_key.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
        )

        key_layout.addWidget(self.inp_key)

        key_layout.addWidget(btn_eye)

        form.addRow("API Key:", key_layout)

        layout.addLayout(form)

        footer = QtWidgets.QHBoxLayout()
        self.btn_probe = QtWidgets.QPushButton("测试连接")
        self.btn_probe.clicked.connect(self._on_probe)
        self.lbl_probe = QtWidgets.QLabel("")
        self.lbl_probe.setObjectName("settingsPageDesc")
        footer.addWidget(self.btn_probe)
        footer.addWidget(self.lbl_probe, 1)
        footer.addStretch()
        btn_save = QtWidgets.QPushButton("保存")
        btn_save.clicked.connect(self._on_save)
        footer.addWidget(btn_save)
        layout.addLayout(footer)
        self._probe_worker = None

    def _on_probe(self):
        cfg = {
            "base_url": self.inp_url.text().strip(),
            "api_key": self.inp_key.text().strip(),
        }
        self.lbl_probe.setText("测试中…")
        self.btn_probe.setEnabled(False)
        self._probe_worker = _ProbeWorker(cfg, self)
        self._probe_worker.result_ready.connect(self._on_probe_done)
        self._probe_worker.start()

    def _on_probe_done(self, result: dict):
        self.btn_probe.setEnabled(True)
        ok = bool(result.get("ok"))
        status = result.get("status") or 0
        latency = result.get("latency_ms") or 0
        message = result.get("message") or ""
        if ok:
            self.lbl_probe.setText(f"{status} · {latency}ms · {message}")
        else:
            prefix = f"{status} · " if status else ""
            self.lbl_probe.setText(f"{prefix}{message}" + (f" · {latency}ms" if latency else ""))

    def _on_save(self):
        name = self.inp_name.text().strip()

        if not name:
            QtWidgets.QMessageBox.warning(self, "校验失败", "提供商名称不能为空。")

            return

        self.result_data = {
            "name": name,
            "config": {
                "base_url": self.inp_url.text().strip(),
                "api_key": self.inp_key.text().strip(),
            },
        }

        self.accept()


class ModelEditDialog(QtWidgets.QDialog):
    API_STYLE_OPTIONS = [
        "",
        "openai",
        "responses",
        "gemini",
        "gemini_native",
    ]

    def __init__(
        self, parent=None, model_id="", model_data=None, on_provider_saved=None
    ):
        super().__init__(parent)

        self.setWindowTitle("编辑模型" if model_id else "添加模型")

        self.setFixedWidth(560)

        self.result_data = None

        self._original = dict(model_data or {})

        self._original_id = str(model_id or "")

        self._on_provider_saved = on_provider_saved

        layout = QtWidgets.QVBoxLayout(self)

        form = QtWidgets.QFormLayout()

        self.combo_provider = QtWidgets.QComboBox()
        self.combo_provider.setToolTip(
            "选择提供商会套用它的 URL/Key；模型自己填的地址在调用时优先。"
        )

        self._reload_provider_combo(
            select_name=str(self._original.get("provider") or "")
        )

        self.combo_provider.currentIndexChanged.connect(self._on_provider_select)

        provider_row = QtWidgets.QWidget()

        provider_layout = QtWidgets.QHBoxLayout(provider_row)

        provider_layout.setContentsMargins(0, 0, 0, 0)

        provider_layout.addWidget(self.combo_provider, 1)

        btn_add_provider = QtWidgets.QPushButton("+ 新增提供商")

        btn_add_provider.clicked.connect(self._on_add_provider_inline)

        provider_layout.addWidget(btn_add_provider)

        form.addRow("提供商:", provider_row)

        self.inp_id = QtWidgets.QLineEdit(model_id)

        self.inp_id.setPlaceholderText("my-gpt-4")

        self.inp_id.setToolTip(
            "任务路由、插件模型队列和向量选择都引用这个 ID；改名会一并更新。"
        )

        form.addRow("模型 ID:", self.inp_id)

        self.inp_model = QtWidgets.QLineEdit(str(self._original.get("model", "")))

        self.inp_model.setPlaceholderText("gpt-4o / gemini-3-flash-preview ...")

        form.addRow("上游模型名:", self.inp_model)

        self.inp_url = QtWidgets.QLineEdit(str(self._original.get("base_url", "")))

        self.inp_url.setPlaceholderText(
            "https://api.example.com/v1 或 https://open.bigmodel.cn/api/paas/v4"
        )

        form.addRow("Base URL:", self.inp_url)

        self.inp_key = QtWidgets.QLineEdit(str(self._original.get("api_key", "")))

        self.inp_key.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)

        self.inp_key.setPlaceholderText("sk-...")

        form.addRow("API Key:", self.inp_key)

        self.inp_api_key_env = QtWidgets.QLineEdit(
            str(self._original.get("api_key_env", "") or "")
        )
        self.inp_api_key_env.setPlaceholderText(
            "GROK_API_KEY,XAI_API_KEY（可选）"
        )
        form.addRow("Key 环境变量:", self.inp_api_key_env)

        self.combo_style = QtWidgets.QComboBox()

        self.combo_style.setEditable(True)

        for x in self.API_STYLE_OPTIONS:
            self.combo_style.addItem(x)

        style_text = str(self._original.get("api_style", "") or "")

        self.combo_style.setCurrentText(style_text)

        form.addRow("api_style:", self.combo_style)

        layout.addLayout(form)

        purpose_card = QtWidgets.QFrame()
        purpose_card.setObjectName("settingsHeaderCard")
        purpose_layout = QtWidgets.QVBoxLayout(purpose_card)
        purpose_layout.setContentsMargins(12, 10, 12, 10)
        purpose_layout.setSpacing(6)
        purpose_title = QtWidgets.QLabel("模型用途")
        purpose_title.setObjectName("settingsPageTitle")
        purpose_layout.addWidget(purpose_title)
        purpose_hint = QtWidgets.QLabel(
            "给模型打标签后，插件/路由可按用途筛选。例如画图插件只会使用勾选了「画图/图生图」的模型。"
        )
        purpose_hint.setObjectName("settingsPageDesc")
        purpose_hint.setWordWrap(True)
        purpose_layout.addWidget(purpose_hint)

        purpose_grid = QtWidgets.QGridLayout()
        purpose_grid.setContentsMargins(0, 0, 0, 0)
        purpose_grid.setHorizontalSpacing(10)
        purpose_grid.setVerticalSpacing(6)
        self.purpose_checks = {}
        selected_purposes = set(get_model_purposes(self._original))
        for index, (purpose_id, label) in enumerate(MODEL_PURPOSE_OPTIONS):
            check = QtWidgets.QCheckBox(label)
            check.setToolTip(purpose_id)
            check.setChecked(purpose_id in selected_purposes)
            self.purpose_checks[purpose_id] = check
            purpose_grid.addWidget(check, index // 3, index % 3)
        purpose_layout.addLayout(purpose_grid)
        layout.addWidget(purpose_card)

        self.image_card = QtWidgets.QFrame()
        self.image_card.setObjectName("settingsHeaderCard")
        image_layout = QtWidgets.QVBoxLayout(self.image_card)
        image_layout.setContentsMargins(12, 10, 12, 10)
        image_layout.setSpacing(6)
        image_title = QtWidgets.QLabel("生图接口（画图/图生图用途）")
        image_title.setObjectName("settingsPageTitle")
        image_layout.addWidget(image_title)
        image_hint = QtWidgets.QLabel(
            "画图插件只从这里读模型。可只填 Base URL + 上游模型名；端点留空时默认 /v1/images/generations。"
        )
        image_hint.setObjectName("settingsPageDesc")
        image_hint.setWordWrap(True)
        image_layout.addWidget(image_hint)
        image_form = QtWidgets.QFormLayout()
        self.inp_endpoint = QtWidgets.QLineEdit(
            str(self._original.get("endpoint_path", "") or "")
        )
        self.inp_endpoint.setPlaceholderText("/v1/images/generations")
        image_form.addRow("文生图路径:", self.inp_endpoint)
        self.inp_edit_endpoint = QtWidgets.QLineEdit(
            str(self._original.get("edit_endpoint_path", "") or "")
        )
        self.inp_edit_endpoint.setPlaceholderText("/v1/images/edits")
        image_form.addRow("图生图路径:", self.inp_edit_endpoint)
        self.inp_api_mode = QtWidgets.QLineEdit(
            str(self._original.get("api_mode", "") or "")
        )
        self.inp_api_mode.setPlaceholderText("images / chat（可选）")
        image_form.addRow("api_mode:", self.inp_api_mode)
        image_layout.addLayout(image_form)
        layout.addWidget(self.image_card)

        self.embedding_card = QtWidgets.QFrame()
        self.embedding_card.setObjectName("settingsHeaderCard")
        embedding_layout = QtWidgets.QVBoxLayout(self.embedding_card)
        embedding_layout.setContentsMargins(12, 10, 12, 10)
        embedding_layout.setSpacing(6)
        embedding_title = QtWidgets.QLabel("向量接口（向量用途）")
        embedding_title.setObjectName("settingsPageTitle")
        embedding_layout.addWidget(embedding_title)
        embedding_hint = QtWidgets.QLabel(
            "向量模型只能单选。维度必须与已有索引一致；更换模型或维度后需要明确重建。"
        )
        embedding_hint.setObjectName("settingsPageDesc")
        embedding_hint.setWordWrap(True)
        embedding_layout.addWidget(embedding_hint)
        embedding_form = QtWidgets.QFormLayout()
        self.inp_embedding_api_url = QtWidgets.QLineEdit(
            str(self._original.get("embedding_api_url", "") or "")
        )
        self.inp_embedding_api_url.setPlaceholderText(
            "https://host/v1/embeddings（可选）"
        )
        embedding_form.addRow("完整接口 URL:", self.inp_embedding_api_url)
        self.inp_embedding_endpoint = QtWidgets.QLineEdit(
            str(self._original.get("embedding_endpoint_path", "") or "")
        )
        self.inp_embedding_endpoint.setPlaceholderText("/embeddings")
        embedding_form.addRow("嵌入路径:", self.inp_embedding_endpoint)
        self.inp_embedding_dimension = QtWidgets.QSpinBox()
        self.inp_embedding_dimension.setRange(0, 65536)
        self.inp_embedding_dimension.setSpecialValueText("未设置")
        self.inp_embedding_dimension.setValue(
            int(self._original.get("embedding_dimension") or 0)
        )
        embedding_form.addRow("向量维度:", self.inp_embedding_dimension)
        self.combo_embedding_provider = QtWidgets.QComboBox()
        self.combo_embedding_provider.setEditable(True)
        self.combo_embedding_provider.addItems(["openai_compatible", "ollama"])
        self.combo_embedding_provider.setCurrentText(
            str(
                self._original.get("embedding_provider")
                or "openai_compatible"
            )
        )
        embedding_form.addRow("Provider:", self.combo_embedding_provider)
        self.inp_embedding_timeout = QtWidgets.QSpinBox()
        self.inp_embedding_timeout.setRange(1, 300)
        self.inp_embedding_timeout.setValue(
            int(self._original.get("embedding_timeout") or 12)
        )
        self.inp_embedding_timeout.setSuffix(" 秒")
        embedding_form.addRow("请求超时:", self.inp_embedding_timeout)
        embedding_layout.addLayout(embedding_form)
        layout.addWidget(self.embedding_card)

        preview_card = QtWidgets.QFrame()

        preview_card.setObjectName("settingsHeaderCard")

        preview_layout = QtWidgets.QVBoxLayout(preview_card)

        preview_layout.setContentsMargins(12, 10, 12, 10)

        preview_layout.setSpacing(6)

        preview_title = QtWidgets.QLabel("请求预览")

        preview_title.setObjectName("settingsPageTitle")

        preview_layout.addWidget(preview_title)

        self.lbl_transport_preview = QtWidgets.QLabel("")

        self.lbl_transport_preview.setObjectName("settingsPageDesc")

        self.lbl_transport_preview.setWordWrap(True)

        preview_layout.addWidget(self.lbl_transport_preview)

        self.preview_url = QtWidgets.QPlainTextEdit()

        self.preview_url.setReadOnly(True)

        self.preview_url.setFixedHeight(82)

        self.preview_url.setPlaceholderText("这里会显示当前配置预计请求的完整 URL")

        preview_layout.addWidget(self.preview_url)

        layout.addWidget(preview_card)

        tips = QtWidgets.QLabel(
            '提示：聊天模型用 api_style；画图和向量模型使用各自的专用接口字段。'
            " 向量模型在记忆窗口中单独选择，不进入普通模型回退链。"
        )

        tips.setWordWrap(True)

        tips.setStyleSheet("color:#6B7280;")

        layout.addWidget(tips)

        footer = QtWidgets.QHBoxLayout()
        self.btn_probe = QtWidgets.QPushButton("测试连接")
        self.btn_probe.clicked.connect(self._on_probe)
        self.lbl_probe = QtWidgets.QLabel("")
        self.lbl_probe.setObjectName("settingsPageDesc")
        footer.addWidget(self.btn_probe)
        footer.addWidget(self.lbl_probe, 1)
        footer.addStretch()
        btn_save = QtWidgets.QPushButton("保存")
        btn_save.clicked.connect(self._on_save)
        footer.addWidget(btn_save)
        layout.addLayout(footer)
        self._probe_worker = None

        self.combo_style.currentTextChanged.connect(self._refresh_request_preview)

        self.inp_url.textChanged.connect(self._refresh_request_preview)

        self.inp_model.textChanged.connect(self._refresh_request_preview)

        self.inp_endpoint.textChanged.connect(self._refresh_request_preview)

        self.inp_embedding_endpoint.textChanged.connect(
            self._refresh_request_preview
        )

        self.inp_embedding_api_url.textChanged.connect(
            self._refresh_request_preview
        )

        self.combo_embedding_provider.currentTextChanged.connect(
            self._refresh_request_preview
        )

        for check in self.purpose_checks.values():
            check.toggled.connect(self._refresh_image_fields_visibility)

        self._refresh_image_fields_visibility()
        self._refresh_request_preview()

    def _reload_provider_combo(self, select_name=None):
        if select_name is None:
            select_name = self.combo_provider.currentData()
        select_name = str(select_name or "").strip()

        self.combo_provider.blockSignals(True)
        self.combo_provider.clear()
        self.combo_provider.addItem("手动输入（不套用提供商）", "")
        known = set()
        for name in PROVIDERS:
            label = str(name or "").strip()
            if not label or label in known:
                continue
            known.add(label)
            self.combo_provider.addItem(label, label)
        if select_name and select_name not in known:
            self.combo_provider.addItem(f"{select_name}（未登记）", select_name)

        idx = self.combo_provider.findData(select_name) if select_name else 0
        self.combo_provider.setCurrentIndex(idx if idx >= 0 else 0)
        self.combo_provider.blockSignals(False)

    def _on_add_provider_inline(self):
        dlg = ProviderEditDialog(self)

        if dlg.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return

        data = dlg.result_data or {}

        name = str(data.get("name", "")).strip()

        cfg = data.get("config", {})

        if not name:
            return

        PROVIDERS[name] = cfg if isinstance(cfg, dict) else {}

        if callable(self._on_provider_saved):
            self._on_provider_saved()

        self._reload_provider_combo(select_name=name)

        self._on_provider_select()

    def _on_provider_select(self):
        name = str(self.combo_provider.currentData() or "").strip()
        if not name:
            return
        data = PROVIDERS.get(name)
        if not isinstance(data, dict):
            return

        self.inp_url.setText(str(data.get("base_url", "")))
        self.inp_key.setText(str(data.get("api_key", "")))
        self._refresh_request_preview()

    def _refresh_image_fields_visibility(self):
        purposes = set(self._selected_purposes())
        show_image = bool(purposes & {"image_gen", "image_edit"})
        self.image_card.setVisible(show_image)
        self.embedding_card.setVisible("embedding" in purposes)
        self._refresh_request_preview()

    def _refresh_request_preview(self):
        base_url = self.inp_url.text().strip()
        style = self.combo_style.currentText().strip().lower()
        model_name = self.inp_model.text().strip()
        purposes = set(self._selected_purposes())
        if "embedding" in purposes:
            full_url = self.inp_embedding_api_url.text().strip()
            path = self.inp_embedding_endpoint.text().strip() or "/embeddings"
            if full_url:
                endpoint = full_url
            else:
                try:
                    from modules.model_catalog import join_endpoint_url

                    endpoint = join_endpoint_url(base_url, path) if base_url else path
                except Exception:
                    endpoint = base_url.rstrip("/") + "/" + path.lstrip("/")
            transport = self.combo_embedding_provider.currentText().strip()
        elif purposes & {"image_gen", "image_edit"}:
            path = self.inp_endpoint.text().strip() or "/v1/images/generations"
            try:
                from modules.model_catalog import join_endpoint_url

                endpoint = (
                    join_endpoint_url(base_url, path)
                    if base_url
                    else (path if path.startswith("/") else "/" + path)
                )
            except Exception:
                if not path.startswith("/"):
                    path = "/" + path
                base = base_url.rstrip("/")
                try:
                    from modules.model_catalog import join_endpoint_url as _join

                    endpoint = _join(base, path) if base else path
                except Exception:
                    endpoint = (base + path) if base else path
            transport = "images"
        else:
            endpoint = self._preview_endpoint(base_url, style)
            transport = self._preview_transport(style, model_name)
        self.lbl_transport_preview.setText(
            f"传输方式：{transport}    端点：{endpoint or '(无法判断)'}"
        )
        self.preview_url.setPlainText(endpoint or "")

    def _preview_transport(self, style: str, model_name: str) -> str:
        lower_model = str(model_name or "").lower()
        if style in {"responses", "openai_responses"}:
            return "openai_responses"
        if style in {"gemini_native", "google"}:
            return "gemini_native"
        if style in {"openai"}:
            return "openai"
        if "glm" in lower_model:
            return "openai"
        if "gemini" in lower_model:
            return "openai / gemini_native（自动判断）"
        return "openai（默认）"

    def _preview_endpoint(self, base_url: str, style: str) -> str:
        base = str(base_url or "").strip().rstrip("/")
        if not base:
            return ""
        if style in {"gemini_native", "google"}:
            return base + "/v1beta/models/{model}:generateContent"
        try:
            from modules.model_catalog import join_openai_compat_url

            if style in {"responses", "openai_responses"}:
                return join_openai_compat_url(base, "responses")
            return join_openai_compat_url(base, "chat/completions")
        except Exception:
            # Fallback mirrors join_openai_compat_url for any trailing /vN.
            import re

            versioned = bool(re.search(r"/v\d+$", base, re.IGNORECASE))
            if style in {"responses", "openai_responses"}:
                return base + ("/responses" if versioned else "/v1/responses")
            return base + (
                "/chat/completions" if versioned else "/v1/chat/completions"
            )

    def _selected_purposes(self):
        selected = []
        for purpose_id, check in self.purpose_checks.items():
            if check.isChecked():
                selected.append(purpose_id)
        return normalize_purposes(selected)

    def _on_probe(self):
        import os

        key = self.inp_key.text().strip()
        if not key:
            env_names = self.inp_api_key_env.text().strip()
            for part in env_names.replace("，", ",").split(","):
                name = part.strip()
                if name and os.getenv(name):
                    key = os.getenv(name).strip()
                    break
        cfg = {
            "base_url": self.inp_url.text().strip(),
            "api_key": key,
            "model": self.inp_model.text().strip(),
            "api_style": self.combo_style.currentText().strip(),
        }
        provider_name = str(self.combo_provider.currentData() or "").strip()
        if provider_name and provider_name in PROVIDERS:
            provider = PROVIDERS.get(provider_name) or {}
            if not cfg["base_url"]:
                cfg["base_url"] = str(provider.get("base_url") or "")
            if not cfg["api_key"]:
                cfg["api_key"] = str(provider.get("api_key") or "")
        self.lbl_probe.setText("测试中…")
        self.btn_probe.setEnabled(False)
        self._probe_worker = _ProbeWorker(cfg, self)
        self._probe_worker.result_ready.connect(self._on_probe_done)
        self._probe_worker.start()

    def _on_probe_done(self, result: dict):
        self.btn_probe.setEnabled(True)
        ok = bool(result.get("ok"))
        status = result.get("status") or 0
        latency = result.get("latency_ms") or 0
        message = result.get("message") or ""
        if ok:
            self.lbl_probe.setText(f"{status} · {latency}ms · {message}")
        else:
            prefix = f"{status} · " if status else ""
            self.lbl_probe.setText(
                f"{prefix}{message}" + (f" · {latency}ms" if latency else "")
            )

    def _on_save(self):
        mid = self.inp_id.text().strip()

        if not mid:
            QtWidgets.QMessageBox.warning(self, "校验失败", "模型 ID 不能为空。")

            return

        merged = dict(self._original)

        merged["model"] = self.inp_model.text().strip()

        merged["base_url"] = self.inp_url.text().strip()

        merged["api_key"] = self.inp_key.text().strip()

        provider_name = str(self.combo_provider.currentData() or "").strip()
        if provider_name:
            merged["provider"] = provider_name
        else:
            merged.pop("provider", None)

        api_key_env = self.inp_api_key_env.text().strip()
        if api_key_env:
            merged["api_key_env"] = api_key_env
        else:
            merged.pop("api_key_env", None)

        style = self.combo_style.currentText().strip()

        if style:
            merged["api_style"] = style

        else:
            merged.pop("api_style", None)

        purposes = self._selected_purposes()
        if purposes:
            merged["purposes"] = purposes
        else:
            merged.pop("purposes", None)
        merged.pop("purpose", None)

        if "embedding" in purposes:
            full_url = self.inp_embedding_api_url.text().strip()
            if not merged["model"] or not (merged["base_url"] or full_url):
                QtWidgets.QMessageBox.warning(
                    self,
                    "校验失败",
                    "向量模型必须填写上游模型名，以及 Base URL 或完整接口 URL。",
                )
                return
            dimension = int(self.inp_embedding_dimension.value())
            if dimension <= 0:
                QtWidgets.QMessageBox.warning(
                    self,
                    "校验失败",
                    "向量维度必须是正整数。",
                )
                return
            merged["embedding_endpoint_path"] = (
                self.inp_embedding_endpoint.text().strip() or "/embeddings"
            )
            if full_url:
                merged["embedding_api_url"] = full_url
            else:
                merged.pop("embedding_api_url", None)
            merged["embedding_dimension"] = dimension
            merged["embedding_provider"] = (
                self.combo_embedding_provider.currentText().strip()
                or "openai_compatible"
            )
            merged["embedding_timeout"] = int(
                self.inp_embedding_timeout.value()
            )

        if purposes and set(purposes) & {"image_gen", "image_edit"}:
            endpoint = self.inp_endpoint.text().strip()
            edit_endpoint = self.inp_edit_endpoint.text().strip()
            api_mode = self.inp_api_mode.text().strip()
            if endpoint:
                merged["endpoint_path"] = endpoint
            else:
                merged.pop("endpoint_path", None)
            if edit_endpoint:
                merged["edit_endpoint_path"] = edit_endpoint
            else:
                merged.pop("edit_endpoint_path", None)
            if api_mode:
                merged["api_mode"] = api_mode
            else:
                merged.pop("api_mode", None)
        else:
            for key in (
                "endpoint_path",
                "edit_endpoint_path",
                "api_mode",
            ):
                # keep existing values if user unchecks temporarily? clearer to drop only when not image
                if key in merged and key not in self._original:
                    merged.pop(key, None)

        self.result_data = {
            "id": mid,
            "original_id": self._original_id,
            "config": merged,
        }

        self.accept()


class RouterConfigDialog(QtWidgets.QDialog):
    # task_name -> purpose filter used when building the model pool
    TASK_PURPOSE_FILTER = {
        "image_gen": ("image_gen", "image_edit"),
        "image_edit": ("image_edit", "image_gen"),
        "vision": ("vision",),
        "sensor_vision_talk": ("vision", "chat"),
        "translation": ("translation", "chat"),
        "summary": ("summary", "chat"),
        "web_search": ("web_search",),
        "tool_reasoning": ("tool_reasoning", "chat"),
        "default": ("chat",),
        "reply_polish": ("chat",),
        "gatekeeper": ("chat", "tool_reasoning"),
        "screen_classify": ("chat", "vision"),
    }

    def __init__(self, parent=None, task_name="", current_chain=None):
        super().__init__(parent)

        self.setWindowTitle(f"路由配置: {task_name}")

        self.resize(760, 520)

        self.task_name = str(task_name or "").strip()

        self.result_chain = list(current_chain or [])

        self.purpose_filter = self.TASK_PURPOSE_FILTER.get(self.task_name)

        self.all_models = self._collect_pool_models()

        self._init_ui()

    def _collect_pool_models(self):
        keys = sorted(list(MODELS.keys()))
        if not self.purpose_filter:
            return keys
        # 画图/图生图：候选池只显示已标记对应用途的模型；其它任务允许未标记模型。
        allow_untagged = self.task_name not in {
            "image_gen",
            "image_edit",
            "web_search",
        }
        filtered = list_models_by_purpose(
            MODELS, self.purpose_filter, allow_untagged=allow_untagged
        )
        # Always keep models already in the chain so existing configs remain editable.
        for model_id in self.result_chain:
            if model_id not in filtered and model_id in MODELS:
                filtered.append(model_id)
        return filtered

    def _init_ui(self):
        main = QtWidgets.QVBoxLayout(self)

        content = QtWidgets.QHBoxLayout()

        left = QtWidgets.QVBoxLayout()

        left.addWidget(QtWidgets.QLabel("可用模型"))

        self.list_pool = QtWidgets.QListWidget()

        self.list_pool.itemDoubleClicked.connect(self._add)

        left.addWidget(self.list_pool)

        content.addLayout(left, 1)

        mid = QtWidgets.QVBoxLayout()

        mid.addStretch()

        btn_add = QtWidgets.QPushButton("->")

        btn_add.clicked.connect(self._add)

        mid.addWidget(btn_add)

        btn_rm = QtWidgets.QPushButton("<-")

        btn_rm.clicked.connect(self._rm)

        mid.addWidget(btn_rm)

        mid.addStretch()

        content.addLayout(mid)

        right = QtWidgets.QVBoxLayout()

        right.addWidget(QtWidgets.QLabel("执行链（优先级：上 -> 下）"))

        self.list_chain = QtWidgets.QListWidget()

        self.list_chain.itemDoubleClicked.connect(self._rm)

        right.addWidget(self.list_chain)

        sort = QtWidgets.QHBoxLayout()

        btn_up = QtWidgets.QPushButton("上移")

        btn_up.clicked.connect(self._up)

        btn_down = QtWidgets.QPushButton("下移")

        btn_down.clicked.connect(self._down)

        sort.addWidget(btn_up)

        sort.addWidget(btn_down)

        right.addLayout(sort)

        content.addLayout(right, 1)

        main.addLayout(content, 1)

        btn_save = QtWidgets.QPushButton("保存")

        btn_save.clicked.connect(self.accept)

        main.addWidget(btn_save)

        self._refresh()

    def _model_label(self, model_id: str) -> str:
        cfg = MODELS.get(model_id) or {}
        name = str(cfg.get("model") or "???")
        purpose_text = format_purposes_label(get_model_purposes(cfg))
        if purpose_text and purpose_text != "未设置":
            return f"{model_id} ({name}) · {purpose_text}"
        return f"{model_id} ({name})"

    def _refresh(self):
        self.list_chain.clear()

        for m in self.result_chain:
            item = QtWidgets.QListWidgetItem(self._model_label(m))

            item.setData(QtCore.Qt.ItemDataRole.UserRole, m)

            self.list_chain.addItem(item)

        self.list_pool.clear()

        for m in self.all_models:
            if m in self.result_chain:
                continue

            item = QtWidgets.QListWidgetItem(self._model_label(m))

            item.setData(QtCore.Qt.ItemDataRole.UserRole, m)

            self.list_pool.addItem(item)

    def _add(self):
        item = self.list_pool.currentItem()

        if not item:
            return

        self.result_chain.append(item.data(QtCore.Qt.ItemDataRole.UserRole))

        self._refresh()

    def _rm(self):
        row = self.list_chain.currentRow()

        if row < 0:
            return

        self.result_chain.pop(row)

        self._refresh()

    def _up(self):
        row = self.list_chain.currentRow()

        if row <= 0:
            return

        self.result_chain[row], self.result_chain[row - 1] = (
            self.result_chain[row - 1],
            self.result_chain[row],
        )

        self._refresh()

        self.list_chain.setCurrentRow(row - 1)

    def _down(self):
        row = self.list_chain.currentRow()

        if row < 0 or row >= len(self.result_chain) - 1:
            return

        self.result_chain[row], self.result_chain[row + 1] = (
            self.result_chain[row + 1],
            self.result_chain[row],
        )

        self._refresh()

        self.list_chain.setCurrentRow(row + 1)


class MCPServerEditDialog(QtWidgets.QDialog):
    TRANSPORT_OPTIONS = [
        ("本地进程 (stdio)", "stdio"),
        ("远程 HTTP", "streamable_http"),
    ]

    def __init__(self, parent=None, server_data=None, default_transport: str = "stdio"):
        super().__init__(parent)

        self.result_data = None

        self._original = dict(server_data or {})

        transport = (
            str(self._original.get("transport") or default_transport or "stdio")
            .strip()
            .lower()
            .replace("-", "_")
        )

        if transport == "http":
            transport = "streamable_http"

        self._transport = transport or "stdio"

        self.setWindowTitle("编辑 MCP 服务器" if server_data else "新增 MCP 服务器")

        self.resize(620, 560)

        self._init_ui()

    @staticmethod
    def _normalize_list(value):
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]

        if isinstance(value, str):
            return [line.strip() for line in value.splitlines() if line.strip()]

        return []

    @staticmethod
    def _format_mapping(mapping: dict, pair_sep: str = "=") -> str:
        if not isinstance(mapping, dict):
            return ""

        rows = []

        for key, value in mapping.items():
            k = str(key).strip()

            if not k:
                continue

            rows.append(f"{k}{pair_sep}{str(value).strip()}")

        return "\n".join(rows)

    @staticmethod
    def _parse_mapping(text: str, default_sep: str = "=") -> dict:
        result = {}

        for raw_line in str(text or "").splitlines():
            line = raw_line.strip()

            if not line:
                continue

            if ":" in line and default_sep == ":":
                key, value = line.split(":", 1)

            elif default_sep in line:
                key, value = line.split(default_sep, 1)

            elif ":" in line:
                key, value = line.split(":", 1)

            else:
                raise ValueError(f"无法解析键值对：{line}")

            key = key.strip()

            if not key:
                raise ValueError(f"键不能为空：{line}")

            result[key] = value.strip()

        return result

    def _init_ui(self):
        layout = QtWidgets.QVBoxLayout(self)

        layout.setContentsMargins(18, 18, 18, 18)

        layout.setSpacing(12)

        form = QtWidgets.QFormLayout()

        form.setLabelAlignment(QtCore.Qt.AlignmentFlag.AlignRight)

        form.setFormAlignment(QtCore.Qt.AlignmentFlag.AlignTop)

        form.setHorizontalSpacing(12)

        form.setVerticalSpacing(10)

        self.inp_name = QtWidgets.QLineEdit(str(self._original.get("name", "")))

        self.inp_name.setPlaceholderText("例如：filesystem / remote_tools")

        form.addRow("名称", self.inp_name)

        self.combo_transport = QtWidgets.QComboBox()

        for text, value in self.TRANSPORT_OPTIONS:
            self.combo_transport.addItem(text, value)

        idx = self.combo_transport.findData(self._transport)

        self.combo_transport.setCurrentIndex(idx if idx >= 0 else 0)

        self.combo_transport.currentIndexChanged.connect(self._refresh_transport_ui)

        form.addRow("连接方式", self.combo_transport)

        self.chk_enabled = QtWidgets.QCheckBox("启用该服务器")

        self.chk_enabled.setChecked(bool(self._original.get("enabled", True)))

        form.addRow("状态", self.chk_enabled)

        layout.addLayout(form)

        self.tip_label = QtWidgets.QLabel("")

        self.tip_label.setWordWrap(True)

        self.tip_label.setStyleSheet("color:#6B7280;")

        layout.addWidget(self.tip_label)

        self.stdio_group = QtWidgets.QGroupBox("stdio 配置")

        stdio_form = QtWidgets.QFormLayout(self.stdio_group)

        stdio_form.setLabelAlignment(QtCore.Qt.AlignmentFlag.AlignRight)

        stdio_form.setHorizontalSpacing(12)

        stdio_form.setVerticalSpacing(10)

        self.inp_command = QtWidgets.QLineEdit(str(self._original.get("command", "")))

        self.inp_command.setPlaceholderText("例如：python / uvx / node")

        stdio_form.addRow("启动命令", self.inp_command)

        self.inp_args = QtWidgets.QPlainTextEdit(
            "\n".join(self._normalize_list(self._original.get("args", [])))
        )

        self.inp_args.setPlaceholderText("每行一个参数，例如：\n-m\nyour_mcp_server")

        self.inp_args.setFixedHeight(86)

        stdio_form.addRow("启动参数", self.inp_args)

        self.inp_cwd = QtWidgets.QLineEdit(str(self._original.get("cwd", "")))

        self.inp_cwd.setPlaceholderText("可选：工作目录")

        stdio_form.addRow("工作目录", self.inp_cwd)

        self.inp_env = QtWidgets.QPlainTextEdit(
            self._format_mapping(self._original.get("env", {}), pair_sep="=")
        )

        self.inp_env.setPlaceholderText(
            "每行一个环境变量，例如：\nAPI_KEY=xxx\nMODE=prod"
        )

        self.inp_env.setFixedHeight(86)

        stdio_form.addRow("环境变量", self.inp_env)

        layout.addWidget(self.stdio_group)

        self.http_group = QtWidgets.QGroupBox("HTTP 配置")

        http_form = QtWidgets.QFormLayout(self.http_group)

        http_form.setLabelAlignment(QtCore.Qt.AlignmentFlag.AlignRight)

        http_form.setHorizontalSpacing(12)

        http_form.setVerticalSpacing(10)

        self.inp_url = QtWidgets.QLineEdit(str(self._original.get("url", "")))

        self.inp_url.setPlaceholderText("例如：http://127.0.0.1:8000/mcp")

        http_form.addRow("URL", self.inp_url)

        self.inp_headers = QtWidgets.QPlainTextEdit(
            self._format_mapping(self._original.get("headers", {}), pair_sep=": ")
        )

        self.inp_headers.setPlaceholderText(
            "每行一个请求头，例如：\nAuthorization: Bearer xxx"
        )

        self.inp_headers.setFixedHeight(86)

        http_form.addRow("请求头", self.inp_headers)

        layout.addWidget(self.http_group)

        timeout_row = QtWidgets.QHBoxLayout()

        self.spin_timeout = QtWidgets.QDoubleSpinBox()

        self.spin_timeout.setRange(1.0, 600.0)

        self.spin_timeout.setDecimals(1)

        self.spin_timeout.setSingleStep(1.0)

        self.spin_timeout.setValue(
            float(self._original.get("timeout_sec", 20.0) or 20.0)
        )

        timeout_row.addWidget(self.spin_timeout)

        timeout_row.addWidget(QtWidgets.QLabel("秒"))

        timeout_row.addStretch()

        timeout_wrap = QtWidgets.QWidget()

        timeout_wrap.setLayout(timeout_row)

        sse_row = QtWidgets.QHBoxLayout()

        self.spin_sse_timeout = QtWidgets.QDoubleSpinBox()

        self.spin_sse_timeout.setRange(1.0, 3600.0)

        self.spin_sse_timeout.setDecimals(1)

        self.spin_sse_timeout.setSingleStep(10.0)

        self.spin_sse_timeout.setValue(
            float(self._original.get("sse_read_timeout_sec", 300.0) or 300.0)
        )

        sse_row.addWidget(self.spin_sse_timeout)

        sse_row.addWidget(QtWidgets.QLabel("秒"))

        sse_row.addStretch()

        sse_wrap = QtWidgets.QWidget()

        sse_wrap.setLayout(sse_row)

        tail_form = QtWidgets.QFormLayout()

        tail_form.setLabelAlignment(QtCore.Qt.AlignmentFlag.AlignRight)

        tail_form.setHorizontalSpacing(12)

        tail_form.setVerticalSpacing(10)

        tail_form.addRow("超时时间", timeout_wrap)

        tail_form.addRow("流式读取超时", sse_wrap)

        layout.addLayout(tail_form)

        footer = QtWidgets.QHBoxLayout()

        footer.addStretch()

        btn_cancel = QtWidgets.QPushButton("取消")

        btn_cancel.clicked.connect(self.reject)

        footer.addWidget(btn_cancel)

        btn_save = QtWidgets.QPushButton("保存")

        btn_save.setObjectName("primaryAction")

        btn_save.clicked.connect(self._on_save)

        footer.addWidget(btn_save)

        layout.addLayout(footer)

        self._refresh_transport_ui()

    def _refresh_transport_ui(self):
        transport = (
            str(self.combo_transport.currentData() or "stdio").strip().lower()
            or "stdio"
        )

        is_stdio = transport == "stdio"

        self.stdio_group.setVisible(is_stdio)

        self.http_group.setVisible(not is_stdio)

        self.spin_sse_timeout.setEnabled(not is_stdio)

        if is_stdio:
            self.tip_label.setText(
                "适合本机直接拉起 MCP 进程，例如 `python -m your_mcp_server`。启动参数一行一个，不用手写 JSON。"
            )

        else:
            self.tip_label.setText(
                "适合连接现成的远程 MCP HTTP 服务。请求头一行一个，格式如 `Authorization: Bearer xxx`。"
            )

    def _on_save(self):
        name = self.inp_name.text().strip()

        if not name:
            QtWidgets.QMessageBox.warning(self, "MCP 服务器", "服务器名称不能为空。")

            return

        transport = (
            str(self.combo_transport.currentData() or "stdio").strip().lower()
            or "stdio"
        )

        data = {
            "name": name,
            "transport": transport,
            "enabled": self.chk_enabled.isChecked(),
            "timeout_sec": float(self.spin_timeout.value()),
            "sse_read_timeout_sec": float(self.spin_sse_timeout.value()),
        }

        try:
            if transport == "stdio":
                command = self.inp_command.text().strip()

                if not command:
                    raise ValueError("stdio 模式必须填写启动命令。")

                data["command"] = command

                args = self._normalize_list(self.inp_args.toPlainText())

                if args:
                    data["args"] = args

                cwd = self.inp_cwd.text().strip()

                if cwd:
                    data["cwd"] = cwd

                env = self._parse_mapping(self.inp_env.toPlainText(), default_sep="=")

                if env:
                    data["env"] = env

            else:
                url = self.inp_url.text().strip()

                if not url:
                    raise ValueError("HTTP 模式必须填写 URL。")

                data["url"] = url

                headers = self._parse_mapping(
                    self.inp_headers.toPlainText(), default_sep=":"
                )

                if headers:
                    data["headers"] = headers

        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "MCP 服务器", str(exc))

            return

        self.result_data = data

        self.accept()


class QQUserProfileEditDialog(QtWidgets.QDialog):
    PERMISSION_OPTIONS = [
        ("默认", "default"),
        ("信任", "trusted"),
        ("受限", "restricted"),
        ("主人", "owner"),
    ]

    def __init__(self, parent=None, profile_data=None, preset_user_id: str = ""):
        super().__init__(parent)

        self.result_data = None

        self._original = dict(profile_data or {})

        self.setWindowTitle("编辑 QQ 档案" if profile_data else "新增 QQ 档案")

        self.resize(560, 520)

        self._init_ui(preset_user_id)

    def _init_ui(self, preset_user_id: str):
        layout = QtWidgets.QVBoxLayout(self)

        layout.setContentsMargins(18, 18, 18, 18)

        layout.setSpacing(12)

        form = QtWidgets.QFormLayout()

        form.setLabelAlignment(QtCore.Qt.AlignmentFlag.AlignRight)

        form.setFormAlignment(QtCore.Qt.AlignmentFlag.AlignTop)

        form.setHorizontalSpacing(12)

        form.setVerticalSpacing(10)

        user_id = str(self._original.get("user_id") or preset_user_id or "").strip()

        self.inp_user_id = QtWidgets.QLineEdit(user_id)

        self.inp_user_id.setPlaceholderText("例如 123456789")

        if self._original.get("user_id"):
            self.inp_user_id.setReadOnly(True)

            self.inp_user_id.setStyleSheet("background: #F3F4F6; color: #666;")

        form.addRow("QQ 号", self.inp_user_id)

        display_name = (
            str(
                self._original.get("remark_name")
                or self._original.get("nickname")
                or ""
            ).strip()
            or "尚未记录昵称或备注"
        )

        self.lbl_display_name = QtWidgets.QLabel(display_name)

        self.lbl_display_name.setWordWrap(True)

        form.addRow("当前显示名", self.lbl_display_name)

        relation_map = {
            "owner": "主人",
            "contact": "熟人联系人",
            "group_member": "群成员",
        }

        relation_text = relation_map.get(
            str(self._original.get("relationship_to_owner") or "").strip(), "未识别"
        )

        self.lbl_relation = QtWidgets.QLabel(relation_text)

        self.lbl_relation.setWordWrap(True)

        form.addRow("与主人的关系", self.lbl_relation)

        scope_map = {
            "owner_shared": "与主人共享",
            "private": "私聊独立",
            "group_shared": "群内共享",
        }

        scope_text = scope_map.get(
            str(self._original.get("memory_scope") or "").strip(), "未设置"
        )

        self.lbl_memory_scope = QtWidgets.QLabel(scope_text)

        self.lbl_memory_scope.setWordWrap(True)

        form.addRow("记忆范围", self.lbl_memory_scope)

        self.combo_permission = QtWidgets.QComboBox()

        for label, value in self.PERMISSION_OPTIONS:
            self.combo_permission.addItem(label, value)

        current_permission = (
            str(self._original.get("permission_level") or "default").strip()
            or "default"
        )

        idx = self.combo_permission.findData(current_permission)

        self.combo_permission.setCurrentIndex(idx if idx >= 0 else 0)

        form.addRow("权限等级", self.combo_permission)

        self.txt_identity_summary = QtWidgets.QPlainTextEdit(
            str(self._original.get("identity_summary") or "")
        )

        self.txt_identity_summary.setPlaceholderText(
            "例如：主人的朋友，偶尔聊游戏和桌面助手，回复可以自然一点。"
        )

        self.txt_identity_summary.setMinimumHeight(110)

        form.addRow("身份摘要", self.txt_identity_summary)

        self.txt_notes = QtWidgets.QPlainTextEdit(
            str(self._original.get("notes") or "")
        )

        self.txt_notes.setPlaceholderText(
            "补充细节，例如称呼偏好、禁忌话题、是否允许远程操作等。"
        )

        self.txt_notes.setMinimumHeight(120)

        form.addRow("备注", self.txt_notes)

        layout.addLayout(form)

        tip = QtWidgets.QLabel(
            "这里编辑的是“这个 QQ 用户是谁”的联系人画像，不会修改她本体的人设。后续收到同一 QQ 的新消息时，系统会沿用这里的资料。"
        )

        tip.setObjectName("launchDesc")

        tip.setWordWrap(True)

        layout.addWidget(tip)

        footer = QtWidgets.QHBoxLayout()

        footer.addStretch()

        btn_cancel = QtWidgets.QPushButton("取消")

        btn_cancel.clicked.connect(self.reject)

        footer.addWidget(btn_cancel)

        btn_save = QtWidgets.QPushButton("保存")

        btn_save.setObjectName("primaryAction")

        btn_save.clicked.connect(self._on_save)

        footer.addWidget(btn_save)

        layout.addLayout(footer)

    def _on_save(self):
        user_id = self.inp_user_id.text().strip()

        if not user_id:
            QtWidgets.QMessageBox.warning(self, "QQ 档案", "QQ 号不能为空。")

            return

        self.result_data = {
            "user_id": user_id,
            "permission_level": str(self.combo_permission.currentData() or "default"),
            "identity_summary": self.txt_identity_summary.toPlainText().strip(),
            "notes": self.txt_notes.toPlainText().strip(),
        }

        self.accept()


class SettingsDialog(QtWidgets.QDialog):
    SCREEN_MARGIN = 8

    def __init__(self, parent=None, main_app=None):
        super().__init__(parent)

        self.setWindowFlags(
            QtCore.Qt.WindowType.Window
            | QtCore.Qt.WindowType.WindowMinMaxButtonsHint
            | QtCore.Qt.WindowType.WindowCloseButtonHint
        )
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_QuitOnClose, False)

        self.main_app = main_app

        self.setObjectName("SettingsDialog")
        self.setWindowTitle("设置")

        if main_app is not None and hasattr(main_app, "_icon"):
            self.setWindowIcon(main_app._icon)

        # 动态计算初始大小，防止超出屏幕
        screen = QtWidgets.QApplication.primaryScreen()
        if screen:
            avail = screen.availableGeometry()
            width = min(1040, int(avail.width() * 0.9))
            height = min(620, int(avail.height() * 0.82))
            self.resize(width, height)
        else:
            self.resize(1040, 620)

        self.setMinimumSize(600, 320)

        self.setSizeGripEnabled(True)

        self.setStyleSheet(get_settings_styles())

        self._tab_meta = list(SETTINGS_PAGES)
        self._page_stack_index = {}
        self._page_builders = {}
        self._built_pages: set[str] = set()
        self._current_page_id = 'start'
        self._start_page = None
        self._plugin_page = None
        self._provider_page = None
        self._llm_page = None
        self._collapsed_groups: set[str] = set()

        main = QtWidgets.QHBoxLayout(self)

        main.setContentsMargins(16, 16, 16, 16)

        main.setSpacing(16)

        nav_card = QtWidgets.QFrame()

        nav_card.setObjectName("settingsNavCard")

        nav_layout = QtWidgets.QVBoxLayout(nav_card)

        nav_layout.setContentsMargins(14, 14, 14, 14)

        nav_layout.setSpacing(10)

        nav_title = QtWidgets.QLabel("✨ 偏好设置")

        nav_title.setObjectName("settingsNavTitle")

        nav_layout.addWidget(nav_title)

        nav_hint = QtWidgets.QLabel(
            "按功能分类管理，随时切换调整。"
        )
        nav_hint.setObjectName("settingsNavHint")
        nav_hint.setWordWrap(True)
        nav_layout.addWidget(nav_hint)

        self.nav_list = QtWidgets.QListWidget()
        self.nav_list.setObjectName("settingsNav")
        self.nav_list.setFixedWidth(212)
        self.nav_list.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.nav_list.currentRowChanged.connect(self._on_tab_changed)
        self.nav_list.itemClicked.connect(self._on_nav_item_clicked)
        nav_layout.addWidget(self.nav_list, 1)

        self.dev_check = QtWidgets.QCheckBox("显示开发者选项")
        self.dev_check.setObjectName("settingsNavHint")
        self.dev_check.setChecked(is_developer_mode())
        self.dev_check.toggled.connect(self._set_developer_mode)
        nav_layout.addWidget(self.dev_check)

        main.addWidget(nav_card)

        content_card = QtWidgets.QFrame()
        content_card.setObjectName("settingsContentCard")
        content_layout = QtWidgets.QVBoxLayout(content_card)
        content_layout.setContentsMargins(18, 18, 18, 18)
        content_layout.setSpacing(14)

        header_card = QtWidgets.QFrame()
        header_card.setObjectName("settingsHeaderCard")
        header_layout = QtWidgets.QVBoxLayout(header_card)
        header_layout.setContentsMargins(14, 12, 14, 12)
        header_layout.setSpacing(4)
        self.page_title = QtWidgets.QLabel("")
        self.page_title.setObjectName("settingsPageTitle")
        header_layout.addWidget(self.page_title)
        self.page_desc = QtWidgets.QLabel("")
        self.page_desc.setObjectName("settingsPageDesc")
        self.page_desc.setWordWrap(True)
        header_layout.addWidget(self.page_desc)
        content_layout.addWidget(header_card)

        self.stack = QtWidgets.QStackedWidget()
        content_layout.addWidget(self.stack, 1)
        main.addWidget(content_card, 1)

        self._init_all_settings_pages()
        self._rebuild_nav(select_id="start")

    def ensure_on_screen(self):

        screen = (
            QtGui.QGuiApplication.screenAt(QtGui.QCursor.pos())
            or QtWidgets.QApplication.primaryScreen()
        )
        if not screen:
            return
        frame = self.frameGeometry()
        geo = self.geometry()
        avail = screen.availableGeometry().adjusted(
            self.SCREEN_MARGIN,
            self.SCREEN_MARGIN,
            -self.SCREEN_MARGIN,
            -self.SCREEN_MARGIN,
        )
        max_x = max(avail.left(), avail.right() - frame.width())
        max_y = max(avail.top(), avail.bottom() - frame.height())
        target_frame_x = min(max(frame.x(), avail.left()), max_x)
        target_frame_y = min(max(frame.y(), avail.top()), max_y)
        dx = target_frame_x - frame.x()
        dy = target_frame_y - frame.y()
        if dx or dy:
            self.move(geo.x() + dx, geo.y() + dy)

    def showEvent(self, event):
        super().showEvent(event)
        self.ensure_on_screen()
        # Defer disk/network work so the window paints first.
        QtCore.QTimer.singleShot(0, self._on_shown)
        QtCore.QTimer.singleShot(80, self.ensure_on_screen)

    def _on_shown(self):
        try:
            self._reload_catalog_from_disk()
        except Exception:
            pass
        start_page = getattr(self, "_start_page", None)
        if start_page is not None and hasattr(start_page, "refresh"):
            try:
                start_page.refresh()
            except Exception:
                pass
        self.ensure_on_screen()

    def _reload_catalog_from_disk(self) -> None:
        from modules.gui.settings_pages.catalog_io import reload_model_catalog

        reload_model_catalog()
        if getattr(self, "_provider_page", None) is not None:
            try:
                self._provider_page.reload_from_disk()
            except Exception:
                pass
        if getattr(self, "_llm_page", None) is not None:
            try:
                self._llm_page.reload_from_disk()
            except Exception:
                pass

    def _page_initters(self):
        return {
            "start": self._init_start_page,
            "character": self._init_costume_page,
            "llm": self._init_llm_page,
            "asr": self._init_asr_page,
            "color": self._init_color_page,
            "memory": self._init_memory_page,
            "diary": self._init_diary_page,
            "knowledge": self._init_knowledge_page,
            "expression": self._init_expression_library_page,
            "meme": self._init_meme_page,
            "sedentary": self._init_sedentary_page,
            "sensor_prompts": self._init_sensor_prompts_page,
            "screen_apps": self._init_screen_app_rules_page,
            "external_services": self._init_external_services_page,
            "qq": self._init_gateway_page,
            "plugins": self._init_plugin_page,
            "mcp": self._init_mcp_page,
            "providers": self._init_provider_page,
            "info_sources": self._init_info_sources_page,
            "status_screen": self._init_status_screen_page,
            "runtime_health": self._init_runtime_health_page,
            "dependency": self._init_dependency_page,
        }

    def _init_all_settings_pages(self):
        initters = self._page_initters()
        self._page_stack_index = {}
        self._page_builders = {}
        self._built_pages = set()
        for spec in SETTINGS_PAGES:
            page_id = spec["id"]
            fn = initters.get(page_id)
            label = str(spec.get("init_label") or spec.get("title") or page_id)
            placeholder = QtWidgets.QWidget()
            self._page_stack_index[page_id] = self.stack.addWidget(placeholder)
            self._page_builders[page_id] = (fn, label)
        self._ensure_page("start")

    def _ensure_page(self, page_id: str) -> None:
        page_id = str(page_id or "").strip()
        if not page_id or page_id in self._built_pages:
            return
        builder = self._page_builders.get(page_id)
        if builder is None:
            self._built_pages.add(page_id)
            return
        fn, label = builder
        idx = self._page_stack_index.get(page_id)
        if idx is None:
            self._built_pages.add(page_id)
            return
        if fn is None:
            self._built_pages.add(page_id)
            return
        placeholder = self.stack.widget(idx)
        count_before = self.stack.count()
        self._safe_init_page(fn, label)
        if self.stack.count() == count_before + 1:
            built = self.stack.widget(count_before)
            self.stack.removeWidget(built)
            self.stack.insertWidget(idx, built)
            if placeholder is not None:
                self.stack.removeWidget(placeholder)
                placeholder.deleteLater()
        self._built_pages.add(page_id)

    def _init_start_page(self):
        from modules.gui.settings_pages.start_page import StartSettingsPage

        widget = StartSettingsPage(
            main_app=self.main_app,
            on_open_page=self.open_page,
        )
        self._start_page = widget
        self.stack.addWidget(widget)

    def _set_developer_mode(self, enabled: bool):
        set_developer_mode(bool(enabled))
        if hasattr(self, "dev_check"):
            self.dev_check.blockSignals(True)
            self.dev_check.setChecked(bool(enabled))
            self.dev_check.blockSignals(False)
        current = self._current_page_id
        spec = SETTINGS_PAGE_BY_ID.get(current) or {}
        if spec.get("developer") and not enabled:
            current = "start"
        self._rebuild_nav(select_id=current)
        plugin_page = getattr(self, "_plugin_page", None)
        if plugin_page is not None and hasattr(plugin_page, "set_developer_visible"):
            plugin_page.set_developer_visible(bool(enabled))

    def _nav_item_data(self, row: int):
        item = self.nav_list.item(row) if hasattr(self, "nav_list") else None
        if item is None:
            return None
        data = item.data(QtCore.Qt.ItemDataRole.UserRole)
        return data if isinstance(data, dict) else None

    NAV_ICONS = {
        "start": "🚀",
        "character": "🎀",
        "llm": "💭",
        "asr": "🎤",
        "color": "🎨",
        "memory": "🧠",
        "diary": "📖",
        "knowledge": "📜",
        "expression": "💬",
        "meme": "🐱",
        "sedentary": "🍵",
        "sensor_prompts": "👀",
        "screen_apps": "🖥️",
        "external_services": "🧰",
        "qq": "🐧",
        "plugins": "🧩",
        "mcp": "🔌",
        "providers": "⚙️",
        "info_sources": "🌐",
        "status_screen": "📟",
        "runtime_health": "💓",
        "dependency": "🩺",
    }

    def _rebuild_nav(self, select_id: str | None = None):
        select_id = str(select_id or self._current_page_id or "start")
        if select_id not in SETTINGS_PAGE_BY_ID:
            select_id = "start"
        spec = SETTINGS_PAGE_BY_ID.get(select_id) or {}
        select_group = str(spec.get("group") or "")
        if select_group and select_group in self._collapsed_groups:
            self._collapsed_groups.remove(select_group)
        developer = is_developer_mode()
        if spec.get("developer") and not developer:
            select_id = "start"
        self.nav_list.blockSignals(True)
        self.nav_list.clear()
        last_group = None
        target_row = None
        for page in SETTINGS_PAGES:
            if page.get("developer") and not developer:
                continue
            group_id = str(page.get("group") or "")
            if group_id != last_group:
                header_label = SETTINGS_GROUP_LABELS.get(group_id) or ""
                if header_label:
                    is_collapsed = group_id in self._collapsed_groups
                    arrow = "▸" if is_collapsed else "▾"
                    header = QtWidgets.QListWidgetItem(f"{arrow}  {header_label}")
                    header.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled)
                    header.setData(
                        QtCore.Qt.ItemDataRole.UserRole,
                        {"kind": "group", "group": group_id},
                    )
                    header.setToolTip("点击折叠 / 展开此类设置")
                    font = header.font()
                    font.setPointSize(max(9, font.pointSize() - 1))
                    font.setBold(True)
                    header.setFont(font)
                    header.setForeground(QtGui.QBrush(QtGui.QColor("#8E9AA8")))
                    header.setSizeHint(QtCore.QSize(0, 30))
                    self.nav_list.addItem(header)
                last_group = group_id
            page_id = page["id"]
            icon = self.NAV_ICONS.get(page_id, "✨")
            nav_text = str(page.get("nav") or page_id)
            item = QtWidgets.QListWidgetItem(f"{icon}  {nav_text}")
            item.setToolTip(str(page.get("desc") or ""))
            item.setData(
                QtCore.Qt.ItemDataRole.UserRole,
                {"kind": "page", "id": page_id, "group": group_id},
            )
            item.setSizeHint(QtCore.QSize(0, 36))
            is_hidden = group_id in self._collapsed_groups
            item.setHidden(is_hidden)
            self.nav_list.addItem(item)
            if page_id == select_id:
                target_row = self.nav_list.count() - 1
        self.nav_list.blockSignals(False)
        if target_row is None:
            for row in range(self.nav_list.count()):
                data = self._nav_item_data(row)
                if data and data.get("kind") == "page":
                    target_row = row
                    select_id = str(data.get("id") or "start")
                    break
        if target_row is not None:
            self.nav_list.blockSignals(True)
            self.nav_list.setCurrentRow(target_row)
            self.nav_list.blockSignals(False)
        self._show_page(select_id)

    def _on_nav_item_clicked(self, item: QtWidgets.QListWidgetItem):
        data = item.data(QtCore.Qt.ItemDataRole.UserRole)
        if isinstance(data, dict) and data.get("kind") == "group":
            group_id = str(data.get("group") or "")
            if group_id:
                self._toggle_group_collapse(group_id)
        elif isinstance(data, dict) and data.get("kind") == "page":
            page_id = str(data.get("id") or "")
            if page_id and page_id != self._current_page_id:
                self._show_page(page_id)

    def _toggle_group_collapse(self, group_id: str):
        if group_id in self._collapsed_groups:
            self._collapsed_groups.remove(group_id)
        else:
            self._collapsed_groups.add(group_id)
        self._apply_group_collapse_state()

    def _apply_group_collapse_state(self):
        sb = self.nav_list.verticalScrollBar()
        saved_scroll = sb.value() if sb is not None else 0
        self.nav_list.blockSignals(True)
        current_group = None
        for row in range(self.nav_list.count()):
            item = self.nav_list.item(row)
            data = item.data(QtCore.Qt.ItemDataRole.UserRole) if item else None
            if not isinstance(data, dict):
                continue
            kind = data.get("kind")
            if kind == "group":
                current_group = str(data.get("group") or "")
                header_label = SETTINGS_GROUP_LABELS.get(current_group) or current_group
                is_collapsed = current_group in self._collapsed_groups
                arrow = "▸" if is_collapsed else "▾"
                item.setText(f"{arrow}  {header_label}")
            elif kind == "page":
                page_group = str(data.get("group") or current_group or "")
                is_collapsed = page_group in self._collapsed_groups
                item.setHidden(is_collapsed)

        # Restore/keep current selection on active page if visible without auto-scrolling to top
        active_row = None
        for row in range(self.nav_list.count()):
            item = self.nav_list.item(row)
            data = item.data(QtCore.Qt.ItemDataRole.UserRole) if item else None
            if isinstance(data, dict) and data.get("kind") == "page" and data.get("id") == self._current_page_id:
                if not item.isHidden():
                    active_row = row
                break
        if active_row is not None:
            index = self.nav_list.model().index(active_row, 0)
            self.nav_list.selectionModel().setCurrentIndex(
                index,
                QtCore.QItemSelectionModel.SelectionFlag.ClearAndSelect,
            )
        else:
            self.nav_list.setCurrentItem(None)
            self.nav_list.clearSelection()

        self.nav_list.blockSignals(False)
        if sb is not None:
            sb.setValue(min(saved_scroll, sb.maximum()))

    def _select_nav_row_for_page(self, page_id: str, auto_expand: bool = False) -> bool:
        for row in range(self.nav_list.count()):
            data = self._nav_item_data(row)
            if data and data.get("kind") == "page" and data.get("id") == page_id:
                page_group = str(data.get("group") or "")
                if auto_expand and page_group and page_group in self._collapsed_groups:
                    self._collapsed_groups.remove(page_group)
                    self._apply_group_collapse_state()
                self.nav_list.blockSignals(True)
                self.nav_list.setCurrentRow(row)
                self.nav_list.blockSignals(False)
                return True
        return False

    def _show_page(self, page_id: str):
        page_id = str(page_id or "").strip() or "start"
        self._ensure_page(page_id)
        idx = self._page_stack_index.get(page_id)
        if idx is None:
            return
        self._current_page_id = page_id
        if 0 <= idx < self.stack.count():
            self.stack.setCurrentIndex(idx)
        spec = SETTINGS_PAGE_BY_ID.get(page_id) or {}
        self.page_title.setText(str(spec.get("title") or ""))
        self.page_desc.setText(str(spec.get("desc") or ""))
        title = str(spec.get("title") or "")
        if any(token in title for token in ("提供商", "聊天模型", "模型", "路由")):
            self._reload_catalog_from_disk()
        if page_id == "start":
            start_page = getattr(self, "_start_page", None)
            if start_page is not None and hasattr(start_page, "refresh"):
                try:
                    start_page.refresh()
                except Exception:
                    pass

    def _on_tab_changed(self, row):
        data = self._nav_item_data(row)
        if not data or data.get("kind") != "page":
            return
        page_id = str(data.get("id") or "start")
        if page_id != self._current_page_id:
            self._show_page(page_id)

    def open_page(self, page_id):
        if isinstance(page_id, int):
            data = self._nav_item_data(page_id)
            if data and data.get("kind") == "page":
                page_id = data.get("id")
            else:
                return
        page_id = str(page_id or "").strip()
        spec = SETTINGS_PAGE_BY_ID.get(page_id)
        if spec is None:
            return
        self.show()
        self.raise_()
        self.activateWindow()
        self.ensure_on_screen()
        QtCore.QTimer.singleShot(0, self.ensure_on_screen)
        QtCore.QTimer.singleShot(80, self.ensure_on_screen)
        QtCore.QTimer.singleShot(240, self.ensure_on_screen)
        if spec.get("developer") and not is_developer_mode():
            self._show_page(page_id)
            if not self._select_nav_row_for_page(page_id, auto_expand=True):
                self.nav_list.clearSelection()
            return
        self._rebuild_nav(select_id=page_id)

    def _refresh_page_header(self, row: int):
        data = self._nav_item_data(row)
        if data and data.get("kind") == "page":
            self._show_page(str(data.get("id") or "start"))
            return
        spec = SETTINGS_PAGE_BY_ID.get(self._current_page_id) or {}
        self.page_title.setText(str(spec.get("title") or ""))
        self.page_desc.setText(str(spec.get("desc") or ""))

    def _create_launch_pad_page(
        self,
        title: str,
        desc: str,
        button_text: str,
        callback,
        *,
        enabled: bool = True,
        disabled_tip: str = "",
    ):
        page = QtWidgets.QWidget()

        layout = QtWidgets.QVBoxLayout(page)

        layout.addStretch()

        card = QtWidgets.QFrame()

        card.setObjectName("launchCard")

        card_layout = QtWidgets.QVBoxLayout(card)

        card_layout.setContentsMargins(24, 24, 24, 24)

        card_layout.setSpacing(10)

        title_label = QtWidgets.QLabel(title)

        title_label.setObjectName("launchTitle")

        card_layout.addWidget(title_label)

        desc_label = QtWidgets.QLabel(desc)

        desc_label.setObjectName("launchDesc")

        desc_label.setWordWrap(True)

        card_layout.addWidget(desc_label)

        btn = QtWidgets.QPushButton(button_text)

        btn.setObjectName("primaryAction")

        btn.setFixedWidth(220)

        btn.setEnabled(enabled)

        if enabled and callback:
            btn.clicked.connect(callback)

        elif disabled_tip:
            btn.setToolTip(disabled_tip)

        card_layout.addSpacing(8)

        card_layout.addWidget(btn, alignment=QtCore.Qt.AlignmentFlag.AlignLeft)

        layout.addWidget(card)

        layout.addStretch()

        self.stack.addWidget(page)

    def _safe_init_page(self, fn, label: str):
        try:
            fn()

        except Exception as e:
            page = QtWidgets.QWidget()

            layout = QtWidgets.QVBoxLayout(page)

            msg = QtWidgets.QLabel(f"页面加载失败: {label}\n{e}")

            msg.setStyleSheet("color:#B91C1C;")

            msg.setWordWrap(True)

            layout.addWidget(msg)

            self.stack.addWidget(page)

    # ---------- Sedentary ----------

    def _init_sensor_prompts_page(self):
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        widget = SensorPromptsSettingsPage(main_app=self.main_app)
        apply_embedded_mode(widget)
        layout.addWidget(widget, 1)
        self.stack.addWidget(page)

    def _init_sedentary_page(self):
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        widget = SedentarySettingsPage(main_app=self.main_app)
        apply_embedded_mode(widget)
        layout.addWidget(self._wrap_in_scroll_area(widget), 1)
        self.stack.addWidget(page)

    def _init_asr_page(self):
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        widget = AsrSettingsPage(main_app=self.main_app)
        apply_embedded_mode(widget)
        layout.addWidget(self._wrap_in_scroll_area(widget), 1)
        self.stack.addWidget(page)

    # ---------- Info Sources ----------

    def _init_info_sources_page(self):
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        widget = InfoSourcesSettingsPage()
        apply_embedded_mode(widget)
        layout.addWidget(self._wrap_in_scroll_area(widget), 1)
        self.stack.addWidget(page)

    def _init_provider_page(self):
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        widget = ProviderSettingsPage(on_changed=self._refresh_llm_table)
        apply_embedded_mode(widget)
        layout.addWidget(self._wrap_in_scroll_area(widget), 1)
        self._provider_page = widget
        self.stack.addWidget(page)

    def _init_llm_page(self):
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        widget = LlmSettingsPage(
            on_open_providers=lambda: self.open_page("providers"),
            plugin_manager=(
                getattr(self.main_app, "plugin_manager", None)
                if self.main_app
                else None
            ),
        )
        apply_embedded_mode(widget)
        layout.addWidget(widget, 1)
        self._llm_page = widget
        self.stack.addWidget(page)

    def _refresh_prov_table(self):
        if getattr(self, "_provider_page", None) is not None:
            self._provider_page.refresh()

    def _refresh_llm_table(self):
        if getattr(self, "_llm_page", None) is not None:
            self._llm_page.refresh_models()

    def _refresh_router(self):
        if getattr(self, "_llm_page", None) is not None:
            self._llm_page.refresh_router()

    def _save_to_json(self):
        from modules.gui.settings_pages.catalog_io import save_model_catalog

        save_model_catalog(parent=self)

    def _init_dependency_page(self):
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        widget = DependencySettingsPage()
        apply_embedded_mode(widget)
        layout.addWidget(self._wrap_in_scroll_area(widget), 1)
        self.stack.addWidget(page)

    def _init_color_page(self):
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        widget = ColorSettingsPage(main_app=self.main_app)
        apply_embedded_mode(widget)
        layout.addWidget(self._wrap_in_scroll_area(widget), 1)
        self.stack.addWidget(page)

    # ---------- MCP / QQ ----------

    def _load_gateway_settings(self):
        runtime = load_runtime_settings()

        def _parse_id_list(value):
            if isinstance(value, str):
                return [
                    item.strip()
                    for item in re.split(r"[,，\n\s]+", value)
                    if item.strip()
                ]

            if isinstance(value, list):
                return [str(item).strip() for item in value if str(item).strip()]

            return []

        owner_ids_raw = runtime.get("napcat_owner_user_ids", [])

        owner_ids = _parse_id_list(owner_ids_raw)

        voice_probability_raw = runtime.get(
            "napcat_voice_reply_probability", NAPCAT_VOICE_REPLY_PROBABILITY
        )

        try:
            voice_probability = max(0, min(100, int(voice_probability_raw)))

        except Exception:
            voice_probability = int(NAPCAT_VOICE_REPLY_PROBABILITY)

        return {
            "napcat_enabled": bool(runtime.get("napcat_enabled", NAPCAT_ENABLED)),
            "napcat_webhook_host": str(
                runtime.get("napcat_webhook_host", NAPCAT_WEBHOOK_HOST)
                or NAPCAT_WEBHOOK_HOST
            ),
            "napcat_webhook_port": int(
                runtime.get("napcat_webhook_port", NAPCAT_WEBHOOK_PORT)
                or NAPCAT_WEBHOOK_PORT
            ),
            "napcat_webhook_path": str(
                runtime.get("napcat_webhook_path", NAPCAT_WEBHOOK_PATH)
                or NAPCAT_WEBHOOK_PATH
            ),
            "napcat_access_token": str(
                runtime.get("napcat_access_token", NAPCAT_ACCESS_TOKEN) or ""
            ),
            "napcat_api_base": str(
                runtime.get("napcat_api_base", NAPCAT_API_BASE) or NAPCAT_API_BASE
            ),
            "napcat_api_token": str(
                runtime.get("napcat_api_token", NAPCAT_API_TOKEN) or ""
            ),
            "napcat_reply_enabled": bool(
                runtime.get("napcat_reply_enabled", NAPCAT_REPLY_ENABLED)
            ),
            "napcat_allow_private": bool(
                runtime.get("napcat_allow_private", NAPCAT_ALLOW_PRIVATE)
            ),
            "napcat_allow_group": bool(
                runtime.get("napcat_allow_group", NAPCAT_ALLOW_GROUP)
            ),
            "napcat_group_require_at": bool(
                runtime.get("napcat_group_require_at", NAPCAT_GROUP_REQUIRE_AT)
            ),
            "napcat_owner_user_ids": owner_ids,
            "napcat_owner_label": str(
                runtime.get("napcat_owner_label", "主人") or "主人"
            ),
            "napcat_image_vision_enabled": bool(
                runtime.get("napcat_image_vision_enabled", True)
            ),
            "napcat_image_prompt": str(
                runtime.get(
                    "napcat_image_prompt",
                    "请客观详细描述这张QQ图片的内容，并提取其中可用于回复的关键信息。",
                )
                or "请客观详细描述这张QQ图片的内容，并提取其中可用于回复的关键信息。"
            ),
            "napcat_voice_reply_enabled": bool(
                runtime.get("napcat_voice_reply_enabled", NAPCAT_VOICE_REPLY_ENABLED)
            ),
            "napcat_voice_reply_probability": voice_probability,
            "napcat_filter_mode": str(runtime.get("napcat_filter_mode", "off") or "off")
            .strip()
            .lower(),
            "napcat_user_whitelist": _parse_id_list(
                runtime.get("napcat_user_whitelist", [])
            ),
            "napcat_user_blacklist": _parse_id_list(
                runtime.get("napcat_user_blacklist", [])
            ),
            "napcat_group_whitelist": _parse_id_list(
                runtime.get("napcat_group_whitelist", [])
            ),
            "napcat_group_blacklist": _parse_id_list(
                runtime.get("napcat_group_blacklist", [])
            ),
            "remote_chat_ui_append": bool(
                runtime.get("remote_chat_ui_append", REMOTE_CHAT_UI_APPEND)
            ),
        }

    def _format_owner_ids(self, values) -> str:
        if isinstance(values, str):
            return values

        if isinstance(values, list):
            return ", ".join(str(item).strip() for item in values if str(item).strip())

        return ""

    def _get_memory_store(self):
        if self.main_app is None:
            return None

        store = getattr(self.main_app, "memory_store", None)

        if store is not None:
            return store

        brain = getattr(self.main_app, "brain", None)

        return getattr(brain, "sqlite_store", None)

    def _selected_gateway_filter_mode(self) -> str:
        if not hasattr(self, "gateway_filter_mode"):
            return "off"

        return (
            str(self.gateway_filter_mode.currentData() or "off").strip().lower()
            or "off"
        )

    def _format_mcp_servers_json(self, servers) -> str:
        value = servers if isinstance(servers, list) else []

        return json.dumps(value, ensure_ascii=False, indent=2)

    def _parse_mcp_servers_json(self):
        if hasattr(self, "_gateway_mcp_server_configs"):
            return self._normalize_mcp_server_configs(self._gateway_mcp_server_configs)

        raw = (
            self.gateway_mcp_servers.toPlainText().strip()
            if hasattr(self, "gateway_mcp_servers")
            else ""
        )

        if not raw:
            return []

        value = json.loads(raw)

        if not isinstance(value, list):
            raise ValueError("MCP 服务器配置必须是 JSON 数组。")

        return value

    def _normalize_mcp_server_configs(self, servers) -> list:
        def _safe_float(value, default):
            try:
                return float(value)

            except Exception:
                return float(default)

        normalized = []

        raw_items = servers if isinstance(servers, list) else []

        for index, item in enumerate(raw_items):
            if not isinstance(item, dict):
                continue

            transport = (
                str(item.get("transport") or "stdio").strip().lower().replace("-", "_")
            )

            if transport == "http":
                transport = "streamable_http"

            if transport not in {"stdio", "streamable_http"}:
                transport = "stdio"

            payload = {
                "name": str(item.get("name") or f"server_{index + 1}").strip()
                or f"server_{index + 1}",
                "transport": transport,
                "enabled": bool(item.get("enabled", True)),
                "timeout_sec": _safe_float(item.get("timeout_sec") or 20.0, 20.0),
                "sse_read_timeout_sec": _safe_float(
                    item.get("sse_read_timeout_sec") or 300.0, 300.0
                ),
            }

            if transport == "stdio":
                payload["command"] = str(item.get("command") or "").strip()

                args_value = item.get("args") or []

                if isinstance(args_value, list):
                    args = [str(x).strip() for x in args_value if str(x).strip()]

                elif isinstance(args_value, str):
                    args = [
                        line.strip() for line in args_value.splitlines() if line.strip()
                    ]

                else:
                    args = []

                if args:
                    payload["args"] = args

                cwd = str(item.get("cwd") or "").strip()

                if cwd:
                    payload["cwd"] = cwd

                env = item.get("env") or {}

                if isinstance(env, dict) and env:
                    payload["env"] = {
                        str(k).strip(): str(v).strip()
                        for k, v in env.items()
                        if str(k).strip()
                    }

            else:
                url = str(item.get("url") or "").strip()

                if url:
                    payload["url"] = url

                headers = item.get("headers") or {}

                if isinstance(headers, dict) and headers:
                    payload["headers"] = {
                        str(k).strip(): str(v).strip()
                        for k, v in headers.items()
                        if str(k).strip()
                    }

            normalized.append(payload)

        return normalized

    def _set_gateway_mcp_server_configs(self, servers):
        self._gateway_mcp_server_configs = self._normalize_mcp_server_configs(servers)

        self._refresh_gateway_mcp_table()

        self._refresh_gateway_preview()

    def _gateway_mcp_entry_text(self, item: dict) -> str:
        if not isinstance(item, dict):
            return "-"

        transport = str(item.get("transport") or "stdio").strip().lower()

        if transport == "stdio":
            command = str(item.get("command") or "").strip()

            args = item.get("args") or []

            if isinstance(args, list) and args:
                return f"{command} {' '.join(str(x) for x in args)}".strip()

            return command or "-"

        return str(item.get("url") or "").strip() or "-"

    def _refresh_gateway_mcp_table(self):
        if not hasattr(self, "gateway_mcp_table"):
            return

        servers = self._parse_mcp_servers_json()

        self.gateway_mcp_table.setRowCount(len(servers))

        for row, item in enumerate(servers):
            enabled_text = "启用" if bool(item.get("enabled", True)) else "停用"

            transport_text = (
                "本地进程"
                if str(item.get("transport") or "stdio") == "stdio"
                else "远程 HTTP"
            )

            timeout_text = f"{float(item.get('timeout_sec') or 20.0):.1f}s"

            self.gateway_mcp_table.setItem(
                row, 0, QtWidgets.QTableWidgetItem(enabled_text)
            )

            self.gateway_mcp_table.setItem(
                row, 1, QtWidgets.QTableWidgetItem(str(item.get("name") or ""))
            )

            self.gateway_mcp_table.setItem(
                row, 2, QtWidgets.QTableWidgetItem(transport_text)
            )

            self.gateway_mcp_table.setItem(
                row, 3, QtWidgets.QTableWidgetItem(self._gateway_mcp_entry_text(item))
            )

            self.gateway_mcp_table.setItem(
                row, 4, QtWidgets.QTableWidgetItem(timeout_text)
            )

            action_widget = QtWidgets.QWidget()

            action_layout = QtWidgets.QHBoxLayout(action_widget)

            action_layout.setContentsMargins(0, 0, 0, 0)

            action_layout.setSpacing(6)

            btn_edit = QtWidgets.QPushButton("编辑")

            btn_edit.setObjectName("tableActionBtn")

            btn_edit.clicked.connect(
                lambda checked=False, idx=row: self._edit_gateway_mcp_server(idx)
            )

            action_layout.addWidget(btn_edit)

            btn_remove = QtWidgets.QPushButton("删除")

            btn_remove.setObjectName("tableDangerBtn")

            btn_remove.clicked.connect(
                lambda checked=False, idx=row: self._remove_gateway_mcp_server(idx)
            )

            action_layout.addWidget(btn_remove)

            self.gateway_mcp_table.setCellWidget(row, 5, action_widget)

        if servers and self.gateway_mcp_table.currentRow() < 0:
            self.gateway_mcp_table.selectRow(0)

        self.gateway_mcp_table.resizeRowsToContents()

        self._update_gateway_mcp_actions()

    def _selected_gateway_mcp_row(self) -> int:
        if not hasattr(self, "gateway_mcp_table"):
            return -1

        return self.gateway_mcp_table.currentRow()

    def _update_gateway_mcp_actions(self):
        row = self._selected_gateway_mcp_row()

        total = len(self._parse_mcp_servers_json())

        has_row = 0 <= row < total

        if hasattr(self, "gateway_mcp_edit_btn"):
            self.gateway_mcp_edit_btn.setEnabled(has_row)

        if hasattr(self, "gateway_mcp_remove_btn"):
            self.gateway_mcp_remove_btn.setEnabled(has_row)

        if hasattr(self, "gateway_mcp_up_btn"):
            self.gateway_mcp_up_btn.setEnabled(has_row and row > 0)

        if hasattr(self, "gateway_mcp_down_btn"):
            self.gateway_mcp_down_btn.setEnabled(has_row and row < total - 1)

    def _add_gateway_mcp_server(self, transport: str = "stdio"):
        dlg = MCPServerEditDialog(self, default_transport=transport)

        if dlg.exec() != QtWidgets.QDialog.DialogCode.Accepted or not dlg.result_data:
            return

        servers = self._parse_mcp_servers_json()

        servers.append(dlg.result_data)

        self._set_gateway_mcp_server_configs(servers)

        self.gateway_mcp_table.selectRow(len(servers) - 1)

    def _edit_gateway_mcp_server(self, row: int = None):
        servers = self._parse_mcp_servers_json()

        target_row = self._selected_gateway_mcp_row() if row is None else row

        if target_row < 0 or target_row >= len(servers):
            return

        dlg = MCPServerEditDialog(self, server_data=servers[target_row])

        if dlg.exec() != QtWidgets.QDialog.DialogCode.Accepted or not dlg.result_data:
            return

        servers[target_row] = dlg.result_data

        self._set_gateway_mcp_server_configs(servers)

        self.gateway_mcp_table.selectRow(target_row)

    def _remove_gateway_mcp_server(self, row: int = None):
        servers = self._parse_mcp_servers_json()

        target_row = self._selected_gateway_mcp_row() if row is None else row

        if target_row < 0 or target_row >= len(servers):
            return

        name = str(servers[target_row].get("name") or f"server_{target_row + 1}")

        reply = QtWidgets.QMessageBox.question(
            self,
            "MCP 服务器",
            f"确定删除 `{name}` 吗？",
            QtWidgets.QMessageBox.StandardButton.Yes
            | QtWidgets.QMessageBox.StandardButton.No,
        )

        if reply != QtWidgets.QMessageBox.StandardButton.Yes:
            return

        servers.pop(target_row)

        self._set_gateway_mcp_server_configs(servers)

        if servers:
            self.gateway_mcp_table.selectRow(min(target_row, len(servers) - 1))

    def _move_gateway_mcp_server(self, delta: int):
        servers = self._parse_mcp_servers_json()

        row = self._selected_gateway_mcp_row()

        target = row + delta

        if row < 0 or target < 0 or target >= len(servers):
            return

        servers[row], servers[target] = servers[target], servers[row]

        self._set_gateway_mcp_server_configs(servers)

        self.gateway_mcp_table.selectRow(target)

    def _gateway_qq_relation_label(self, value: str) -> str:
        mapping = {
            "owner": "主人",
            "contact": "熟人联系人",
            "group_member": "群成员",
        }

        return mapping.get(str(value or "").strip(), "-")

    def _gateway_qq_scope_label(self, value: str) -> str:
        mapping = {
            "owner_shared": "与主人共享",
            "private": "私聊独立",
            "group_shared": "群内共享",
        }

        return mapping.get(str(value or "").strip(), "-")

    def _gateway_qq_permission_label(self, value: str) -> str:
        mapping = {
            "default": "默认",
            "trusted": "信任",
            "restricted": "受限",
            "owner": "主人",
        }

        raw = str(value or "default").strip() or "default"

        return mapping.get(raw, raw)

    def _selected_gateway_qq_profile_user_id(self) -> str:
        if not hasattr(self, "gateway_qq_profile_table"):
            return ""

        row = self.gateway_qq_profile_table.currentRow()

        if row < 0:
            return ""

        item = self.gateway_qq_profile_table.item(row, 1)

        return str(item.text()).strip() if item else ""

    def _update_gateway_qq_profile_actions(self):
        user_id = self._selected_gateway_qq_profile_user_id()

        has_selection = bool(user_id)

        if hasattr(self, "gateway_qq_profile_edit_btn"):
            self.gateway_qq_profile_edit_btn.setEnabled(has_selection)

        if hasattr(self, "gateway_qq_profile_remove_btn"):
            self.gateway_qq_profile_remove_btn.setEnabled(has_selection)

        self._refresh_gateway_qq_profile_preview()

    def _refresh_gateway_qq_profile_preview(self):
        if not hasattr(self, "gateway_qq_profile_preview"):
            return

        store = self._get_memory_store()

        if store is None:
            self.gateway_qq_profile_preview.setText(
                "当前记忆存储不可用，暂时无法读取 QQ 档案。"
            )

            return

        user_id = self._selected_gateway_qq_profile_user_id()

        if not user_id:
            self.gateway_qq_profile_preview.setText(
                "请先选择一个 QQ 联系人，这里会显示她的身份摘要、关系、权限和备注。"
            )

            return

        profile = (
            store.get_qq_user_profile(user_id)
            if hasattr(store, "get_qq_user_profile")
            else None
        )

        if not profile:
            self.gateway_qq_profile_preview.setText("未找到该 QQ 联系人的档案。")

            return

        display_name = (
            str(profile.get("remark_name") or profile.get("nickname") or "-").strip()
            or "-"
        )

        identity_summary = (
            str(profile.get("identity_summary") or "").strip() or "暂无身份摘要"
        )

        notes = str(profile.get("notes") or "").strip() or "暂无备注"

        permission_text = self._gateway_qq_permission_label(
            profile.get("permission_level")
        )

        relation_text = self._gateway_qq_relation_label(
            profile.get("relationship_to_owner")
        )

        scope_text = self._gateway_qq_scope_label(profile.get("memory_scope"))

        preview_lines = [
            f"QQ：{user_id}",
            f"显示名：{display_name}",
            f"关系：{relation_text}",
            f"记忆范围：{scope_text}",
            f"权限等级：{permission_text}",
            f"身份摘要：{identity_summary}",
            f"备注：{notes}",
        ]

        self.gateway_qq_profile_preview.setText("\n".join(preview_lines))

    def _refresh_gateway_qq_profile_table(self):
        if not hasattr(self, "gateway_qq_profile_table"):
            return

        store = self._get_memory_store()

        profiles = []

        if store is not None and hasattr(store, "list_qq_user_profiles"):
            try:
                profiles = store.list_qq_user_profiles(limit=300)

            except Exception as exc:
                self.gateway_qq_profile_preview.setText(f"加载 QQ 档案失败：{exc}")

                profiles = []

        self.gateway_qq_profile_table.setRowCount(len(profiles))

        for row, profile in enumerate(profiles):
            user_id = str(profile.get("user_id") or "").strip()

            status_text = "主人" if bool(profile.get("is_owner")) else "普通"

            display_name = (
                str(profile.get("remark_name") or profile.get("nickname") or "").strip()
                or "-"
            )

            relation_text = self._gateway_qq_relation_label(
                profile.get("relationship_to_owner")
            )

            scope_text = self._gateway_qq_scope_label(profile.get("memory_scope"))

            permission_text = self._gateway_qq_permission_label(
                profile.get("permission_level")
            )

            summary_text = (
                str(profile.get("identity_summary") or "").replace("\n", " ").strip()
            )

            if len(summary_text) > 42:
                summary_text = summary_text[:42] + "?"

            self.gateway_qq_profile_table.setItem(
                row, 0, QtWidgets.QTableWidgetItem(status_text)
            )

            self.gateway_qq_profile_table.setItem(
                row, 1, QtWidgets.QTableWidgetItem(user_id)
            )

            self.gateway_qq_profile_table.setItem(
                row, 2, QtWidgets.QTableWidgetItem(display_name)
            )

            self.gateway_qq_profile_table.setItem(
                row, 3, QtWidgets.QTableWidgetItem(relation_text)
            )

            self.gateway_qq_profile_table.setItem(
                row, 4, QtWidgets.QTableWidgetItem(scope_text)
            )

            self.gateway_qq_profile_table.setItem(
                row, 5, QtWidgets.QTableWidgetItem(permission_text)
            )

            self.gateway_qq_profile_table.setItem(
                row, 6, QtWidgets.QTableWidgetItem(summary_text or "-")
            )

            action_widget = QtWidgets.QWidget()

            action_layout = QtWidgets.QHBoxLayout(action_widget)

            action_layout.setContentsMargins(0, 0, 0, 0)

            action_layout.setSpacing(6)

            btn_edit = QtWidgets.QPushButton("编辑")

            btn_edit.setObjectName("tableActionBtn")

            btn_edit.clicked.connect(
                lambda checked=False, uid=user_id: self._edit_gateway_qq_profile(uid)
            )

            action_layout.addWidget(btn_edit)

            btn_remove = QtWidgets.QPushButton("删除")

            btn_remove.setObjectName("tableDangerBtn")

            btn_remove.clicked.connect(
                lambda checked=False, uid=user_id: self._delete_gateway_qq_profile(uid)
            )

            action_layout.addWidget(btn_remove)

            self.gateway_qq_profile_table.setCellWidget(row, 7, action_widget)

        if profiles and self.gateway_qq_profile_table.currentRow() < 0:
            self.gateway_qq_profile_table.selectRow(0)

        self.gateway_qq_profile_table.resizeRowsToContents()

        self._update_gateway_qq_profile_actions()

        if not profiles:
            self.gateway_qq_profile_preview.setText(
                "还没有 QQ 联系人档案。后续收到 QQ 新消息时会自动补齐基础档案，你也可以手动新增。"
            )

    def _add_gateway_qq_profile(self):
        store = self._get_memory_store()

        if store is None or not hasattr(store, "upsert_qq_user_profile"):
            QtWidgets.QMessageBox.warning(self, "QQ 档案", "当前记忆存储不可用。")

            return

        dlg = QQUserProfileEditDialog(self)

        if dlg.exec() != QtWidgets.QDialog.DialogCode.Accepted or not dlg.result_data:
            return

        try:
            store.upsert_qq_user_profile(dlg.result_data)

        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "QQ 档案", f"保存失败：{exc}")

            return

        self._refresh_gateway_qq_profile_table()

        for row in range(self.gateway_qq_profile_table.rowCount()):
            item = self.gateway_qq_profile_table.item(row, 1)

            if (
                item
                and item.text().strip()
                == str(dlg.result_data.get("user_id") or "").strip()
            ):
                self.gateway_qq_profile_table.selectRow(row)

                break

    def _edit_gateway_qq_profile(self, user_id: str = ""):
        store = self._get_memory_store()

        if store is None or not hasattr(store, "get_qq_user_profile"):
            QtWidgets.QMessageBox.warning(self, "QQ 档案", "当前记忆存储不可用。")

            return

        target_user_id = str(
            user_id or self._selected_gateway_qq_profile_user_id()
        ).strip()

        if not target_user_id:
            return

        profile = store.get_qq_user_profile(target_user_id)

        if not profile:
            QtWidgets.QMessageBox.warning(self, "QQ 档案", "未找到该 QQ 档案。")

            return

        dlg = QQUserProfileEditDialog(self, profile_data=profile)

        if dlg.exec() != QtWidgets.QDialog.DialogCode.Accepted or not dlg.result_data:
            return

        try:
            store.upsert_qq_user_profile(dlg.result_data)

        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "QQ 档案", f"保存失败：{exc}")

            return

        self._refresh_gateway_qq_profile_table()

        for row in range(self.gateway_qq_profile_table.rowCount()):
            item = self.gateway_qq_profile_table.item(row, 1)

            if item and item.text().strip() == target_user_id:
                self.gateway_qq_profile_table.selectRow(row)

                break

    def _delete_gateway_qq_profile(self, user_id: str = ""):
        store = self._get_memory_store()

        if store is None or not hasattr(store, "delete_qq_user_profile"):
            QtWidgets.QMessageBox.warning(self, "QQ 档案", "当前记忆存储不可用。")

            return

        target_user_id = str(
            user_id or self._selected_gateway_qq_profile_user_id()
        ).strip()

        if not target_user_id:
            return

        reply = QtWidgets.QMessageBox.question(
            self,
            "QQ 档案",
            f"确定删除 QQ 档案 `{target_user_id}` 吗？\n\n删除后只会移除联系人画像，不会删除聊天记录与记忆正文。",
            QtWidgets.QMessageBox.StandardButton.Yes
            | QtWidgets.QMessageBox.StandardButton.No,
        )

        if reply != QtWidgets.QMessageBox.StandardButton.Yes:
            return

        try:
            ok = bool(store.delete_qq_user_profile(target_user_id))

        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "QQ 档案", f"删除失败：{exc}")

            return

        if not ok:
            QtWidgets.QMessageBox.warning(
                self, "QQ 档案", "删除失败，可能是档案已不存在。"
            )

            return

        self._refresh_gateway_qq_profile_table()

    def _gateway_webhook_url(self) -> str:
        host = self.gateway_webhook_host.text().strip() or "127.0.0.1"

        port = int(self.gateway_webhook_port.value())

        path = self.gateway_webhook_path.text().strip() or "/chat/napcat"

        if not path.startswith("/"):
            path = "/" + path

        return f"http://{host}:{port}{path}"

    def _gateway_ws_url(self, root: bool = False) -> str:
        host = self.gateway_webhook_host.text().strip() or "127.0.0.1"

        port = int(self.gateway_webhook_port.value())

        path = self.gateway_webhook_path.text().strip() or "/chat/napcat"

        if not path.startswith("/"):
            path = "/" + path

        if root:
            return f"ws://{host}:{port}"

        return f"ws://{host}:{port}{path}"

    def _normalize_mcp_access_control(self, access_control) -> dict:
        normalized = {
            "allow_local": True,
            "allow_remote_qq": True,
            "allow_qq_owner": True,
            "allow_qq_others": False,
        }

        if isinstance(access_control, dict):
            for key in normalized.keys():
                if key in access_control:
                    normalized[key] = bool(access_control.get(key))

        return normalized

    def _load_mcp_tools_config(self) -> dict:
        plugin_manager = (
            getattr(self.main_app, "plugin_manager", None) if self.main_app else None
        )

        if plugin_manager is not None:
            try:
                config = plugin_manager.get_plugin_config("mcp_tools")

                if isinstance(config, dict):
                    return dict(config)

            except Exception:
                pass

        config_path = os.path.join("plugins", "mcp_tools", "config.json")

        if os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)

                if isinstance(data, dict):
                    return data

            except Exception:
                pass

        return {}

    def _default_mcp_intent_route_settings(self) -> dict:
        return {
            "enabled": True,
            "brand_keywords": ["麦当劳", "mcd", "mcdonald", "麦乐送"],
            "action_keywords": [
                "查",
                "查一下",
                "查询",
                "看",
                "看一下",
                "领",
                "领取",
                "获取",
                "优惠",
                "优惠券",
                "会员券",
                "券",
                "折扣",
                "活动",
            ],
            "web_search_override_keywords": [
                "联网",
                "上网",
                "网页",
                "百度",
                "google",
                "bing",
                "搜索",
            ],
        }

    @staticmethod
    def _read_plugin_setting(settings: dict, key: str, default):
        if not isinstance(settings, dict):
            return default

        value = settings.get(key, default)

        if isinstance(value, dict):
            if "default" in value:
                return value.get("default")

            if "value" in value:
                return value.get("value")

        return value

    @staticmethod
    def _normalize_keyword_list(value) -> list:
        if isinstance(value, list):
            rows = [str(item).strip() for item in value if str(item).strip()]

        elif isinstance(value, str):
            text = value.replace("，", ",").replace("、", ",").replace("|", ",")

            rows = [
                item.strip()
                for line in text.splitlines()
                for item in line.split(",")
                if item.strip()
            ]

        else:
            rows = []

        return list(dict.fromkeys(rows))

    def _load_mcp_intent_route_settings(self) -> dict:
        defaults = self._default_mcp_intent_route_settings()

        config = self._load_mcp_tools_config()

        settings = config.get("settings", {}) if isinstance(config, dict) else {}

        enabled = bool(
            self._read_plugin_setting(
                settings, "intent_route_enabled", defaults["enabled"]
            )
        )

        brand_keywords = self._normalize_keyword_list(
            self._read_plugin_setting(
                settings, "intent_route_brand_keywords", defaults["brand_keywords"]
            )
        )

        action_keywords = self._normalize_keyword_list(
            self._read_plugin_setting(
                settings, "intent_route_action_keywords", defaults["action_keywords"]
            )
        )

        web_keywords = self._normalize_keyword_list(
            self._read_plugin_setting(
                settings,
                "intent_route_web_search_override_keywords",
                defaults["web_search_override_keywords"],
            )
        )

        return {
            "enabled": enabled,
            "brand_keywords": brand_keywords,
            "action_keywords": action_keywords,
            "web_search_override_keywords": web_keywords,
        }

    def _load_mcp_server_configs(self) -> list:
        config = self._load_mcp_tools_config()
        settings = config.get("settings", {}) if isinstance(config, dict) else {}
        raw = self._read_plugin_setting(
            settings if isinstance(settings, dict) else {},
            "servers",
            [],
        )
        if not isinstance(raw, list):
            raw = []
        return self._normalize_mcp_server_configs(raw)

    def _collect_mcp_server_configs(self) -> list:
        if not hasattr(self, "gateway_mcp_table"):
            return self._load_mcp_server_configs()
        return self._parse_mcp_servers_json()

    def _collect_mcp_intent_route_settings(self) -> dict:
        if not hasattr(self, "gateway_mcp_intent_route_enabled"):
            return self._load_mcp_intent_route_settings()

        return {
            "enabled": bool(self.gateway_mcp_intent_route_enabled.isChecked()),
            "brand_keywords": self._normalize_keyword_list(
                self.gateway_mcp_brand_keywords.toPlainText()
            ),
            "action_keywords": self._normalize_keyword_list(
                self.gateway_mcp_action_keywords.toPlainText()
            ),
            "web_search_override_keywords": self._normalize_keyword_list(
                self.gateway_mcp_web_search_override_keywords.toPlainText()
            ),
        }

    def _upsert_mcp_setting_entry(
        self,
        settings: dict,
        key: str,
        value,
        *,
        setting_type: str,
        label: str,
        description: str,
    ):
        old_entry = settings.get(key)

        if isinstance(old_entry, dict):
            new_entry = dict(old_entry)

            new_entry["default"] = value

            if "type" not in new_entry:
                new_entry["type"] = setting_type

            if "label" not in new_entry:
                new_entry["label"] = label

            if "description" not in new_entry:
                new_entry["description"] = description

            settings[key] = new_entry

            return

        settings[key] = {
            "type": setting_type,
            "default": value,
            "label": label,
            "description": description,
        }

    def _collect_mcp_access_control(self) -> dict:
        if not hasattr(self, "gateway_mcp_allow_local"):
            return self._normalize_mcp_access_control(
                self._load_mcp_tools_config().get("access_control")
            )

        return self._normalize_mcp_access_control(
            {
                "allow_local": self.gateway_mcp_allow_local.isChecked(),
                "allow_remote_qq": self.gateway_mcp_allow_remote_qq.isChecked(),
                "allow_qq_owner": self.gateway_mcp_allow_qq_owner.isChecked(),
                "allow_qq_others": self.gateway_mcp_allow_qq_others.isChecked(),
            }
        )

    def _describe_mcp_access_control(self, access_control) -> str:
        normalized = self._normalize_mcp_access_control(access_control)

        local_text = (
            "允许本地桌面侧触发"
            if normalized["allow_local"]
            else "已禁用本地桌面侧触发"
        )

        if not normalized["allow_remote_qq"]:
            qq_text = "QQ 侧不可触发"

        elif normalized["allow_qq_owner"] and normalized["allow_qq_others"]:
            qq_text = "主人和其他 QQ 联系人都可触发"

        elif normalized["allow_qq_owner"]:
            qq_text = "仅主人 QQ 可触发"

        elif normalized["allow_qq_others"]:
            qq_text = "仅其他 QQ 联系人可触发"

        else:
            qq_text = "已开放 QQ 通道，但当前没有任何 QQ 联系人可触发"

        return f"{local_text}；{qq_text}"

    def _save_mcp_tools_access_control(self) -> dict:
        config = self._load_mcp_tools_config()

        if not config:
            return {
                "changed": False,
                "saved": False,
                "applied": False,
                "error": "未找到 mcp_tools 插件配置",
            }

        new_access = self._collect_mcp_access_control()

        old_access = self._normalize_mcp_access_control(config.get("access_control"))

        new_intent_route = self._collect_mcp_intent_route_settings()

        old_intent_route = self._load_mcp_intent_route_settings()

        new_servers = self._collect_mcp_server_configs()

        old_servers = self._load_mcp_server_configs()

        config["access_control"] = new_access

        settings = config.get("settings", {})

        if not isinstance(settings, dict):
            settings = {}

        config["settings"] = settings

        self._upsert_mcp_setting_entry(
            settings,
            "intent_route_enabled",
            bool(new_intent_route.get("enabled", True)),
            setting_type="boolean",
            label="自然语言路由开关",
            description="开启后可按品牌词+动作词优先路由到 mcp_tools；关闭后只按常规插件关键词路由。",
        )

        self._upsert_mcp_setting_entry(
            settings,
            "intent_route_brand_keywords",
            list(new_intent_route.get("brand_keywords") or []),
            setting_type="list",
            label="品牌/领域关键词",
            description="命中这些词会被视为 MCP 目标领域（如：麦当劳）。",
        )

        self._upsert_mcp_setting_entry(
            settings,
            "intent_route_action_keywords",
            list(new_intent_route.get("action_keywords") or []),
            setting_type="list",
            label="动作关键词",
            description="和品牌词同时命中时，优先走 mcp_tools（如：查、领、优惠券）。",
        )

        self._upsert_mcp_setting_entry(
            settings,
            "intent_route_web_search_override_keywords",
            list(new_intent_route.get("web_search_override_keywords") or []),
            setting_type="list",
            label="显式联网搜索关键词",
            description="命中这些词时优先走联网搜索，不抢占 mcp_tools。",
        )

        self._upsert_mcp_setting_entry(
            settings,
            "servers",
            list(new_servers),
            setting_type="json",
            label="远程 MCP 服务器",
            description="由设置里的 MCP 页面维护。每项是 stdio 或 streamable_http 服务器。",
        )
        servers_entry = settings.get("servers")
        if isinstance(servers_entry, dict):
            servers_entry["ui"] = "hidden"

        access_changed = old_access != new_access

        intent_route_changed = old_intent_route != new_intent_route

        servers_changed = old_servers != new_servers

        changed = access_changed or intent_route_changed or servers_changed

        if not changed:
            return {
                "changed": False,
                "saved": True,
                "applied": True,
                "access_control": new_access,
                "intent_route": new_intent_route,
                "servers": new_servers,
                "server_status": self._mcp_plugin_server_status(),
                "access_changed": False,
                "intent_route_changed": False,
                "servers_changed": False,
            }

        plugin_manager = (
            getattr(self.main_app, "plugin_manager", None) if self.main_app else None
        )

        if plugin_manager is not None:
            try:
                existing = plugin_manager.get_plugin_config("mcp_tools")

            except Exception:
                existing = None

            if isinstance(existing, dict):
                ok = bool(plugin_manager.save_plugin_config("mcp_tools", config))

                return {
                    "changed": True,
                    "saved": ok,
                    "applied": ok,
                    "error": ""
                    if ok
                    else "plugin_manager.save_plugin_config 返回 False",
                    "access_control": new_access,
                    "intent_route": new_intent_route,
                    "servers": new_servers,
                    "server_status": self._mcp_plugin_server_status() if ok else [],
                    "access_changed": access_changed,
                    "intent_route_changed": intent_route_changed,
                    "servers_changed": servers_changed,
                }

        config_path = os.path.join("plugins", "mcp_tools", "config.json")

        try:
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=4)

            return {
                "changed": True,
                "saved": True,
                "applied": False,
                "access_control": new_access,
                "intent_route": new_intent_route,
                "servers": new_servers,
                "server_status": [],
                "access_changed": access_changed,
                "intent_route_changed": intent_route_changed,
                "servers_changed": servers_changed,
            }

        except Exception as e:
            return {
                "changed": True,
                "saved": False,
                "applied": False,
                "error": str(e),
                "access_control": new_access,
                "intent_route": new_intent_route,
                "servers": new_servers,
                "server_status": [],
                "access_changed": access_changed,
                "intent_route_changed": intent_route_changed,
                "servers_changed": servers_changed,
            }

    def _mcp_plugin_server_status(self) -> list:
        plugin_manager = (
            getattr(self.main_app, "plugin_manager", None) if self.main_app else None
        )
        plugins = getattr(plugin_manager, "plugins", None) or {}
        plugin = plugins.get("mcp_tools") if isinstance(plugins, dict) else None
        getter = getattr(plugin, "list_server_status", None)
        if not callable(getter):
            return []
        try:
            return list(getter() or [])
        except Exception:
            return []

    def _refresh_gateway_preview(self, *args):
        if hasattr(self, "gateway_url_preview"):
            mode_map = {"off": "不过滤", "whitelist": "白名单", "blacklist": "黑名单"}

            mode_text = mode_map.get(self._selected_gateway_filter_mode(), "不过滤")

            voice_enabled = bool(
                getattr(self, "gateway_voice_reply_enabled", None)
                and self.gateway_voice_reply_enabled.isChecked()
            )

            voice_probability = (
                int(self.gateway_voice_reply_probability.value())
                if hasattr(self, "gateway_voice_reply_probability")
                else 0
            )

            self.gateway_url_preview.setText(
                f"Webhook 地址：{self._gateway_webhook_url()}\n"
                f"反向 WS 地址：{self._gateway_ws_url()}（也兼容 {self._gateway_ws_url(root=True)}）\n"
                f"NapCat 可二选一：HTTP 上报到 Webhook，或反向 WebSocket 连到 WS 地址；回消息优先走 WS，不通再回退 HTTP API。\n"
                f"QQ 过滤模式：{mode_text}\n"
                f"QQ 概率语音：{'开启' if voice_enabled else '关闭'}"
                f"{'（' + str(voice_probability) + '% 命中，成功时优先发语音，失败回退文本）' if voice_enabled else ''}\n"
                f"QQ 对话默认沿用当前本地激活人设，不会单独切一个 QQ 人格。"
            )

        if hasattr(self, "gateway_group_require_at") and hasattr(
            self, "gateway_allow_group"
        ):
            self.gateway_group_require_at.setEnabled(
                self.gateway_allow_group.isChecked()
            )

        if hasattr(self, "gateway_voice_reply_probability") and hasattr(
            self, "gateway_voice_reply_enabled"
        ):
            self.gateway_voice_reply_probability.setEnabled(
                self.gateway_voice_reply_enabled.isChecked()
            )

        mode = self._selected_gateway_filter_mode()

        for name in ["gateway_user_whitelist", "gateway_group_whitelist"]:
            if hasattr(self, name):
                getattr(self, name).setEnabled(mode == "whitelist")

        for name in ["gateway_user_blacklist", "gateway_group_blacklist"]:
            if hasattr(self, name):
                getattr(self, name).setEnabled(mode == "blacklist")

        if hasattr(self, "gateway_mcp_allow_remote_qq"):
            remote_enabled = self.gateway_mcp_allow_remote_qq.isChecked()

            if hasattr(self, "gateway_mcp_allow_qq_owner"):
                self.gateway_mcp_allow_qq_owner.setEnabled(remote_enabled)

            if hasattr(self, "gateway_mcp_allow_qq_others"):
                self.gateway_mcp_allow_qq_others.setEnabled(remote_enabled)

        if hasattr(self, "gateway_mcp_intent_route_enabled"):
            intent_enabled = self.gateway_mcp_intent_route_enabled.isChecked()

            if hasattr(self, "gateway_mcp_brand_keywords"):
                self.gateway_mcp_brand_keywords.setEnabled(intent_enabled)

            if hasattr(self, "gateway_mcp_action_keywords"):
                self.gateway_mcp_action_keywords.setEnabled(intent_enabled)

            if hasattr(self, "gateway_mcp_web_search_override_keywords"):
                self.gateway_mcp_web_search_override_keywords.setEnabled(intent_enabled)

        if hasattr(self, "gateway_mcp_access_preview"):
            config = self._load_mcp_tools_config()

            if not config:
                self.gateway_mcp_access_preview.setText(
                    "未找到 `mcp_tools` 插件配置，暂时无法设置 MCP 的 QQ 触发权限。"
                )

            else:
                summary = self._describe_mcp_access_control(
                    self._collect_mcp_access_control()
                )

                self.gateway_mcp_access_preview.setText(
                    f"当前权限：{summary}\n建议：如果你只想让自己从 QQ 远程调用 MCP，就仅保留“允许主人 QQ 触发”。"
                )

        if hasattr(self, "gateway_mcp_intent_route_preview"):
            route_cfg = self._collect_mcp_intent_route_settings()

            if not route_cfg.get("enabled", True):
                self.gateway_mcp_intent_route_preview.setText(
                    "自然语言路由：已关闭（不会优先抢占 mcp_tools）。"
                )

            else:
                brands = route_cfg.get("brand_keywords") or []

                actions = route_cfg.get("action_keywords") or []

                web_words = route_cfg.get("web_search_override_keywords") or []

                sample_brand = "、".join(brands[:4]) + (
                    " 等" if len(brands) > 4 else ""
                )

                sample_action = "、".join(actions[:5]) + (
                    " 等" if len(actions) > 5 else ""
                )

                sample_web = "、".join(web_words[:5]) + (
                    " 等" if len(web_words) > 5 else ""
                )

                self.gateway_mcp_intent_route_preview.setText(
                    f"自然语言路由：已开启\n"
                    f"- 品牌词 {len(brands)} 个：{sample_brand or '（空）'}\n"
                    f"- 动作词 {len(actions)} 个：{sample_action or '（空）'}\n"
                    f"- 联网覆盖词 {len(web_words)} 个：{sample_web or '（空）'}"
                )

        if hasattr(self, "gateway_mcp_preview"):
            try:
                servers = self._parse_mcp_servers_json()

                enabled_count = sum(
                    1
                    for item in servers
                    if isinstance(item, dict) and bool(item.get("enabled", True))
                )

                stdio_count = sum(
                    1
                    for item in servers
                    if isinstance(item, dict)
                    and str(item.get("transport") or "stdio") == "stdio"
                )

                http_count = len(servers) - stdio_count

                names = "、".join(
                    str(item.get("name") or "server")
                    for item in servers[:4]
                    if isinstance(item, dict)
                )

                if len(servers) > 4:
                    names += " 等"

                lines = [
                    f"已配置 MCP 服务器 {len(servers)} 个，启用 {enabled_count} 个；其中 stdio {stdio_count} 个、HTTP {http_count} 个。"
                ]

                if names:
                    lines.append(f"当前列表：{names}")

                lines.extend(
                    [
                        "",
                        "调用示例：",
                        "- [CMD: mcp_tools | list_tools]",
                        "- [CMD: mcp_tools | server_status]",
                        '- [CMD: mcp_tools | call_tool ||| mcp.server.tool ||| {"key": "value"}]',
                        "远程工具会自动暴露成 `mcp.<server>.<tool>` 形式。",
                    ]
                )

                self.gateway_mcp_preview.setText("\n".join(lines))

            except Exception as e:
                self.gateway_mcp_preview.setText(f"MCP 服务器预览生成失败：{e}")

    def _collect_gateway_settings(self):
        return {
            "napcat_enabled": self.gateway_napcat_enabled.isChecked(),
            "napcat_webhook_host": self.gateway_webhook_host.text().strip()
            or "127.0.0.1",
            "napcat_webhook_port": int(self.gateway_webhook_port.value()),
            "napcat_webhook_path": (
                self.gateway_webhook_path.text().strip() or "/chat/napcat"
            ),
            "napcat_access_token": self.gateway_access_token.text().strip(),
            "napcat_api_base": self.gateway_api_base.text().strip()
            or "http://127.0.0.1:3000",
            "napcat_api_token": self.gateway_api_token.text().strip(),
            "napcat_reply_enabled": self.gateway_reply_enabled.isChecked(),
            "napcat_allow_private": self.gateway_allow_private.isChecked(),
            "napcat_allow_group": self.gateway_allow_group.isChecked(),
            "napcat_group_require_at": self.gateway_group_require_at.isChecked(),
            "napcat_owner_user_ids": [
                item.strip()
                for item in re.split(
                    r"[,，\n\s]+", self.gateway_owner_ids.text().strip()
                )
                if item.strip()
            ],
            "napcat_owner_label": self.gateway_owner_label.text().strip() or "主人",
            "napcat_image_vision_enabled": self.gateway_image_vision_enabled.isChecked(),
            "napcat_image_prompt": self.gateway_image_prompt.toPlainText().strip()
            or "请客观详细描述这张QQ图片的内容，并提取其中可用于回复的关键信息。",
            "napcat_voice_reply_enabled": self.gateway_voice_reply_enabled.isChecked(),
            "napcat_voice_reply_probability": int(
                self.gateway_voice_reply_probability.value()
            ),
            "napcat_filter_mode": self._selected_gateway_filter_mode(),
            "napcat_user_whitelist": [
                item.strip()
                for item in re.split(
                    r"[,，\n\s]+", self.gateway_user_whitelist.text().strip()
                )
                if item.strip()
            ],
            "napcat_user_blacklist": [
                item.strip()
                for item in re.split(
                    r"[,，\n\s]+", self.gateway_user_blacklist.text().strip()
                )
                if item.strip()
            ],
            "napcat_group_whitelist": [
                item.strip()
                for item in re.split(
                    r"[,，\n\s]+", self.gateway_group_whitelist.text().strip()
                )
                if item.strip()
            ],
            "napcat_group_blacklist": [
                item.strip()
                for item in re.split(
                    r"[,，\n\s]+", self.gateway_group_blacklist.text().strip()
                )
                if item.strip()
            ],
            "remote_chat_ui_append": self.gateway_ui_append.isChecked(),
        }

    def _copy_gateway_webhook_url(self):
        url = self._gateway_webhook_url()

        QtWidgets.QApplication.clipboard().setText(url)

        QtWidgets.QMessageBox.information(self, "QQ 接入", f"Webhook 地址已复制\n{url}")

    def _save_gateway_settings(self, restart_after_save: bool = False):
        old_settings = self._load_gateway_settings()

        try:
            new_settings = self._collect_gateway_settings()

        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "外部接入", f"配置保存失败：{e}")

            return

        if not new_settings["napcat_webhook_path"].startswith("/"):
            new_settings["napcat_webhook_path"] = (
                "/" + new_settings["napcat_webhook_path"]
            )

        update_runtime_settings(new_settings)

        apply_result = {}

        if getattr(self.main_app, "apply_external_settings", None):
            try:
                apply_result = self.main_app.apply_external_settings(new_settings) or {}

            except Exception as e:
                apply_result = {"error": str(e)}

        restart_sensitive_keys = {
            "napcat_enabled",
            "napcat_webhook_host",
            "napcat_webhook_port",
            "napcat_webhook_path",
            "napcat_access_token",
            "napcat_api_base",
            "napcat_api_token",
            "napcat_reply_enabled",
            "napcat_allow_private",
            "napcat_allow_group",
            "napcat_group_require_at",
            "napcat_owner_user_ids",
            "napcat_owner_label",
            "napcat_image_vision_enabled",
            "napcat_image_prompt",
            "napcat_voice_reply_enabled",
            "napcat_voice_reply_probability",
            "napcat_filter_mode",
            "napcat_user_whitelist",
            "napcat_user_blacklist",
            "napcat_group_whitelist",
            "napcat_group_blacklist",
        }

        live_apply_ok = (
            bool(apply_result)
            and not apply_result.get("error")
            and bool(apply_result.get("napcat_live_applied"))
        )

        restart_needed = (
            any(
                old_settings.get(key) != new_settings.get(key)
                for key in restart_sensitive_keys
            )
            and not live_apply_ok
        )

        message_lines = [
            "设置已保存。",
            "",
            "- QQ 回复是否显示到本地聊天框：对新消息立即生效",
        ]

        if live_apply_ok:
            if new_settings.get("napcat_enabled"):
                if apply_result.get("napcat_server_running"):
                    message_lines.append(
                        "- NapCat Webhook / 回发参数：已即时刷新并开始监听"
                    )

                else:
                    message_lines.append(
                        "- NapCat 回发参数：已即时刷新；Webhook 将在异步循环就绪后监听"
                    )

            else:
                message_lines.append("- NapCat Webhook：已即时关闭")

        else:
            message_lines.append("- NapCat 运行时参数：建议重启后完全生效")

            if apply_result.get("error"):
                message_lines.append(f"- 即时应用失败：{apply_result.get('error')}")

        message = "\n".join(message_lines)

        if restart_after_save and getattr(self.main_app, "on_restart_callback", None):
            QtWidgets.QMessageBox.information(
                self, "外部接入", message + "\n\n即将重启应用。"
            )

            self.main_app.on_restart_callback()

            return

        if restart_needed and getattr(self.main_app, "on_restart_callback", None):
            reply = QtWidgets.QMessageBox.question(
                self,
                "外部接入",
                message
                + "\n\n检测到 QQ 接入参数发生变化，是否现在重启应用？",
                QtWidgets.QMessageBox.StandardButton.Yes
                | QtWidgets.QMessageBox.StandardButton.No,
            )

            if reply == QtWidgets.QMessageBox.StandardButton.Yes:
                self.main_app.on_restart_callback()

                return

        QtWidgets.QMessageBox.information(self, "外部接入", message)

    def _save_mcp_settings(self, restart_after_save: bool = False):
        try:
            result = self._save_mcp_tools_access_control()
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "MCP", f"配置保存失败：{exc}")
            return

        lines = ["MCP 设置已保存。", ""]
        if not result.get("saved"):
            lines.append(f"- 写入失败：{result.get('error') or '未知错误'}")
        else:
            servers = result.get("servers") or []
            status = result.get("server_status") or []
            connected = sum(
                1 for item in status if isinstance(item, dict) and item.get("connected")
            )
            if result.get("servers_changed"):
                lines.append(
                    f"- 远程服务器：已写入 mcp_tools，共 {len(servers)} 个，当前连通 {connected} 个"
                )
            else:
                lines.append(f"- 远程服务器：没有变化（当前 {len(servers)} 个）")
            if result.get("access_changed"):
                lines.append(
                    "- QQ 触发权限：已更新"
                    if result.get("applied")
                    else "- QQ 触发权限：已写入配置，建议重启后确认生效"
                )
            if result.get("intent_route_changed"):
                lines.append(
                    "- 自然语言路由：已更新"
                    if result.get("applied")
                    else "- 自然语言路由：已写入配置，建议重启后确认生效"
                )
            if not result.get("applied") and result.get("changed"):
                lines.append("- 插件未加载，配置已写入文件")

        message = "\n".join(lines)
        if restart_after_save and getattr(self.main_app, "on_restart_callback", None):
            QtWidgets.QMessageBox.information(self, "MCP", message + "\n\n即将重启应用。")
            self.main_app.on_restart_callback()
            return
        QtWidgets.QMessageBox.information(self, "MCP", message)

    def _init_mcp_page(self):
        page = QtWidgets.QWidget()

        root = QtWidgets.QVBoxLayout(page)

        root.setContentsMargins(0, 0, 0, 0)

        container = QtWidgets.QWidget()

        layout = QtWidgets.QVBoxLayout(container)

        layout.setContentsMargins(0, 0, 0, 0)

        layout.setSpacing(14)

        mcp_card = QtWidgets.QFrame()

        mcp_card.setObjectName("launchCard")

        mcp_layout = QtWidgets.QVBoxLayout(mcp_card)

        mcp_layout.setContentsMargins(20, 18, 20, 18)

        mcp_layout.setSpacing(8)

        mcp_title = QtWidgets.QLabel("MCP 当前状态")

        mcp_title.setObjectName("launchTitle")

        mcp_layout.addWidget(mcp_title)

        mcp_desc = QtWidgets.QLabel(
            "在这里添加远程 MCP 服务器。保存后写入 mcp_tools 插件配置并立刻重连。"
            "没有服务器时，聊天不会把 mcp_tools 放进工具列表。"
            "远程工具名是 mcp.<server>.<tool>。调用还要在允许名单里写下服务器名，或打开“允许全部远程 MCP 工具”。"
        )

        mcp_desc.setObjectName("launchDesc")

        mcp_desc.setWordWrap(True)

        mcp_layout.addWidget(mcp_desc)

        mcp_editor_desc = QtWidgets.QLabel(
            "远程 MCP 服务器现在可以直接在这里新增、编辑和排序，不用再手填 JSON。"
        )

        mcp_editor_desc.setObjectName("launchDesc")

        mcp_editor_desc.setWordWrap(True)

        mcp_layout.addWidget(mcp_editor_desc)

        mcp_toolbar = QtWidgets.QHBoxLayout()

        btn_add_stdio = QtWidgets.QPushButton("+ 本地进程")

        btn_add_stdio.clicked.connect(
            lambda checked=False: self._add_gateway_mcp_server("stdio")
        )

        mcp_toolbar.addWidget(btn_add_stdio)

        btn_add_http = QtWidgets.QPushButton("+ HTTP 服务器")

        btn_add_http.clicked.connect(
            lambda checked=False: self._add_gateway_mcp_server("streamable_http")
        )

        mcp_toolbar.addWidget(btn_add_http)

        self.gateway_mcp_edit_btn = QtWidgets.QPushButton("编辑所选")

        self.gateway_mcp_edit_btn.clicked.connect(self._edit_gateway_mcp_server)

        mcp_toolbar.addWidget(self.gateway_mcp_edit_btn)

        self.gateway_mcp_remove_btn = QtWidgets.QPushButton("删除所选")

        self.gateway_mcp_remove_btn.clicked.connect(self._remove_gateway_mcp_server)

        mcp_toolbar.addWidget(self.gateway_mcp_remove_btn)

        self.gateway_mcp_up_btn = QtWidgets.QPushButton("上移")

        self.gateway_mcp_up_btn.clicked.connect(
            lambda checked=False: self._move_gateway_mcp_server(-1)
        )

        mcp_toolbar.addWidget(self.gateway_mcp_up_btn)

        self.gateway_mcp_down_btn = QtWidgets.QPushButton("下移")

        self.gateway_mcp_down_btn.clicked.connect(
            lambda checked=False: self._move_gateway_mcp_server(1)
        )

        mcp_toolbar.addWidget(self.gateway_mcp_down_btn)

        mcp_toolbar.addStretch()

        mcp_layout.addLayout(mcp_toolbar)

        self.gateway_mcp_table = QtWidgets.QTableWidget()

        self.gateway_mcp_table.setColumnCount(6)

        self.gateway_mcp_table.setHorizontalHeaderLabels(
            ["状态", "名称", "方式", "入口", "超时", "操作"]
        )

        self.gateway_mcp_table.setSelectionBehavior(
            QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows
        )

        self.gateway_mcp_table.setSelectionMode(
            QtWidgets.QAbstractItemView.SelectionMode.SingleSelection
        )

        self.gateway_mcp_table.setEditTriggers(
            QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers
        )

        self.gateway_mcp_table.verticalHeader().setVisible(False)

        self.gateway_mcp_table.verticalHeader().setDefaultSectionSize(36)

        self.gateway_mcp_table.setAlternatingRowColors(True)

        self.gateway_mcp_table.setMinimumHeight(0)

        self.gateway_mcp_table.horizontalHeader().setStretchLastSection(False)

        self.gateway_mcp_table.horizontalHeader().setSectionResizeMode(
            0, QtWidgets.QHeaderView.ResizeMode.ResizeToContents
        )

        self.gateway_mcp_table.horizontalHeader().setSectionResizeMode(
            1, QtWidgets.QHeaderView.ResizeMode.ResizeToContents
        )

        self.gateway_mcp_table.horizontalHeader().setSectionResizeMode(
            2, QtWidgets.QHeaderView.ResizeMode.ResizeToContents
        )

        self.gateway_mcp_table.horizontalHeader().setSectionResizeMode(
            3, QtWidgets.QHeaderView.ResizeMode.Stretch
        )

        self.gateway_mcp_table.horizontalHeader().setSectionResizeMode(
            4, QtWidgets.QHeaderView.ResizeMode.ResizeToContents
        )

        self.gateway_mcp_table.horizontalHeader().setSectionResizeMode(
            5, QtWidgets.QHeaderView.ResizeMode.Fixed
        )
        self.gateway_mcp_table.setColumnWidth(5, 124)

        self.gateway_mcp_table.itemSelectionChanged.connect(
            self._update_gateway_mcp_actions
        )

        self.gateway_mcp_table.itemDoubleClicked.connect(
            lambda item: self._edit_gateway_mcp_server(item.row())
        )

        mcp_layout.addWidget(self.gateway_mcp_table)

        self._set_gateway_mcp_server_configs(self._load_mcp_server_configs())

        self.gateway_mcp_preview = QtWidgets.QLabel("")

        self.gateway_mcp_preview.setObjectName("launchDesc")

        self.gateway_mcp_preview.setWordWrap(True)

        mcp_layout.addWidget(self.gateway_mcp_preview)

        layout.addWidget(mcp_card)

        access_card = QtWidgets.QFrame()

        access_card.setObjectName("launchCard")

        access_layout = QtWidgets.QVBoxLayout(access_card)

        access_layout.setContentsMargins(20, 18, 20, 18)

        access_layout.setSpacing(8)

        access_title = QtWidgets.QLabel("MCP 的 QQ 触发权限")

        access_title.setObjectName("launchTitle")

        access_layout.addWidget(access_title)

        access_desc = QtWidgets.QLabel(
            "这里控制 `mcp_tools` 插件是否能被 QQ 侧触发。这个权限只影响 MCP，不影响普通 QQ 对话。"
        )

        access_desc.setObjectName("launchDesc")

        access_desc.setWordWrap(True)

        access_layout.addWidget(access_desc)

        access = self._normalize_mcp_access_control(
            self._load_mcp_tools_config().get("access_control")
        )

        self.gateway_mcp_allow_local = QtWidgets.QCheckBox("允许桌面本地触发 MCP")

        self.gateway_mcp_allow_local.setChecked(bool(access["allow_local"]))

        access_layout.addWidget(self.gateway_mcp_allow_local)

        self.gateway_mcp_allow_remote_qq = QtWidgets.QCheckBox("允许 QQ 远程触发 MCP")

        self.gateway_mcp_allow_remote_qq.setChecked(bool(access["allow_remote_qq"]))

        access_layout.addWidget(self.gateway_mcp_allow_remote_qq)

        self.gateway_mcp_allow_qq_owner = QtWidgets.QCheckBox("允许主人 QQ 触发")

        self.gateway_mcp_allow_qq_owner.setChecked(bool(access["allow_qq_owner"]))

        access_layout.addWidget(self.gateway_mcp_allow_qq_owner)

        self.gateway_mcp_allow_qq_others = QtWidgets.QCheckBox("允许其他 QQ 联系人触发")

        self.gateway_mcp_allow_qq_others.setChecked(bool(access["allow_qq_others"]))

        access_layout.addWidget(self.gateway_mcp_allow_qq_others)

        self.gateway_mcp_access_preview = QtWidgets.QLabel("")

        self.gateway_mcp_access_preview.setObjectName("launchDesc")

        self.gateway_mcp_access_preview.setWordWrap(True)

        access_layout.addWidget(self.gateway_mcp_access_preview)

        layout.addWidget(access_card)

        intent_route_card = QtWidgets.QFrame()

        intent_route_card.setObjectName("launchCard")

        intent_route_layout = QtWidgets.QVBoxLayout(intent_route_card)

        intent_route_layout.setContentsMargins(20, 18, 20, 18)

        intent_route_layout.setSpacing(8)

        intent_route_title = QtWidgets.QLabel("MCP 自然语言路由")

        intent_route_title.setObjectName("launchTitle")

        intent_route_layout.addWidget(intent_route_title)

        intent_route_desc = QtWidgets.QLabel(
            "当消息同时命中“品牌词 + 动作词”时，优先将请求路由到 mcp_tools；如果命中“显式联网词”，则优先走联网搜索。"
        )

        intent_route_desc.setObjectName("launchDesc")

        intent_route_desc.setWordWrap(True)

        intent_route_layout.addWidget(intent_route_desc)

        intent_route = self._load_mcp_intent_route_settings()

        self.gateway_mcp_intent_route_enabled = QtWidgets.QCheckBox(
            "启用 MCP 自然语言优先路由"
        )

        self.gateway_mcp_intent_route_enabled.setChecked(
            bool(intent_route.get("enabled", True))
        )

        intent_route_layout.addWidget(self.gateway_mcp_intent_route_enabled)

        intent_form = QtWidgets.QFormLayout()

        intent_form.setLabelAlignment(QtCore.Qt.AlignmentFlag.AlignRight)

        intent_form.setFormAlignment(QtCore.Qt.AlignmentFlag.AlignTop)

        intent_form.setHorizontalSpacing(14)

        intent_form.setVerticalSpacing(10)

        self.gateway_mcp_brand_keywords = QtWidgets.QPlainTextEdit(
            "\n".join(intent_route.get("brand_keywords") or [])
        )

        self.gateway_mcp_brand_keywords.setPlaceholderText("每行一个，如：麦当劳")

        self.gateway_mcp_brand_keywords.setFixedHeight(78)

        self.gateway_mcp_action_keywords = QtWidgets.QPlainTextEdit(
            "\n".join(intent_route.get("action_keywords") or [])
        )

        self.gateway_mcp_action_keywords.setPlaceholderText(
            "每行一个，如：查一下、优惠券、领取"
        )

        self.gateway_mcp_action_keywords.setFixedHeight(96)

        self.gateway_mcp_web_search_override_keywords = QtWidgets.QPlainTextEdit(
            "\n".join(intent_route.get("web_search_override_keywords") or [])
        )

        self.gateway_mcp_web_search_override_keywords.setPlaceholderText(
            "每行一个，如：联网、百度、搜索"
        )

        self.gateway_mcp_web_search_override_keywords.setFixedHeight(78)

        intent_form.addRow("品牌词", self.gateway_mcp_brand_keywords)

        intent_form.addRow("动作词", self.gateway_mcp_action_keywords)

        intent_form.addRow("联网覆盖词", self.gateway_mcp_web_search_override_keywords)

        intent_route_layout.addLayout(intent_form)

        self.gateway_mcp_intent_route_preview = QtWidgets.QLabel("")

        self.gateway_mcp_intent_route_preview.setObjectName("launchDesc")

        self.gateway_mcp_intent_route_preview.setWordWrap(True)

        intent_route_layout.addWidget(self.gateway_mcp_intent_route_preview)

        layout.addWidget(intent_route_card)

        footer = QtWidgets.QHBoxLayout()

        btn_save = QtWidgets.QPushButton("保存")

        btn_save.setObjectName("primaryAction")

        btn_save.clicked.connect(self._save_mcp_settings)

        footer.addWidget(btn_save)

        btn_save_restart = QtWidgets.QPushButton("保存并重启")

        btn_save_restart.clicked.connect(
            lambda checked=False: self._save_mcp_settings(restart_after_save=True)
        )

        btn_save_restart.setEnabled(
            bool(getattr(self.main_app, "on_restart_callback", None))
        )

        footer.addWidget(btn_save_restart)

        footer.addStretch()

        layout.addLayout(footer)

        layout.addStretch()

        self.gateway_mcp_allow_local.toggled.connect(self._refresh_gateway_preview)

        self.gateway_mcp_allow_remote_qq.toggled.connect(self._refresh_gateway_preview)

        self.gateway_mcp_allow_qq_owner.toggled.connect(self._refresh_gateway_preview)

        self.gateway_mcp_allow_qq_others.toggled.connect(self._refresh_gateway_preview)

        self.gateway_mcp_intent_route_enabled.toggled.connect(
            self._refresh_gateway_preview
        )

        self.gateway_mcp_brand_keywords.textChanged.connect(
            self._refresh_gateway_preview
        )

        self.gateway_mcp_action_keywords.textChanged.connect(
            self._refresh_gateway_preview
        )

        self.gateway_mcp_web_search_override_keywords.textChanged.connect(
            self._refresh_gateway_preview
        )

        self._refresh_gateway_preview()

        root.addWidget(self._wrap_in_scroll_area(container), 1)

        self.stack.addWidget(page)

    def _init_external_services_page(self):
        widget = ExternalServicesPage()
        apply_embedded_mode(widget)
        self.stack.addWidget(self._wrap_in_scroll_area(widget))

    def _init_gateway_page(self):
        page = QtWidgets.QWidget()

        root = QtWidgets.QVBoxLayout(page)

        root.setContentsMargins(0, 0, 0, 0)

        container = QtWidgets.QWidget()

        layout = QtWidgets.QVBoxLayout(container)

        layout.setContentsMargins(0, 0, 0, 0)

        layout.setSpacing(14)

        state = self._load_gateway_settings()

        qq_card = QtWidgets.QFrame()

        qq_card.setObjectName("launchCard")

        qq_layout = QtWidgets.QVBoxLayout(qq_card)

        qq_layout.setContentsMargins(20, 18, 20, 18)

        qq_layout.setSpacing(10)

        qq_title = QtWidgets.QLabel("NapCat / QQ 接入")

        qq_title.setObjectName("launchTitle")

        qq_layout.addWidget(qq_title)

        qq_desc = QtWidgets.QLabel(
            "这里保存的是运行时接入参数。QQ 回复显示到本地聊天框的开关会立即用于新消息；NapCat 的监听地址、Token 与回发地址现在会尝试即时刷新，重启只作为兜底。QQ 对话默认沿用当前本地激活人设与口吻，不单独切人格。"
        )

        qq_desc.setObjectName("launchDesc")

        qq_desc.setWordWrap(True)

        qq_layout.addWidget(qq_desc)

        qq_mode_hint = QtWidgets.QLabel(
            "接入建议：本程序现在同端口同时支持 HTTP webhook 和反向 WebSocket；NapCat 任选其一接入即可。若你开了反向 WS，程序会优先走 WS 回消息，不通再回退到 HTTP API。"
        )

        qq_mode_hint.setObjectName("launchDesc")

        qq_mode_hint.setWordWrap(True)

        qq_layout.addWidget(qq_mode_hint)

        self.gateway_napcat_enabled = QtWidgets.QCheckBox("启用 NapCat / QQ 接入")

        self.gateway_napcat_enabled.setChecked(bool(state["napcat_enabled"]))

        qq_layout.addWidget(self.gateway_napcat_enabled)

        form = QtWidgets.QFormLayout()

        form.setLabelAlignment(QtCore.Qt.AlignmentFlag.AlignRight)

        form.setFormAlignment(QtCore.Qt.AlignmentFlag.AlignTop)

        form.setHorizontalSpacing(14)

        form.setVerticalSpacing(10)

        self.gateway_webhook_host = QtWidgets.QLineEdit(state["napcat_webhook_host"])

        self.gateway_webhook_port = QtWidgets.QSpinBox()

        self.gateway_webhook_port.setRange(1, 65535)

        self.gateway_webhook_port.setValue(int(state["napcat_webhook_port"]))

        self.gateway_webhook_path = QtWidgets.QLineEdit(state["napcat_webhook_path"])

        self.gateway_access_token = QtWidgets.QLineEdit(state["napcat_access_token"])

        self.gateway_access_token.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)

        self.gateway_api_base = QtWidgets.QLineEdit(state["napcat_api_base"])

        self.gateway_api_token = QtWidgets.QLineEdit(state["napcat_api_token"])

        self.gateway_api_token.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)

        self.gateway_owner_ids = QtWidgets.QLineEdit(
            self._format_owner_ids(state["napcat_owner_user_ids"])
        )

        self.gateway_owner_ids.setPlaceholderText("填你的 QQ 号；多个用逗号或空格分隔")

        self.gateway_owner_label = QtWidgets.QLineEdit(state["napcat_owner_label"])

        self.gateway_owner_label.setPlaceholderText("主人")

        self.gateway_image_prompt = QtWidgets.QPlainTextEdit(
            state["napcat_image_prompt"]
        )

        self.gateway_image_prompt.setFixedHeight(82)

        self.gateway_filter_mode = QtWidgets.QComboBox()

        self.gateway_filter_mode.addItem("不过滤", "off")

        self.gateway_filter_mode.addItem("白名单", "whitelist")

        self.gateway_filter_mode.addItem("黑名单", "blacklist")

        filter_index = self.gateway_filter_mode.findData(state["napcat_filter_mode"])

        self.gateway_filter_mode.setCurrentIndex(
            filter_index if filter_index >= 0 else 0
        )

        self.gateway_user_whitelist = QtWidgets.QLineEdit(
            self._format_owner_ids(state["napcat_user_whitelist"])
        )

        self.gateway_user_whitelist.setPlaceholderText(
            "允许通过的 QQ 号；多个用逗号或空格分隔"
        )

        self.gateway_user_blacklist = QtWidgets.QLineEdit(
            self._format_owner_ids(state["napcat_user_blacklist"])
        )

        self.gateway_user_blacklist.setPlaceholderText(
            "拒绝通过的 QQ 号；多个用逗号或空格分隔"
        )

        self.gateway_group_whitelist = QtWidgets.QLineEdit(
            self._format_owner_ids(state["napcat_group_whitelist"])
        )

        self.gateway_group_whitelist.setPlaceholderText(
            "允许通过的群号；多个用逗号或空格分隔"
        )

        self.gateway_group_blacklist = QtWidgets.QLineEdit(
            self._format_owner_ids(state["napcat_group_blacklist"])
        )

        self.gateway_group_blacklist.setPlaceholderText(
            "拒绝通过的群号；多个用逗号或空格分隔"
        )

        self.gateway_reply_enabled = QtWidgets.QCheckBox("允许副号回发消息")

        self.gateway_reply_enabled.setChecked(bool(state["napcat_reply_enabled"]))

        self.gateway_allow_private = QtWidgets.QCheckBox("允许私聊")

        self.gateway_allow_private.setChecked(bool(state["napcat_allow_private"]))

        self.gateway_allow_group = QtWidgets.QCheckBox("允许群聊")

        self.gateway_allow_group.setChecked(bool(state["napcat_allow_group"]))

        self.gateway_group_require_at = QtWidgets.QCheckBox("群聊仅响应 @我")

        self.gateway_group_require_at.setChecked(bool(state["napcat_group_require_at"]))

        self.gateway_image_vision_enabled = QtWidgets.QCheckBox("启用 QQ 图片识别")

        self.gateway_image_vision_enabled.setChecked(
            bool(state["napcat_image_vision_enabled"])
        )

        self.gateway_voice_reply_enabled = QtWidgets.QCheckBox(
            "启用 QQ 概率语音回复（命中时优先发语音，失败回退文字）"
        )

        self.gateway_voice_reply_enabled.setChecked(
            bool(state["napcat_voice_reply_enabled"])
        )

        self.gateway_ui_append = QtWidgets.QCheckBox("QQ 回复同步显示到本地聊天框")

        self.gateway_ui_append.setChecked(bool(state["remote_chat_ui_append"]))

        self.gateway_voice_reply_probability = QtWidgets.QSpinBox()

        self.gateway_voice_reply_probability.setRange(0, 100)

        self.gateway_voice_reply_probability.setSuffix(" %")

        self.gateway_voice_reply_probability.setValue(
            int(state["napcat_voice_reply_probability"])
        )

        self.gateway_voice_reply_probability.setEnabled(
            self.gateway_voice_reply_enabled.isChecked()
        )

        form.addRow("Webhook Host", self.gateway_webhook_host)

        form.addRow("Webhook Port", self.gateway_webhook_port)

        form.addRow("Webhook Path", self.gateway_webhook_path)

        form.addRow("Access Token", self.gateway_access_token)

        form.addRow("NapCat API Base", self.gateway_api_base)

        form.addRow("NapCat API Token", self.gateway_api_token)

        form.addRow("主人 QQ", self.gateway_owner_ids)

        form.addRow("主人称呼", self.gateway_owner_label)

        form.addRow("语音概率", self.gateway_voice_reply_probability)

        form.addRow("QQ 过滤模式", self.gateway_filter_mode)

        form.addRow("用户白名单", self.gateway_user_whitelist)

        form.addRow("用户黑名单", self.gateway_user_blacklist)

        form.addRow("群白名单", self.gateway_group_whitelist)

        form.addRow("群黑名单", self.gateway_group_blacklist)

        form.addRow("图片提示词", self.gateway_image_prompt)

        toggles = QtWidgets.QVBoxLayout()

        toggles.setContentsMargins(0, 0, 0, 0)

        toggles.setSpacing(6)

        toggles.addWidget(self.gateway_reply_enabled)

        toggles.addWidget(self.gateway_allow_private)

        toggles.addWidget(self.gateway_allow_group)

        toggles.addWidget(self.gateway_group_require_at)

        toggles.addWidget(self.gateway_image_vision_enabled)

        toggles.addWidget(self.gateway_voice_reply_enabled)

        toggles.addWidget(self.gateway_ui_append)

        toggle_widget = QtWidgets.QWidget()

        toggle_widget.setLayout(toggles)

        form.addRow("消息策略", toggle_widget)

        qq_layout.addLayout(form)

        self.gateway_url_preview = QtWidgets.QLabel("")

        self.gateway_url_preview.setObjectName("launchDesc")

        self.gateway_url_preview.setWordWrap(True)

        qq_layout.addWidget(self.gateway_url_preview)

        footer = QtWidgets.QHBoxLayout()

        btn_copy = QtWidgets.QPushButton("复制 Webhook 地址")

        btn_copy.clicked.connect(self._copy_gateway_webhook_url)

        footer.addWidget(btn_copy)

        btn_save = QtWidgets.QPushButton("保存")

        btn_save.setObjectName("primaryAction")

        btn_save.clicked.connect(self._save_gateway_settings)

        footer.addWidget(btn_save)

        btn_save_restart = QtWidgets.QPushButton("保存并重启")

        btn_save_restart.clicked.connect(
            lambda checked=False: self._save_gateway_settings(restart_after_save=True)
        )

        btn_save_restart.setEnabled(
            bool(getattr(self.main_app, "on_restart_callback", None))
        )

        footer.addWidget(btn_save_restart)

        footer.addStretch()

        qq_layout.addLayout(footer)

        layout.addWidget(qq_card)

        qq_profile_card = QtWidgets.QFrame()

        qq_profile_card.setObjectName("launchCard")

        qq_profile_layout = QtWidgets.QVBoxLayout(qq_profile_card)

        qq_profile_layout.setContentsMargins(20, 18, 20, 18)

        qq_profile_layout.setSpacing(10)

        qq_profile_title = QtWidgets.QLabel("QQ 联系人档案")

        qq_profile_title.setObjectName("launchTitle")

        qq_profile_layout.addWidget(qq_profile_title)

        qq_profile_desc = QtWidgets.QLabel(
            "这里维护不同 QQ 用户的身份、关系、权限和记忆范围，方便她区分“你是谁”和“该共享哪段记忆”。"
        )

        qq_profile_desc.setObjectName("launchDesc")

        qq_profile_desc.setWordWrap(True)

        qq_profile_layout.addWidget(qq_profile_desc)

        qq_profile_toolbar = QtWidgets.QHBoxLayout()

        btn_refresh_profiles = QtWidgets.QPushButton("刷新")

        btn_refresh_profiles.clicked.connect(self._refresh_gateway_qq_profile_table)

        qq_profile_toolbar.addWidget(btn_refresh_profiles)

        btn_add_profile = QtWidgets.QPushButton("新增")

        btn_add_profile.clicked.connect(self._add_gateway_qq_profile)

        qq_profile_toolbar.addWidget(btn_add_profile)

        self.gateway_qq_profile_edit_btn = QtWidgets.QPushButton("编辑")

        self.gateway_qq_profile_edit_btn.clicked.connect(
            lambda checked=False: self._edit_gateway_qq_profile()
        )

        qq_profile_toolbar.addWidget(self.gateway_qq_profile_edit_btn)

        self.gateway_qq_profile_remove_btn = QtWidgets.QPushButton("删除")

        self.gateway_qq_profile_remove_btn.clicked.connect(
            lambda checked=False: self._delete_gateway_qq_profile()
        )

        qq_profile_toolbar.addWidget(self.gateway_qq_profile_remove_btn)

        qq_profile_toolbar.addStretch()

        qq_profile_layout.addLayout(qq_profile_toolbar)

        self.gateway_qq_profile_table = QtWidgets.QTableWidget()

        self.gateway_qq_profile_table.setColumnCount(8)

        self.gateway_qq_profile_table.setHorizontalHeaderLabels(
            ["主人", "QQ", "备注", "昵称", "关系", "权限", "身份摘要", "记忆范围"]
        )

        self.gateway_qq_profile_table.setSelectionBehavior(
            QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows
        )

        self.gateway_qq_profile_table.setSelectionMode(
            QtWidgets.QAbstractItemView.SelectionMode.SingleSelection
        )

        self.gateway_qq_profile_table.setEditTriggers(
            QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers
        )

        self.gateway_qq_profile_table.verticalHeader().setVisible(False)

        self.gateway_qq_profile_table.verticalHeader().setDefaultSectionSize(36)

        self.gateway_qq_profile_table.setAlternatingRowColors(True)

        self.gateway_qq_profile_table.setMinimumHeight(0)

        self.gateway_qq_profile_table.horizontalHeader().setStretchLastSection(False)

        self.gateway_qq_profile_table.horizontalHeader().setSectionResizeMode(
            0, QtWidgets.QHeaderView.ResizeMode.ResizeToContents
        )

        self.gateway_qq_profile_table.horizontalHeader().setSectionResizeMode(
            1, QtWidgets.QHeaderView.ResizeMode.ResizeToContents
        )

        self.gateway_qq_profile_table.horizontalHeader().setSectionResizeMode(
            2, QtWidgets.QHeaderView.ResizeMode.ResizeToContents
        )

        self.gateway_qq_profile_table.horizontalHeader().setSectionResizeMode(
            3, QtWidgets.QHeaderView.ResizeMode.ResizeToContents
        )

        self.gateway_qq_profile_table.horizontalHeader().setSectionResizeMode(
            4, QtWidgets.QHeaderView.ResizeMode.ResizeToContents
        )

        self.gateway_qq_profile_table.horizontalHeader().setSectionResizeMode(
            5, QtWidgets.QHeaderView.ResizeMode.ResizeToContents
        )

        self.gateway_qq_profile_table.horizontalHeader().setSectionResizeMode(
            6, QtWidgets.QHeaderView.ResizeMode.Stretch
        )

        self.gateway_qq_profile_table.horizontalHeader().setSectionResizeMode(
            7, QtWidgets.QHeaderView.ResizeMode.Fixed
        )
        self.gateway_qq_profile_table.setColumnWidth(7, 124)

        self.gateway_qq_profile_table.itemSelectionChanged.connect(
            self._update_gateway_qq_profile_actions
        )

        self.gateway_qq_profile_table.itemDoubleClicked.connect(
            lambda item: self._edit_gateway_qq_profile()
        )

        qq_profile_layout.addWidget(self.gateway_qq_profile_table)

        self.gateway_qq_profile_preview = QtWidgets.QLabel("")

        self.gateway_qq_profile_preview.setObjectName("launchDesc")

        self.gateway_qq_profile_preview.setWordWrap(True)

        qq_profile_layout.addWidget(self.gateway_qq_profile_preview)

        layout.addWidget(qq_profile_card)

        self._refresh_gateway_qq_profile_table()

        layout.addStretch()

        self.gateway_webhook_host.textChanged.connect(self._refresh_gateway_preview)

        self.gateway_webhook_port.valueChanged.connect(self._refresh_gateway_preview)

        self.gateway_webhook_path.textChanged.connect(self._refresh_gateway_preview)

        self.gateway_allow_group.toggled.connect(self._refresh_gateway_preview)

        self.gateway_filter_mode.currentIndexChanged.connect(
            self._refresh_gateway_preview
        )

        self.gateway_voice_reply_enabled.toggled.connect(
            self.gateway_voice_reply_probability.setEnabled
        )

        self.gateway_voice_reply_enabled.toggled.connect(self._refresh_gateway_preview)

        self.gateway_voice_reply_probability.valueChanged.connect(
            self._refresh_gateway_preview
        )

        self._refresh_gateway_preview()

        root.addWidget(self._wrap_in_scroll_area(container), 1)

        self.stack.addWidget(page)

    # ---------- Other tabs ----------

    def _wrap_in_scroll_area(self, widget: QtWidgets.QWidget) -> QtWidgets.QScrollArea:
        return wrap_in_scroll_area(widget)

    def _init_costume_page(self):
        from modules.gui.dialogs.character_editor import CharacterEditorWidget

        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        widget = CharacterEditorWidget(self.main_app)
        apply_embedded_mode(widget)
        layout.addWidget(self._wrap_in_scroll_area(widget), 1)
        self.stack.addWidget(page)

    def _init_plugin_page(self):
        from modules.gui.dialogs.plugin_manager import PluginManagerDialog

        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        plugin_manager = (
            getattr(self.main_app, "plugin_manager", None) if self.main_app else None
        )
        if plugin_manager is None and self.main_app is not None:
            # 遥控窗远程模式：大脑的插件管理器不在本进程，走 /gui HTTP。
            from modules.gui.remote_plugin_manager import RemotePluginManager

            plugin_manager = RemotePluginManager()
        if plugin_manager is None:
            layout.addWidget(QtWidgets.QLabel("当前上下文还不能管理能力。"))
        else:
            widget = PluginManagerDialog(
                parent=page,
                plugin_manager=plugin_manager,
                main_app=self.main_app,
                embedded=True,
            )
            self._plugin_page = widget
            apply_embedded_mode(widget)
            # 插件页以表格为主，直接填满内容区，不要外层 scroll 吃掉表格高度。
            widget.setSizePolicy(
                QtWidgets.QSizePolicy.Policy.Expanding,
                QtWidgets.QSizePolicy.Policy.Expanding,
            )
            layout.addWidget(widget, 1)
        self.stack.addWidget(page)

    def _add_embedded_dialog_page(self, dialog_cls, label: str):
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        try:
            widget = dialog_cls(parent=page, main_app=self.main_app)
            apply_embedded_mode(widget)
            layout.addWidget(self._wrap_in_scroll_area(widget), 1)
        except Exception as exc:
            fallback = QtWidgets.QLabel(f"{label}加载失败：{exc}")
            fallback.setWordWrap(True)
            layout.addWidget(fallback)
        self.stack.addWidget(page)

    def _init_knowledge_page(self):
        from modules.gui.dialogs.knowledge_manager import KnowledgeManagerDialog

        self._add_embedded_dialog_page(KnowledgeManagerDialog, "知识库")

    def _init_expression_library_page(self):
        from modules.gui.dialogs.expression_library_manager import (
            ExpressionLibraryManagerDialog,
        )

        self._add_embedded_dialog_page(ExpressionLibraryManagerDialog, "表达学习库")

    def _init_meme_page(self):
        from modules.gui.dialogs.meme_manager import MemeManagerDialog

        self._add_embedded_dialog_page(MemeManagerDialog, "表情包库")

    def _init_diary_page(self):
        from modules.gui.dialogs.diary_manager import DiaryManagerDialog

        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        widget = DiaryManagerDialog(parent=page, embedded=True)
        apply_embedded_mode(widget)
        layout.addWidget(self._wrap_in_scroll_area(widget), 1)
        self.stack.addWidget(page)

    def _init_memory_page(self):
        from modules.gui.dialogs.memory_editor import MemoryEditorDialog

        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        from services.gui_api.memory_service import MemoryGuiService

        brain = getattr(self.main_app, "brain", None)
        live_core = getattr(brain, "memory_core", None) if brain is not None else None
        widget = MemoryEditorDialog(
            parent=page,
            embedded=True,
            brain=brain,
            memory_core=live_core,
            memory_gui=MemoryGuiService(memory_core=live_core, brain=brain),
        )
        apply_embedded_mode(widget)
        layout.addWidget(self._wrap_in_scroll_area(widget), 1)
        self.stack.addWidget(page)

    def _init_status_screen_page(self):
        from modules.gui.dialogs.status_screen_manager import StatusScreenManagerDialog

        self._add_embedded_dialog_page(StatusScreenManagerDialog, "状态屏")

    def _init_runtime_health_page(self):
        from modules.gui.dialogs.runtime_health import RuntimeHealthDialog

        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        health_center = (
            getattr(self.main_app, "runtime_health", None)
            if self.main_app is not None
            else None
        )
        if health_center is None:
            try:
                from services.runtime_health import get_runtime_health

                health_center = get_runtime_health()
            except Exception:
                health_center = None
        widget = RuntimeHealthDialog(health_center, parent=page, embedded=True)
        apply_embedded_mode(widget)
        layout.addWidget(widget, 1)
        self.stack.addWidget(page)

    def _init_screen_app_rules_page(self):
        from modules.gui.dialogs.screen_app_rules import ScreenAppRulesDialog

        self._add_embedded_dialog_page(ScreenAppRulesDialog, "应用识别")
