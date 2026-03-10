import json

import os

import re



from PySide6 import QtCore, QtGui, QtWidgets



from modules.gui.dialogs.character_editor import CharacterEditorWidget

from modules.gui.dialogs.plugin_manager import PluginManagerDialog

from modules.gui.dialogs.memory_editor import MemoryEditorDialog

from modules.gui.styles import DEFAULT_CONSOLE, UI_PALETTE, get_settings_styles, get_ui_palette



try:

    from modules.runtime_settings import load_runtime_settings, update_runtime_settings

except Exception:

    def load_runtime_settings():

        return {}



    def update_runtime_settings(patch):

        return patch or {}



try:

    from modules.dependency_check import (

        build_install_command,

        install_missing,

        scan_missing_dependencies,

    )

except Exception:

    def scan_missing_dependencies(*args, **kwargs):

        return []



    def build_install_command(rows):

        return ""



    def install_missing(rows, timeout=600):

        return {"ok": "0", "message": "dependency_check unavailable"}



try:

    from config import (

        COSTUME_MAP,

        CUSTOM_MODELS_PATH,

        LLM_ROUTER,

        MCP_ENABLED,

        MCP_SERVER_CONFIGS,

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

    MCP_ENABLED = True

    MCP_SERVER_CONFIGS = []

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

        if provider_name:

            self.inp_name.setReadOnly(True)

            self.inp_name.setStyleSheet("background: #F3F4F6; color: #666;")

        form.addRow("提供商名称:", self.inp_name)



        self.inp_url = QtWidgets.QLineEdit(str(data.get("base_url", "")))

        self.inp_url.setPlaceholderText("https://api.example.com/v1")

        form.addRow("Base URL:", self.inp_url)



        key_layout = QtWidgets.QHBoxLayout()

        self.inp_key = QtWidgets.QLineEdit(str(data.get("api_key", "")))

        self.inp_key.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)

        self.inp_key.setPlaceholderText("sk-...")

        btn_eye = QtWidgets.QToolButton()

        btn_eye.setText("显示")

        btn_eye.pressed.connect(lambda: self.inp_key.setEchoMode(QtWidgets.QLineEdit.EchoMode.Normal))

        btn_eye.released.connect(lambda: self.inp_key.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password))

        key_layout.addWidget(self.inp_key)

        key_layout.addWidget(btn_eye)

        form.addRow("API Key:", key_layout)



        layout.addLayout(form)



        footer = QtWidgets.QHBoxLayout()

        footer.addStretch()

        btn_save = QtWidgets.QPushButton("保存")

        btn_save.clicked.connect(self._on_save)

        footer.addWidget(btn_save)

        layout.addLayout(footer)



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



    def __init__(self, parent=None, model_id="", model_data=None, on_provider_saved=None):

        super().__init__(parent)

        self.setWindowTitle("编辑模型" if model_id else "添加模型")

        self.setFixedWidth(520)

        self.result_data = None

        self._original = dict(model_data or {})

        self._on_provider_saved = on_provider_saved



        layout = QtWidgets.QVBoxLayout(self)

        form = QtWidgets.QFormLayout()



        self.combo_provider = QtWidgets.QComboBox()

        self._reload_provider_combo()

        self.combo_provider.currentIndexChanged.connect(self._on_provider_select)



        provider_row = QtWidgets.QWidget()

        provider_layout = QtWidgets.QHBoxLayout(provider_row)

        provider_layout.setContentsMargins(0, 0, 0, 0)

        provider_layout.addWidget(self.combo_provider, 1)

        btn_add_provider = QtWidgets.QPushButton("+ 新增提供商")

        btn_add_provider.clicked.connect(self._on_add_provider_inline)

        provider_layout.addWidget(btn_add_provider)

        form.addRow("快速套用提供商:", provider_row)



        self.inp_id = QtWidgets.QLineEdit(model_id)

        self.inp_id.setPlaceholderText("my-gpt-4")

        if model_id:

            self.inp_id.setReadOnly(True)

            self.inp_id.setStyleSheet("background: #F3F4F6; color: #666;")

        form.addRow("模型 ID:", self.inp_id)



        self.inp_model = QtWidgets.QLineEdit(str(self._original.get("model", "")))

        self.inp_model.setPlaceholderText("gpt-4o / gemini-3-flash-preview ...")

        form.addRow("上游模型名:", self.inp_model)



        self.inp_url = QtWidgets.QLineEdit(str(self._original.get("base_url", "")))

        self.inp_url.setPlaceholderText("https://api.example.com/v1")

        form.addRow("Base URL:", self.inp_url)



        self.inp_key = QtWidgets.QLineEdit(str(self._original.get("api_key", "")))

        self.inp_key.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)

        self.inp_key.setPlaceholderText("sk-...")

        form.addRow("API Key:", self.inp_key)



        self.combo_style = QtWidgets.QComboBox()

        self.combo_style.setEditable(True)

        for x in self.API_STYLE_OPTIONS:

            self.combo_style.addItem(x)

        style_text = str(self._original.get("api_style", "") or "")

        self.combo_style.setCurrentText(style_text)

        form.addRow("api_style:", self.combo_style)



        layout.addLayout(form)



        tips = QtWidgets.QLabel(

            '提示：遇到 “/v1/chat/completions 不支持” 的网关，可设 `api_style = "responses"`。'

        )

        tips.setWordWrap(True)

        tips.setStyleSheet("color:#6B7280;")

        layout.addWidget(tips)



        footer = QtWidgets.QHBoxLayout()

        footer.addStretch()

        btn_save = QtWidgets.QPushButton("保存")

        btn_save.clicked.connect(self._on_save)

        footer.addWidget(btn_save)

        layout.addLayout(footer)



    def _reload_provider_combo(self):

        self.combo_provider.clear()

        self.combo_provider.addItem("手动输入（不套用提供商）", None)

        for name, p_data in PROVIDERS.items():

            self.combo_provider.addItem(name, p_data)



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

        self._reload_provider_combo()

        idx = self.combo_provider.findText(name)

        if idx >= 0:

            self.combo_provider.setCurrentIndex(idx)

            self._on_provider_select()



    def _on_provider_select(self):

        data = self.combo_provider.currentData()

        if not isinstance(data, dict):

            return

        self.inp_url.setText(str(data.get("base_url", "")))

        self.inp_key.setText(str(data.get("api_key", "")))



    def _on_save(self):

        mid = self.inp_id.text().strip()

        if not mid:

            QtWidgets.QMessageBox.warning(self, "校验失败", "模型 ID 不能为空。")

            return



        merged = dict(self._original)

        merged["model"] = self.inp_model.text().strip()

        merged["base_url"] = self.inp_url.text().strip()

        merged["api_key"] = self.inp_key.text().strip()



        style = self.combo_style.currentText().strip()

        if style:

            merged["api_style"] = style

        else:

            merged.pop("api_style", None)



        self.result_data = {"id": mid, "config": merged}

        self.accept()





class RouterConfigDialog(QtWidgets.QDialog):

    def __init__(self, parent=None, task_name="", current_chain=None):

        super().__init__(parent)

        self.setWindowTitle(f"路由配置: {task_name}")

        self.resize(760, 520)

        self.result_chain = list(current_chain or [])

        self.all_models = sorted(list(MODELS.keys()))

        self._init_ui()



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



    def _refresh(self):

        self.list_chain.clear()

        for m in self.result_chain:

            name = MODELS.get(m, {}).get("model", "???")

            item = QtWidgets.QListWidgetItem(f"{m} ({name})")

            item.setData(QtCore.Qt.ItemDataRole.UserRole, m)

            self.list_chain.addItem(item)



        self.list_pool.clear()

        for m in self.all_models:

            if m in self.result_chain:

                continue

            name = MODELS.get(m, {}).get("model", "???")

            item = QtWidgets.QListWidgetItem(f"{m} ({name})")

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

        self.result_chain[row], self.result_chain[row - 1] = self.result_chain[row - 1], self.result_chain[row]

        self._refresh()

        self.list_chain.setCurrentRow(row - 1)



    def _down(self):

        row = self.list_chain.currentRow()

        if row < 0 or row >= len(self.result_chain) - 1:

            return

        self.result_chain[row], self.result_chain[row + 1] = self.result_chain[row + 1], self.result_chain[row]

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

        transport = str(self._original.get("transport") or default_transport or "stdio").strip().lower().replace("-", "_")

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



        self.inp_args = QtWidgets.QPlainTextEdit("\n".join(self._normalize_list(self._original.get("args", []))))

        self.inp_args.setPlaceholderText("每行一个参数，例如：\n-m\nyour_mcp_server")

        self.inp_args.setFixedHeight(86)

        stdio_form.addRow("启动参数", self.inp_args)



        self.inp_cwd = QtWidgets.QLineEdit(str(self._original.get("cwd", "")))

        self.inp_cwd.setPlaceholderText("可选：工作目录")

        stdio_form.addRow("工作目录", self.inp_cwd)



        self.inp_env = QtWidgets.QPlainTextEdit(self._format_mapping(self._original.get("env", {}), pair_sep="="))

        self.inp_env.setPlaceholderText("每行一个环境变量，例如：\nAPI_KEY=xxx\nMODE=prod")

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



        self.inp_headers = QtWidgets.QPlainTextEdit(self._format_mapping(self._original.get("headers", {}), pair_sep=": "))

        self.inp_headers.setPlaceholderText("每行一个请求头，例如：\nAuthorization: Bearer xxx")

        self.inp_headers.setFixedHeight(86)

        http_form.addRow("请求头", self.inp_headers)



        layout.addWidget(self.http_group)



        timeout_row = QtWidgets.QHBoxLayout()

        self.spin_timeout = QtWidgets.QDoubleSpinBox()

        self.spin_timeout.setRange(1.0, 600.0)

        self.spin_timeout.setDecimals(1)

        self.spin_timeout.setSingleStep(1.0)

        self.spin_timeout.setValue(float(self._original.get("timeout_sec", 20.0) or 20.0))

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

        self.spin_sse_timeout.setValue(float(self._original.get("sse_read_timeout_sec", 300.0) or 300.0))

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

        transport = str(self.combo_transport.currentData() or "stdio").strip().lower() or "stdio"

        is_stdio = transport == "stdio"

        self.stdio_group.setVisible(is_stdio)

        self.http_group.setVisible(not is_stdio)

        self.spin_sse_timeout.setEnabled(not is_stdio)

        if is_stdio:

            self.tip_label.setText("适合本机直接拉起 MCP 进程，例如 `python -m your_mcp_server`。启动参数一行一个，不用手写 JSON。")

        else:

            self.tip_label.setText("适合连接现成的远程 MCP HTTP 服务。请求头一行一个，格式如 `Authorization: Bearer xxx`。")



    def _on_save(self):

        name = self.inp_name.text().strip()

        if not name:

            QtWidgets.QMessageBox.warning(self, "MCP 服务器", "服务器名称不能为空。")

            return



        transport = str(self.combo_transport.currentData() or "stdio").strip().lower() or "stdio"

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

                headers = self._parse_mapping(self.inp_headers.toPlainText(), default_sep=":")

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



        display_name = str(self._original.get("remark_name") or self._original.get("nickname") or "").strip() or "尚未记录昵称或备注"

        self.lbl_display_name = QtWidgets.QLabel(display_name)

        self.lbl_display_name.setWordWrap(True)

        form.addRow("当前显示名", self.lbl_display_name)



        relation_map = {

            "owner": "主人",

            "contact": "熟人联系人",

            "group_member": "群成员",

        }

        relation_text = relation_map.get(str(self._original.get("relationship_to_owner") or "").strip(), "未识别")

        self.lbl_relation = QtWidgets.QLabel(relation_text)

        self.lbl_relation.setWordWrap(True)

        form.addRow("与主人的关系", self.lbl_relation)



        scope_map = {

            "owner_shared": "与主人共享",

            "private": "私聊独立",

            "group_shared": "群内共享",

        }

        scope_text = scope_map.get(str(self._original.get("memory_scope") or "").strip(), "未设置")

        self.lbl_memory_scope = QtWidgets.QLabel(scope_text)

        self.lbl_memory_scope.setWordWrap(True)

        form.addRow("记忆范围", self.lbl_memory_scope)



        self.combo_permission = QtWidgets.QComboBox()

        for label, value in self.PERMISSION_OPTIONS:

            self.combo_permission.addItem(label, value)

        current_permission = str(self._original.get("permission_level") or "default").strip() or "default"

        idx = self.combo_permission.findData(current_permission)

        self.combo_permission.setCurrentIndex(idx if idx >= 0 else 0)

        form.addRow("权限等级", self.combo_permission)



        self.txt_identity_summary = QtWidgets.QPlainTextEdit(str(self._original.get("identity_summary") or ""))

        self.txt_identity_summary.setPlaceholderText("例如：主人的朋友，偶尔聊游戏和桌面助手，回复可以自然一点。")

        self.txt_identity_summary.setMinimumHeight(110)

        form.addRow("身份摘要", self.txt_identity_summary)



        self.txt_notes = QtWidgets.QPlainTextEdit(str(self._original.get("notes") or ""))

        self.txt_notes.setPlaceholderText("补充细节，例如称呼偏好、禁忌话题、是否允许远程操作等。")

        self.txt_notes.setMinimumHeight(120)

        form.addRow("备注", self.txt_notes)



        layout.addLayout(form)



        tip = QtWidgets.QLabel("这里编辑的是“这个 QQ 用户是谁”的联系人画像，不会修改她本体的人设。后续收到同一 QQ 的新消息时，系统会沿用这里的资料。")

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

    def __init__(self, parent=None, main_app=None):

        super().__init__(parent)

        self.setWindowFlags(QtCore.Qt.WindowType.Window | QtCore.Qt.WindowType.WindowMinMaxButtonsHint | QtCore.Qt.WindowType.WindowCloseButtonHint)

        self.setWindowFlag(QtCore.Qt.WindowType.WindowStaysOnTopHint, False)

        self.setWindowFlag(QtCore.Qt.WindowType.Tool, False)

        self.main_app = main_app

        self.setWindowTitle("系统设置中心")

        if main_app is not None and hasattr(main_app, "_icon"):

            self.setWindowIcon(main_app._icon)

        self.resize(1040, 760)

        self.setMinimumSize(920, 680)

        self.setSizeGripEnabled(True)

        self.setStyleSheet(get_settings_styles())



        self._tab_meta = [
            {
                "nav": "🤖 常用 · 模型与路由",
                "title": "模型与路由",
                "desc": "配置模型、API 地址与各类任务的调用链，是最常用的基础设置区。",
            },
            {
                "nav": "🌐 常用 · 提供商",
                "title": "提供商",
                "desc": "集中管理 Base URL 和 API Key，减少手动重复填写。",
            },
            {
                "nav": "🎭 常用 · 形象管理",
                "title": "形象管理",
                "desc": "维护角色人设、提示词、服装与表情动作映射。",
            },
            {
                "nav": "🎨 常用 · 颜色主题",
                "title": "颜色主题",
                "desc": "自定义界面配色与控制台颜色，修改后即时生效。",
            },
            {
                "nav": "🧩 高级 · 插件工具",
                "title": "插件工具",
                "desc": "进入插件管理器，查看启停状态、兼容入口和插件配置。",
            },
            {
                "nav": "🧠 高级 · 记忆数据",
                "title": "记忆数据",
                "desc": "打开重型记忆编辑器，适合排查 transcript、todo、图记忆等底层数据。",
            },
            {
                "nav": "🩺 高级 · 依赖体检",
                "title": "依赖体检",
                "desc": "扫描插件缺失依赖并生成安装命令，适合排查插件为什么不能正常工作。",
            },
            {
                "nav": "🌐 常用 · MCP",
                "title": "MCP",
                "desc": "集中管理本地 MCP Bridge、远程 MCP 服务器，以及 MCP 是否允许被 QQ 侧触发。",
            },
            {
                "nav": "🔌 常用 · QQ 接入",
                "title": "QQ 接入",
                "desc": "集中管理 NapCat、QQ 消息接入、黑白名单、图片识别、语音回复和联系人档案。",
            },
        ]
        main = QtWidgets.QHBoxLayout(self)

        main.setContentsMargins(16, 16, 16, 16)

        main.setSpacing(16)



        nav_card = QtWidgets.QFrame()

        nav_card.setObjectName("settingsNavCard")

        nav_layout = QtWidgets.QVBoxLayout(nav_card)

        nav_layout.setContentsMargins(14, 14, 14, 14)

        nav_layout.setSpacing(10)



        nav_title = QtWidgets.QLabel("设置导航")

        nav_title.setObjectName("settingsNavTitle")

        nav_layout.addWidget(nav_title)



        nav_hint = QtWidgets.QLabel("把常用配置和高级工具分开，减少页面跳来跳去的负担。")

        nav_hint.setObjectName("settingsNavHint")

        nav_hint.setWordWrap(True)

        nav_layout.addWidget(nav_hint)



        self.nav_list = QtWidgets.QListWidget()

        self.nav_list.setObjectName("settingsNav")

        self.nav_list.setFixedWidth(220)

        for meta in self._tab_meta:

            item = QtWidgets.QListWidgetItem(meta["nav"])

            item.setToolTip(meta["desc"])

            self.nav_list.addItem(item)

        self.nav_list.currentRowChanged.connect(self._on_tab_changed)

        nav_layout.addWidget(self.nav_list, 1)

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



        self._safe_init_page(self._init_llm_page, "模型与路由")

        self._safe_init_page(self._init_provider_page, "提供商")

        self._safe_init_page(self._init_costume_page, "形象管理")

        self._safe_init_page(self._init_color_page, "颜色主题")


        self._safe_init_page(self._init_plugin_page, "插件工具")

        self._safe_init_page(self._init_memory_page, "记忆数据")

        self._safe_init_page(self._init_dependency_page, "依赖体检")

        self._safe_init_page(self._init_mcp_page, "MCP")

        self._safe_init_page(self._init_gateway_page, "QQ 接入")



        self.nav_list.setCurrentRow(0)

        self._refresh_page_header(0)



    def _on_tab_changed(self, row):

        if 0 <= row < self.stack.count():

            self.stack.setCurrentIndex(row)

            self._refresh_page_header(row)



    def open_page(self, row: int):

        if 0 <= row < self.nav_list.count():

            self.nav_list.setCurrentRow(row)

            self.show()

            self.raise_()

            self.activateWindow()



    def _refresh_page_header(self, row: int):

        if not (0 <= row < len(self._tab_meta)):

            return

        meta = self._tab_meta[row]

        self.page_title.setText(meta["title"])

        self.page_desc.setText(meta["desc"])



    def _create_launch_pad_page(self, title: str, desc: str, button_text: str, callback, *, enabled: bool = True, disabled_tip: str = ""):

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



    # ---------- Provider ----------

    def _init_provider_page(self):

        page = QtWidgets.QWidget()

        layout = QtWidgets.QVBoxLayout(page)



        top = QtWidgets.QHBoxLayout()

        top.addWidget(QtWidgets.QLabel("提供商配置 (Providers)", objectName="header"))

        btn_add = QtWidgets.QPushButton("+ 添加提供商")

        btn_add.clicked.connect(self._on_add_provider)

        top.addWidget(btn_add)

        layout.addLayout(top)



        self.prov_table = QtWidgets.QTableWidget()

        self.prov_table.setColumnCount(3)

        self.prov_table.setHorizontalHeaderLabels(["名称", "Base URL", "操作"])

        self.prov_table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.Stretch)

        self.prov_table.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)

        self.prov_table.verticalHeader().setVisible(False)

        layout.addWidget(self.prov_table)



        self.stack.addWidget(page)

        self._refresh_prov_table()



    def _refresh_prov_table(self):

        self.prov_table.setRowCount(0)

        for name, data in PROVIDERS.items():

            row = self.prov_table.rowCount()

            self.prov_table.insertRow(row)

            self.prov_table.setItem(row, 0, QtWidgets.QTableWidgetItem(name))

            self.prov_table.setItem(row, 1, QtWidgets.QTableWidgetItem(str((data or {}).get("base_url", ""))))



            action_widget = QtWidgets.QWidget()

            h = QtWidgets.QHBoxLayout(action_widget)

            h.setContentsMargins(4, 4, 4, 4)

            h.setSpacing(8)

            btn_edit = QtWidgets.QPushButton("编辑")

            btn_edit.clicked.connect(lambda c=False, n=name: self._on_edit_provider(n))

            btn_del = QtWidgets.QPushButton("删除")

            btn_edit.setObjectName("tableActionBtn")

            btn_del.setObjectName("tableDangerBtn")

            btn_del.clicked.connect(lambda c=False, n=name: self._on_del_provider(n))

            h.addWidget(btn_edit)

            h.addWidget(btn_del)

            h.addStretch()

            self.prov_table.setCellWidget(row, 2, action_widget)



    def _on_add_provider(self):

        dlg = ProviderEditDialog(self)

        if dlg.exec() == QtWidgets.QDialog.DialogCode.Accepted:

            d = dlg.result_data

            PROVIDERS[d["name"]] = d["config"]

            self._save_to_json()

            self._refresh_prov_table()



    def _on_edit_provider(self, name):

        dlg = ProviderEditDialog(self, name, PROVIDERS.get(name, {}))

        if dlg.exec() == QtWidgets.QDialog.DialogCode.Accepted:

            PROVIDERS[name] = dlg.result_data["config"]

            self._save_to_json()

            self._refresh_prov_table()



    def _on_del_provider(self, name):

        reply = QtWidgets.QMessageBox.question(

            self,

            "删除",

            f"确定删除提供商 {name} 吗？",

            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,

        )

        if reply == QtWidgets.QMessageBox.StandardButton.Yes:

            PROVIDERS.pop(name, None)

            self._save_to_json()

            self._refresh_prov_table()



    # ---------- LLM ----------

    def _init_llm_page(self):

        page = QtWidgets.QWidget()

        layout = QtWidgets.QVBoxLayout(page)



        top = QtWidgets.QHBoxLayout()

        top.addWidget(QtWidgets.QLabel("模型管理", objectName="header"))

        btn_add = QtWidgets.QPushButton("+ 添加模型")

        btn_add.clicked.connect(self._on_add_model)

        top.addWidget(btn_add)

        layout.addLayout(top)



        note = QtWidgets.QLabel(

            f"提示：路由会保存到 {CUSTOM_MODELS_PATH}。若你改了 config.py 却不生效，请检查该文件是否覆盖。"

        )

        note.setStyleSheet("color:#6B7280;")

        note.setWordWrap(True)

        layout.addWidget(note)



        self.llm_table = QtWidgets.QTableWidget()

        self.llm_table.setColumnCount(4)

        self.llm_table.setHorizontalHeaderLabels(["ID", "模型名", "API 地址", "操作"])

        self.llm_table.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.Stretch)

        self.llm_table.horizontalHeader().setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)

        self.llm_table.verticalHeader().setVisible(False)

        layout.addWidget(self.llm_table, 2)



        layout.addWidget(QtWidgets.QLabel("任务路由策略", objectName="header"))

        self.router_ui = {}

        form = QtWidgets.QFormLayout()

        route_items = [

            ("default", "闲聊"),

            ("tool_reasoning", "推理"),

            ("summary", "总结"),

            ("gatekeeper", "看门人"),

            ("translation", "翻译"),

            ("screen_classify", "屏幕分类"),

            ("codex", "代码助手"),

        ]

        for task, label in route_items:

            w = QtWidgets.QWidget()

            h = QtWidgets.QHBoxLayout(w)

            h.setContentsMargins(0, 0, 0, 0)

            line = QtWidgets.QLineEdit()

            line.setReadOnly(True)

            line.setStyleSheet("background:#f3f4f6;")

            self.router_ui[task] = line

            btn = QtWidgets.QPushButton("齿轮")

            btn.setObjectName("routerConfigBtn")

            btn.clicked.connect(lambda c=False, t=task: self._on_conf_router(t))

            h.addWidget(line)

            h.addWidget(btn)

            form.addRow(label + ":", w)

        layout.addLayout(form)



        self.stack.addWidget(page)

        self._refresh_llm_table()

        self._refresh_router()



    def _refresh_llm_table(self):

        self.llm_table.setRowCount(0)

        for mid, cfg in MODELS.items():

            row = self.llm_table.rowCount()

            self.llm_table.insertRow(row)

            self.llm_table.setItem(row, 0, QtWidgets.QTableWidgetItem(str(mid)))

            self.llm_table.setItem(row, 1, QtWidgets.QTableWidgetItem(str((cfg or {}).get("model", ""))))

            self.llm_table.setItem(row, 2, QtWidgets.QTableWidgetItem(str((cfg or {}).get("base_url", ""))))



            action_widget = QtWidgets.QWidget()

            h = QtWidgets.QHBoxLayout(action_widget)

            h.setContentsMargins(4, 4, 4, 4)

            h.setSpacing(8)

            btn_edit = QtWidgets.QPushButton("编辑")

            btn_edit.clicked.connect(lambda c=False, m=mid: self._on_edit_model(m))

            btn_del = QtWidgets.QPushButton("删除")

            btn_edit.setObjectName("tableActionBtn")

            btn_del.setObjectName("tableDangerBtn")

            btn_del.clicked.connect(lambda c=False, m=mid: self._on_del_model(m))

            h.addWidget(btn_edit)

            h.addWidget(btn_del)

            h.addStretch()

            self.llm_table.setCellWidget(row, 3, action_widget)



    def _refresh_router(self):

        for task, line in self.router_ui.items():

            chain = LLM_ROUTER.get(task, [])

            if isinstance(chain, str):

                chain = [chain]

            line.setText(" -> ".join(chain) if chain else "(未配置)")



    def _on_add_model(self):

        dlg = ModelEditDialog(self, on_provider_saved=self._save_to_json)

        if dlg.exec() == QtWidgets.QDialog.DialogCode.Accepted:

            data = dlg.result_data

            mid = data["id"]

            if mid in MODELS:

                QtWidgets.QMessageBox.warning(self, "错误", "ID 已存在")

                return

            MODELS[mid] = data["config"]

            self._save_to_json()

            self._refresh_llm_table()



    def _on_edit_model(self, mid):

        dlg = ModelEditDialog(self, mid, MODELS.get(mid, {}), on_provider_saved=self._save_to_json)

        if dlg.exec() == QtWidgets.QDialog.DialogCode.Accepted:

            MODELS[mid] = dlg.result_data["config"]

            self._save_to_json()

            self._refresh_llm_table()



    def _on_del_model(self, mid):

        reply = QtWidgets.QMessageBox.question(

            self,

            "删除",

            f"删除模型 {mid} 吗？",

            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,

        )

        if reply == QtWidgets.QMessageBox.StandardButton.Yes:

            MODELS.pop(mid, None)

            self._save_to_json()

            self._refresh_llm_table()



    def _on_conf_router(self, task):

        chain = LLM_ROUTER.get(task, [])

        if isinstance(chain, str):

            chain = [chain]

        dlg = RouterConfigDialog(self, task, list(chain))

        if dlg.exec() == QtWidgets.QDialog.DialogCode.Accepted:

            LLM_ROUTER[task] = dlg.result_chain

            self._save_to_json()

            self._refresh_router()



    def _save_to_json(self):

        data = {"models": MODELS, "router": LLM_ROUTER, "providers": PROVIDERS}

        try:

            directory = os.path.dirname(CUSTOM_MODELS_PATH)

            if directory:

                os.makedirs(directory, exist_ok=True)

            with open(CUSTOM_MODELS_PATH, "w", encoding="utf-8") as f:

                json.dump(data, f, ensure_ascii=False, indent=2)

        except Exception as e:

            QtWidgets.QMessageBox.critical(self, "保存失败", str(e))



    # ---------- Dependency ----------

    def _init_dependency_page(self):

        page = QtWidgets.QWidget()

        layout = QtWidgets.QVBoxLayout(page)



        top = QtWidgets.QHBoxLayout()

        self.dep_title = QtWidgets.QLabel("依赖体检", objectName="header")

        top.addWidget(self.dep_title)

        top.addStretch()



        btn_refresh = QtWidgets.QPushButton("刷新")

        btn_refresh.clicked.connect(self._refresh_dependency_rows)

        top.addWidget(btn_refresh)



        btn_copy = QtWidgets.QPushButton("复制安装命令")

        btn_copy.clicked.connect(self._copy_dependency_install_cmd)

        top.addWidget(btn_copy)



        btn_install = QtWidgets.QPushButton("一键安装")

        btn_install.clicked.connect(self._install_missing_dependencies)

        top.addWidget(btn_install)

        layout.addLayout(top)



        tip = QtWidgets.QLabel("扫描 plugins 目录中缺失的第三方依赖。")

        tip.setStyleSheet("color:#6B7280;")

        layout.addWidget(tip)



        self.dep_table = QtWidgets.QTableWidget()

        self.dep_table.setColumnCount(4)

        self.dep_table.setHorizontalHeaderLabels(["模块", "pip 包", "来源插件", "状态"])

        self.dep_table.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.Stretch)

        self.dep_table.verticalHeader().setVisible(False)

        layout.addWidget(self.dep_table, 1)



        self.dep_result = QtWidgets.QPlainTextEdit()

        self.dep_result.setReadOnly(True)

        self.dep_result.setPlaceholderText("安装结果会显示在这里")

        self.dep_result.setMaximumHeight(180)

        layout.addWidget(self.dep_result)



        self.dep_rows = []

        self.stack.addWidget(page)

        self._refresh_dependency_rows()



    def _refresh_dependency_rows(self):

        self.dep_rows = scan_missing_dependencies("./plugins")

        rows = self.dep_rows or []

        self.dep_table.setRowCount(0)



        if not rows:

            self.dep_title.setText("依赖体检（无缺失）")

            self.dep_table.setRowCount(1)

            self.dep_table.setItem(0, 0, QtWidgets.QTableWidgetItem("无"))

            self.dep_table.setItem(0, 1, QtWidgets.QTableWidgetItem("-"))

            self.dep_table.setItem(0, 2, QtWidgets.QTableWidgetItem("-"))

            self.dep_table.setItem(0, 3, QtWidgets.QTableWidgetItem("OK"))

            return



        self.dep_title.setText(f"依赖体检（缺失 {len(rows)} 项）")

        for row in rows:

            r = self.dep_table.rowCount()

            self.dep_table.insertRow(r)

            self.dep_table.setItem(r, 0, QtWidgets.QTableWidgetItem(str(row.get("module", ""))))

            self.dep_table.setItem(r, 1, QtWidgets.QTableWidgetItem(str(row.get("package", ""))))

            self.dep_table.setItem(r, 2, QtWidgets.QTableWidgetItem(str(row.get("plugins", ""))))

            self.dep_table.setItem(r, 3, QtWidgets.QTableWidgetItem("缺失"))



    def _copy_dependency_install_cmd(self):

        cmd = build_install_command(self.dep_rows)

        if not cmd:

            QtWidgets.QMessageBox.information(self, "依赖体检", "当前没有需要安装的依赖。")

            return

        QtWidgets.QApplication.clipboard().setText(cmd)

        self.dep_result.setPlainText(cmd)

        QtWidgets.QMessageBox.information(self, "依赖体检", "安装命令已复制到剪贴板。")



    def _install_missing_dependencies(self):

        if not self.dep_rows:

            QtWidgets.QMessageBox.information(self, "依赖体检", "当前没有需要安装的依赖。")

            return

        reply = QtWidgets.QMessageBox.question(

            self,

            "依赖体检",

            "将执行 pip 安装缺失依赖，是否继续？",

            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,

        )

        if reply != QtWidgets.QMessageBox.StandardButton.Yes:

            return



        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.CursorShape.WaitCursor)

        try:

            result = install_missing(self.dep_rows, timeout=900)

        finally:

            QtWidgets.QApplication.restoreOverrideCursor()



        ok = str(result.get("ok", "0")) == "1"

        msg = str(result.get("message", ""))

        self.dep_result.setPlainText(msg)

        if ok:

            QtWidgets.QMessageBox.information(self, "依赖体检", "依赖安装完成。")

        else:

            QtWidgets.QMessageBox.warning(self, "依赖体检", "依赖安装失败，请查看输出日志。")

        self._refresh_dependency_rows()



    # ---------- Colors ----------
    def _init_color_page(self):
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)

        tip = QtWidgets.QLabel("支持输入十六进制颜色（如 #1F2937）。保存后会自动应用到主界面与对话窗口。")
        tip.setStyleSheet("color:#6B7280;")
        tip.setWordWrap(True)
        layout.addWidget(tip)

        self._ui_color_inputs = {}
        self._ui_color_labels = {}
        self._ui_color_previews = {}

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)

        body = QtWidgets.QWidget()
        body_layout = QtWidgets.QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(12)

        def add_group(title: str):
            group = QtWidgets.QGroupBox(title)
            form = QtWidgets.QFormLayout(group)
            form.setLabelAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
            form.setFormAlignment(QtCore.Qt.AlignmentFlag.AlignTop)
            form.setHorizontalSpacing(12)
            form.setVerticalSpacing(10)
            body_layout.addWidget(group)
            return form

        def add_color_row(form: QtWidgets.QFormLayout, label: str, key: str):
            row = QtWidgets.QWidget()
            h = QtWidgets.QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(8)

            inp = QtWidgets.QLineEdit()
            inp.setPlaceholderText("#RRGGBB")
            preview = QtWidgets.QLabel()
            preview.setFixedSize(22, 22)
            preview.setStyleSheet("border: 1px solid #D1D5DB; border-radius: 4px;")

            btn = QtWidgets.QPushButton("选色")
            btn.setFixedWidth(64)
            btn.clicked.connect(lambda checked=False, w=inp, p=preview: self._pick_color(w, p))
            inp.textChanged.connect(lambda text, p=preview: self._refresh_color_preview(p, text))

            h.addWidget(inp, 1)
            h.addWidget(preview)
            h.addWidget(btn)
            form.addRow(label + ":", row)

            self._ui_color_inputs[key] = inp
            self._ui_color_labels[key] = label
            self._ui_color_previews[key] = preview

        base_form = add_group("基础配色")
        add_color_row(base_form, "主色", "accent")
        add_color_row(base_form, "主色悬停", "accent_hover")
        add_color_row(base_form, "主色浅背景", "accent_soft")
        add_color_row(base_form, "应用背景", "bg_app")
        add_color_row(base_form, "卡片背景", "bg_card")
        add_color_row(base_form, "柔和背景", "bg_soft")
        add_color_row(base_form, "边框", "border")
        add_color_row(base_form, "强边框", "border_strong")
        add_color_row(base_form, "主文本", "text_primary")
        add_color_row(base_form, "次文本", "text_secondary")
        add_color_row(base_form, "弱文本", "text_muted")
        add_color_row(base_form, "成功", "success")
        add_color_row(base_form, "成功浅色", "success_soft")
        add_color_row(base_form, "警告", "warning")
        add_color_row(base_form, "危险", "danger")

        main_console_form = add_group("主控制台")
        add_color_row(main_console_form, "背景", "console_main.bg")
        add_color_row(main_console_form, "文字", "console_main.fg")
        add_color_row(main_console_form, "边框", "console_main.border")
        add_color_row(main_console_form, "选中背景", "console_main.selection_bg")
        add_color_row(main_console_form, "选中文字", "console_main.selection_fg")
        add_color_row(main_console_form, "弱文本", "console_main.muted")
        add_color_row(main_console_form, "标签", "console_main.label")

        codex_console_form = add_group("代码助手控制台")
        add_color_row(codex_console_form, "背景", "console_codex.bg")
        add_color_row(codex_console_form, "文字", "console_codex.fg")
        add_color_row(codex_console_form, "边框", "console_codex.border")
        add_color_row(codex_console_form, "选中背景", "console_codex.selection_bg")
        add_color_row(codex_console_form, "选中文字", "console_codex.selection_fg")
        add_color_row(codex_console_form, "弱文本", "console_codex.muted")
        add_color_row(codex_console_form, "标签", "console_codex.label")

        body_layout.addStretch()
        scroll.setWidget(body)
        layout.addWidget(scroll, 1)

        footer = QtWidgets.QHBoxLayout()
        footer.addStretch()
        btn_reset = QtWidgets.QPushButton("恢复默认")
        btn_reset.clicked.connect(self._reset_color_settings)
        btn_save = QtWidgets.QPushButton("保存并应用")
        btn_save.setObjectName("primaryAction")
        btn_save.clicked.connect(self._save_color_settings)
        footer.addWidget(btn_reset)
        footer.addWidget(btn_save)
        layout.addLayout(footer)

        self.stack.addWidget(page)
        self._refresh_color_inputs()

    def _default_ui_palette(self):
        palette = dict(UI_PALETTE)
        palette["console_main"] = dict(DEFAULT_CONSOLE)
        palette["console_codex"] = dict(DEFAULT_CONSOLE)
        return palette

    def _refresh_color_inputs(self):
        palette = get_ui_palette()
        for key, inp in self._ui_color_inputs.items():
            value = self._get_palette_value(palette, key)
            if value:
                inp.setText(str(value))

    @staticmethod
    def _get_palette_value(palette: dict, key: str) -> str:
        if "." not in key:
            return str(palette.get(key, ""))
        group, sub = key.split(".", 1)
        group_map = palette.get(group, {})
        if isinstance(group_map, dict):
            return str(group_map.get(sub, ""))
        return ""

    @staticmethod
    def _is_valid_color(value: str) -> bool:
        return bool(re.match(r"^#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?$", value or ""))

    def _refresh_color_preview(self, preview: QtWidgets.QLabel, value: str):
        color = value.strip()
        if self._is_valid_color(color):
            preview.setStyleSheet(f"background: {color}; border: 1px solid #D1D5DB; border-radius: 4px;")
        else:
            preview.setStyleSheet("border: 1px solid #D1D5DB; border-radius: 4px;")

    def _pick_color(self, inp: QtWidgets.QLineEdit, preview: QtWidgets.QLabel):
        current = inp.text().strip()
        if not self._is_valid_color(current):
            current = "#FFFFFF"
        color = QtGui.QColor(current)
        chosen = QtWidgets.QColorDialog.getColor(color, self, "选择颜色")
        if chosen.isValid():
            inp.setText(chosen.name())
            self._refresh_color_preview(preview, chosen.name())

    def _collect_color_settings(self):
        palette = {}
        console_main = {}
        console_codex = {}
        for key, inp in self._ui_color_inputs.items():
            value = inp.text().strip()
            label = self._ui_color_labels.get(key, key)
            if not self._is_valid_color(value):
                QtWidgets.QMessageBox.warning(self, "颜色设置", f"{label} 的颜色值无效：{value}")
                return None
            if "." in key:
                group, sub = key.split(".", 1)
                if group == "console_main":
                    console_main[sub] = value
                elif group == "console_codex":
                    console_codex[sub] = value
            else:
                palette[key] = value
        if console_main:
            palette["console_main"] = console_main
        if console_codex:
            palette["console_codex"] = console_codex
        return palette

    def _apply_palette_now(self):
        if self.main_app is not None and hasattr(self.main_app, "apply_ui_palette"):
            self.main_app.apply_ui_palette()
        else:
            self.setStyleSheet(get_settings_styles())

    def _save_color_settings(self):
        palette = self._collect_color_settings()
        if not palette:
            return
        update_runtime_settings({"ui_palette": palette})
        self._apply_palette_now()

    def _reset_color_settings(self):
        palette = self._default_ui_palette()
        update_runtime_settings({"ui_palette": palette})
        self._refresh_color_inputs()
        self._apply_palette_now()
# ---------- MCP / QQ ----------

    def _load_gateway_settings(self):

        runtime = load_runtime_settings()

        def _parse_id_list(value):

            if isinstance(value, str):

                return [item.strip() for item in re.split(r"[,，\n\s]+", value) if item.strip()]

            if isinstance(value, list):

                return [str(item).strip() for item in value if str(item).strip()]

            return []

        owner_ids_raw = runtime.get("napcat_owner_user_ids", [])

        owner_ids = _parse_id_list(owner_ids_raw)

        voice_probability_raw = runtime.get("napcat_voice_reply_probability", NAPCAT_VOICE_REPLY_PROBABILITY)

        try:

            voice_probability = max(0, min(100, int(voice_probability_raw)))

        except Exception:

            voice_probability = int(NAPCAT_VOICE_REPLY_PROBABILITY)

        return {

            "mcp_enabled": bool(runtime.get("mcp_enabled", MCP_ENABLED)),

            "mcp_server_configs": runtime.get("mcp_server_configs", MCP_SERVER_CONFIGS) if isinstance(runtime.get("mcp_server_configs", MCP_SERVER_CONFIGS), list) else MCP_SERVER_CONFIGS,

            "napcat_enabled": bool(runtime.get("napcat_enabled", NAPCAT_ENABLED)),

            "napcat_webhook_host": str(runtime.get("napcat_webhook_host", NAPCAT_WEBHOOK_HOST) or NAPCAT_WEBHOOK_HOST),

            "napcat_webhook_port": int(runtime.get("napcat_webhook_port", NAPCAT_WEBHOOK_PORT) or NAPCAT_WEBHOOK_PORT),

            "napcat_webhook_path": str(runtime.get("napcat_webhook_path", NAPCAT_WEBHOOK_PATH) or NAPCAT_WEBHOOK_PATH),

            "napcat_access_token": str(runtime.get("napcat_access_token", NAPCAT_ACCESS_TOKEN) or ""),

            "napcat_api_base": str(runtime.get("napcat_api_base", NAPCAT_API_BASE) or NAPCAT_API_BASE),

            "napcat_api_token": str(runtime.get("napcat_api_token", NAPCAT_API_TOKEN) or ""),

            "napcat_reply_enabled": bool(runtime.get("napcat_reply_enabled", NAPCAT_REPLY_ENABLED)),

            "napcat_allow_private": bool(runtime.get("napcat_allow_private", NAPCAT_ALLOW_PRIVATE)),

            "napcat_allow_group": bool(runtime.get("napcat_allow_group", NAPCAT_ALLOW_GROUP)),

            "napcat_group_require_at": bool(runtime.get("napcat_group_require_at", NAPCAT_GROUP_REQUIRE_AT)),

            "napcat_owner_user_ids": owner_ids,

            "napcat_owner_label": str(runtime.get("napcat_owner_label", "主人") or "主人"),

            "napcat_image_vision_enabled": bool(runtime.get("napcat_image_vision_enabled", True)),

            "napcat_image_prompt": str(runtime.get("napcat_image_prompt", "请客观详细描述这张QQ图片的内容，并提取其中可用于回复的关键信息。") or "请客观详细描述这张QQ图片的内容，并提取其中可用于回复的关键信息。"),

            "napcat_voice_reply_enabled": bool(runtime.get("napcat_voice_reply_enabled", NAPCAT_VOICE_REPLY_ENABLED)),

            "napcat_voice_reply_probability": voice_probability,

            "napcat_filter_mode": str(runtime.get("napcat_filter_mode", "off") or "off").strip().lower(),

            "napcat_user_whitelist": _parse_id_list(runtime.get("napcat_user_whitelist", [])),

            "napcat_user_blacklist": _parse_id_list(runtime.get("napcat_user_blacklist", [])),

            "napcat_group_whitelist": _parse_id_list(runtime.get("napcat_group_whitelist", [])),

            "napcat_group_blacklist": _parse_id_list(runtime.get("napcat_group_blacklist", [])),

            "remote_chat_ui_append": bool(runtime.get("remote_chat_ui_append", REMOTE_CHAT_UI_APPEND)),

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

        return str(self.gateway_filter_mode.currentData() or "off").strip().lower() or "off"



    def _format_mcp_servers_json(self, servers) -> str:

        value = servers if isinstance(servers, list) else []

        return json.dumps(value, ensure_ascii=False, indent=2)



    def _parse_mcp_servers_json(self):

        if hasattr(self, "_gateway_mcp_server_configs"):

            return self._normalize_mcp_server_configs(self._gateway_mcp_server_configs)

        raw = self.gateway_mcp_servers.toPlainText().strip() if hasattr(self, "gateway_mcp_servers") else ""

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

            transport = str(item.get("transport") or "stdio").strip().lower().replace("-", "_")

            if transport == "http":

                transport = "streamable_http"

            if transport not in {"stdio", "streamable_http"}:

                transport = "stdio"

            payload = {

                "name": str(item.get("name") or f"server_{index + 1}").strip() or f"server_{index + 1}",

                "transport": transport,

                "enabled": bool(item.get("enabled", True)),

                "timeout_sec": _safe_float(item.get("timeout_sec") or 20.0, 20.0),

                "sse_read_timeout_sec": _safe_float(item.get("sse_read_timeout_sec") or 300.0, 300.0),

            }

            if transport == "stdio":

                payload["command"] = str(item.get("command") or "").strip()

                args_value = item.get("args") or []

                if isinstance(args_value, list):

                    args = [str(x).strip() for x in args_value if str(x).strip()]

                elif isinstance(args_value, str):

                    args = [line.strip() for line in args_value.splitlines() if line.strip()]

                else:

                    args = []

                if args:

                    payload["args"] = args

                cwd = str(item.get("cwd") or "").strip()

                if cwd:

                    payload["cwd"] = cwd

                env = item.get("env") or {}

                if isinstance(env, dict) and env:

                    payload["env"] = {str(k).strip(): str(v).strip() for k, v in env.items() if str(k).strip()}

            else:

                url = str(item.get("url") or "").strip()

                if url:

                    payload["url"] = url

                headers = item.get("headers") or {}

                if isinstance(headers, dict) and headers:

                    payload["headers"] = {str(k).strip(): str(v).strip() for k, v in headers.items() if str(k).strip()}

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

            transport_text = "本地进程" if str(item.get("transport") or "stdio") == "stdio" else "远程 HTTP"

            timeout_text = f"{float(item.get('timeout_sec') or 20.0):.1f}s"



            self.gateway_mcp_table.setItem(row, 0, QtWidgets.QTableWidgetItem(enabled_text))

            self.gateway_mcp_table.setItem(row, 1, QtWidgets.QTableWidgetItem(str(item.get("name") or "")))

            self.gateway_mcp_table.setItem(row, 2, QtWidgets.QTableWidgetItem(transport_text))

            self.gateway_mcp_table.setItem(row, 3, QtWidgets.QTableWidgetItem(self._gateway_mcp_entry_text(item)))

            self.gateway_mcp_table.setItem(row, 4, QtWidgets.QTableWidgetItem(timeout_text))



            action_widget = QtWidgets.QWidget()

            action_layout = QtWidgets.QHBoxLayout(action_widget)

            action_layout.setContentsMargins(0, 0, 0, 0)

            action_layout.setSpacing(6)



            btn_edit = QtWidgets.QPushButton("编辑")

            btn_edit.setObjectName("tableActionBtn")

            btn_edit.clicked.connect(lambda checked=False, idx=row: self._edit_gateway_mcp_server(idx))

            action_layout.addWidget(btn_edit)



            btn_remove = QtWidgets.QPushButton("删除")

            btn_remove.setObjectName("tableDangerBtn")

            btn_remove.clicked.connect(lambda checked=False, idx=row: self._remove_gateway_mcp_server(idx))

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

            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,

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

            self.gateway_qq_profile_preview.setText("当前记忆存储不可用，暂时无法读取 QQ 档案。")

            return

        user_id = self._selected_gateway_qq_profile_user_id()

        if not user_id:

            self.gateway_qq_profile_preview.setText("请先选择一个 QQ 联系人，这里会显示她的身份摘要、关系、权限和备注。")

            return

        profile = store.get_qq_user_profile(user_id) if hasattr(store, "get_qq_user_profile") else None

        if not profile:

            self.gateway_qq_profile_preview.setText("未找到该 QQ 联系人的档案。")

            return

        display_name = str(profile.get("remark_name") or profile.get("nickname") or "-").strip() or "-"

        identity_summary = str(profile.get("identity_summary") or "").strip() or "暂无身份摘要"

        notes = str(profile.get("notes") or "").strip() or "暂无备注"

        permission_text = self._gateway_qq_permission_label(profile.get("permission_level"))

        relation_text = self._gateway_qq_relation_label(profile.get("relationship_to_owner"))

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

            display_name = str(profile.get("remark_name") or profile.get("nickname") or "").strip() or "-"

            relation_text = self._gateway_qq_relation_label(profile.get("relationship_to_owner"))

            scope_text = self._gateway_qq_scope_label(profile.get("memory_scope"))

            permission_text = self._gateway_qq_permission_label(profile.get("permission_level"))

            summary_text = str(profile.get("identity_summary") or "").replace("\n", " ").strip()

            if len(summary_text) > 42:

                summary_text = summary_text[:42] + "?"

            self.gateway_qq_profile_table.setItem(row, 0, QtWidgets.QTableWidgetItem(status_text))

            self.gateway_qq_profile_table.setItem(row, 1, QtWidgets.QTableWidgetItem(user_id))

            self.gateway_qq_profile_table.setItem(row, 2, QtWidgets.QTableWidgetItem(display_name))

            self.gateway_qq_profile_table.setItem(row, 3, QtWidgets.QTableWidgetItem(relation_text))

            self.gateway_qq_profile_table.setItem(row, 4, QtWidgets.QTableWidgetItem(scope_text))

            self.gateway_qq_profile_table.setItem(row, 5, QtWidgets.QTableWidgetItem(permission_text))

            self.gateway_qq_profile_table.setItem(row, 6, QtWidgets.QTableWidgetItem(summary_text or "-"))



            action_widget = QtWidgets.QWidget()

            action_layout = QtWidgets.QHBoxLayout(action_widget)

            action_layout.setContentsMargins(0, 0, 0, 0)

            action_layout.setSpacing(6)



            btn_edit = QtWidgets.QPushButton("编辑")

            btn_edit.setObjectName("tableActionBtn")

            btn_edit.clicked.connect(lambda checked=False, uid=user_id: self._edit_gateway_qq_profile(uid))

            action_layout.addWidget(btn_edit)



            btn_remove = QtWidgets.QPushButton("删除")

            btn_remove.setObjectName("tableDangerBtn")

            btn_remove.clicked.connect(lambda checked=False, uid=user_id: self._delete_gateway_qq_profile(uid))

            action_layout.addWidget(btn_remove)



            self.gateway_qq_profile_table.setCellWidget(row, 7, action_widget)



        if profiles and self.gateway_qq_profile_table.currentRow() < 0:

            self.gateway_qq_profile_table.selectRow(0)

        self.gateway_qq_profile_table.resizeRowsToContents()

        self._update_gateway_qq_profile_actions()

        if not profiles:

            self.gateway_qq_profile_preview.setText("还没有 QQ 联系人档案。后续收到 QQ 新消息时会自动补齐基础档案，你也可以手动新增。")



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

            if item and item.text().strip() == str(dlg.result_data.get("user_id") or "").strip():

                self.gateway_qq_profile_table.selectRow(row)

                break



    def _edit_gateway_qq_profile(self, user_id: str = ""):

        store = self._get_memory_store()

        if store is None or not hasattr(store, "get_qq_user_profile"):

            QtWidgets.QMessageBox.warning(self, "QQ 档案", "当前记忆存储不可用。")

            return

        target_user_id = str(user_id or self._selected_gateway_qq_profile_user_id()).strip()

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

        target_user_id = str(user_id or self._selected_gateway_qq_profile_user_id()).strip()

        if not target_user_id:

            return

        reply = QtWidgets.QMessageBox.question(

            self,

            "QQ 档案",

            f"确定删除 QQ 档案 `{target_user_id}` 吗？\n\n删除后只会移除联系人画像，不会删除聊天记录与记忆正文。",

            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,

        )

        if reply != QtWidgets.QMessageBox.StandardButton.Yes:

            return

        try:

            ok = bool(store.delete_qq_user_profile(target_user_id))

        except Exception as exc:

            QtWidgets.QMessageBox.warning(self, "QQ 档案", f"删除失败：{exc}")

            return

        if not ok:

            QtWidgets.QMessageBox.warning(self, "QQ 档案", "删除失败，可能是档案已不存在。")

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

        plugin_manager = getattr(self.main_app, "plugin_manager", None) if self.main_app else None

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

            "action_keywords": ["查", "查一下", "查询", "看", "看一下", "领", "领取", "获取", "优惠", "优惠券", "会员券", "券", "折扣", "活动"],

            "web_search_override_keywords": ["联网", "上网", "网页", "百度", "google", "bing", "搜索"],

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

            rows = [item.strip() for line in text.splitlines() for item in line.split(",") if item.strip()]

        else:

            rows = []

        return list(dict.fromkeys(rows))



    def _load_mcp_intent_route_settings(self) -> dict:

        defaults = self._default_mcp_intent_route_settings()

        config = self._load_mcp_tools_config()

        settings = config.get("settings", {}) if isinstance(config, dict) else {}

        enabled = bool(self._read_plugin_setting(settings, "intent_route_enabled", defaults["enabled"]))

        brand_keywords = self._normalize_keyword_list(

            self._read_plugin_setting(settings, "intent_route_brand_keywords", defaults["brand_keywords"])

        )

        action_keywords = self._normalize_keyword_list(

            self._read_plugin_setting(settings, "intent_route_action_keywords", defaults["action_keywords"])

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



    def _collect_mcp_intent_route_settings(self) -> dict:

        if not hasattr(self, "gateway_mcp_intent_route_enabled"):

            return self._load_mcp_intent_route_settings()

        return {

            "enabled": bool(self.gateway_mcp_intent_route_enabled.isChecked()),

            "brand_keywords": self._normalize_keyword_list(self.gateway_mcp_brand_keywords.toPlainText()),

            "action_keywords": self._normalize_keyword_list(self.gateway_mcp_action_keywords.toPlainText()),

            "web_search_override_keywords": self._normalize_keyword_list(

                self.gateway_mcp_web_search_override_keywords.toPlainText()

            ),

        }



    def _upsert_mcp_setting_entry(self, settings: dict, key: str, value, *, setting_type: str, label: str, description: str):

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

            return self._normalize_mcp_access_control(self._load_mcp_tools_config().get("access_control"))

        return self._normalize_mcp_access_control({

            "allow_local": self.gateway_mcp_allow_local.isChecked(),

            "allow_remote_qq": self.gateway_mcp_allow_remote_qq.isChecked(),

            "allow_qq_owner": self.gateway_mcp_allow_qq_owner.isChecked(),

            "allow_qq_others": self.gateway_mcp_allow_qq_others.isChecked(),

        })



    def _describe_mcp_access_control(self, access_control) -> str:

        normalized = self._normalize_mcp_access_control(access_control)

        local_text = "允许本地桌面侧触发" if normalized["allow_local"] else "已禁用本地桌面侧触发"

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

            return {"changed": False, "saved": False, "applied": False, "error": "未找到 mcp_tools 插件配置"}



        new_access = self._collect_mcp_access_control()

        old_access = self._normalize_mcp_access_control(config.get("access_control"))



        new_intent_route = self._collect_mcp_intent_route_settings()

        old_intent_route = self._load_mcp_intent_route_settings()



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



        access_changed = old_access != new_access

        intent_route_changed = old_intent_route != new_intent_route

        changed = access_changed or intent_route_changed

        if not changed:

            return {

                "changed": False,

                "saved": True,

                "applied": True,

                "access_control": new_access,

                "intent_route": new_intent_route,

                "access_changed": False,

                "intent_route_changed": False,

            }



        plugin_manager = getattr(self.main_app, "plugin_manager", None) if self.main_app else None

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

                    "error": "" if ok else "plugin_manager.save_plugin_config 返回 False",

                    "access_control": new_access,

                    "intent_route": new_intent_route,

                    "access_changed": access_changed,

                    "intent_route_changed": intent_route_changed,

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

                "access_changed": access_changed,

                "intent_route_changed": intent_route_changed,

            }

        except Exception as e:

            return {

                "changed": True,

                "saved": False,

                "applied": False,

                "error": str(e),

                "access_control": new_access,

                "intent_route": new_intent_route,

                "access_changed": access_changed,

                "intent_route_changed": intent_route_changed,

            }



    def _refresh_gateway_preview(self, *args):

        if hasattr(self, "gateway_url_preview"):

            mode_map = {"off": "不过滤", "whitelist": "白名单", "blacklist": "黑名单"}

            mode_text = mode_map.get(self._selected_gateway_filter_mode(), "不过滤")

            voice_enabled = bool(getattr(self, "gateway_voice_reply_enabled", None) and self.gateway_voice_reply_enabled.isChecked())

            voice_probability = int(self.gateway_voice_reply_probability.value()) if hasattr(self, "gateway_voice_reply_probability") else 0

            self.gateway_url_preview.setText(

                f"Webhook 地址：{self._gateway_webhook_url()}\n"

                f"反向 WS 地址：{self._gateway_ws_url()}（也兼容 {self._gateway_ws_url(root=True)}）\n"

                f"NapCat 可二选一：HTTP 上报到 Webhook，或反向 WebSocket 连到 WS 地址；回消息优先走 WS，不通再回退 HTTP API。\n"

                f"QQ 过滤模式：{mode_text}\n"

                f"QQ 概率语音：{'开启' if voice_enabled else '关闭'}"

                f"{'（' + str(voice_probability) + '% 命中，成功时优先发语音，失败回退文本）' if voice_enabled else ''}\n"

                f"QQ 对话默认沿用当前本地激活人设，不会单独切一个 QQ 人格。"

            )

        if hasattr(self, "gateway_group_require_at") and hasattr(self, "gateway_allow_group"):

            self.gateway_group_require_at.setEnabled(self.gateway_allow_group.isChecked())

        if hasattr(self, "gateway_voice_reply_probability") and hasattr(self, "gateway_voice_reply_enabled"):

            self.gateway_voice_reply_probability.setEnabled(self.gateway_voice_reply_enabled.isChecked())

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

                self.gateway_mcp_access_preview.setText("未找到 `mcp_tools` 插件配置，暂时无法设置 MCP 的 QQ 触发权限。")

            else:

                summary = self._describe_mcp_access_control(self._collect_mcp_access_control())

                self.gateway_mcp_access_preview.setText(f"当前权限：{summary}\n建议：如果你只想让自己从 QQ 远程调用 MCP，就仅保留“允许主人 QQ 触发”。")



        if hasattr(self, "gateway_mcp_intent_route_preview"):

            route_cfg = self._collect_mcp_intent_route_settings()

            if not route_cfg.get("enabled", True):

                self.gateway_mcp_intent_route_preview.setText("自然语言路由：已关闭（不会优先抢占 mcp_tools）。")

            else:

                brands = route_cfg.get("brand_keywords") or []

                actions = route_cfg.get("action_keywords") or []

                web_words = route_cfg.get("web_search_override_keywords") or []

                sample_brand = "、".join(brands[:4]) + (" 等" if len(brands) > 4 else "")

                sample_action = "、".join(actions[:5]) + (" 等" if len(actions) > 5 else "")

                sample_web = "、".join(web_words[:5]) + (" 等" if len(web_words) > 5 else "")

                self.gateway_mcp_intent_route_preview.setText(

                    f"自然语言路由：已开启\n"

                    f"- 品牌词 {len(brands)} 个：{sample_brand or '（空）'}\n"

                    f"- 动作词 {len(actions)} 个：{sample_action or '（空）'}\n"

                    f"- 联网覆盖词 {len(web_words)} 个：{sample_web or '（空）'}"

                )



        if hasattr(self, "gateway_mcp_preview"):

            try:

                servers = self._parse_mcp_servers_json()

                enabled_count = sum(1 for item in servers if isinstance(item, dict) and bool(item.get("enabled", True)))

                stdio_count = sum(1 for item in servers if isinstance(item, dict) and str(item.get("transport") or "stdio") == "stdio")

                http_count = len(servers) - stdio_count

                names = "、".join(str(item.get("name") or "server") for item in servers[:4] if isinstance(item, dict))

                if len(servers) > 4:

                    names += " 等"

                lines = [f"已配置 MCP 服务器 {len(servers)} 个，启用 {enabled_count} 个；其中 stdio {stdio_count} 个、HTTP {http_count} 个。"]

                if names:

                    lines.append(f"当前列表：{names}")

                lines.extend(["", "调用示例：", "- [CMD: mcp_tools | list_tools]", "- [CMD: mcp_tools | server_status]", "- [CMD: mcp_tools | call_tool ||| mcp.server.tool ||| {\"key\": \"value\"}]", "远程工具会自动暴露成 `mcp.<server>.<tool>` 形式。"])

                self.gateway_mcp_preview.setText("\n".join(lines))

            except Exception as e:

                self.gateway_mcp_preview.setText(f"MCP 服务器预览生成失败：{e}")



    def _collect_gateway_settings(self):

        return {

            "mcp_enabled": self.gateway_mcp_enabled.isChecked(),

            "mcp_server_configs": self._parse_mcp_servers_json(),

            "napcat_enabled": self.gateway_napcat_enabled.isChecked(),

            "napcat_webhook_host": self.gateway_webhook_host.text().strip() or "127.0.0.1",

            "napcat_webhook_port": int(self.gateway_webhook_port.value()),

            "napcat_webhook_path": (self.gateway_webhook_path.text().strip() or "/chat/napcat"),

            "napcat_access_token": self.gateway_access_token.text().strip(),

            "napcat_api_base": self.gateway_api_base.text().strip() or "http://127.0.0.1:3000",

            "napcat_api_token": self.gateway_api_token.text().strip(),

            "napcat_reply_enabled": self.gateway_reply_enabled.isChecked(),

            "napcat_allow_private": self.gateway_allow_private.isChecked(),

            "napcat_allow_group": self.gateway_allow_group.isChecked(),

            "napcat_group_require_at": self.gateway_group_require_at.isChecked(),

            "napcat_owner_user_ids": [item.strip() for item in re.split(r"[,，\n\s]+", self.gateway_owner_ids.text().strip()) if item.strip()],

            "napcat_owner_label": self.gateway_owner_label.text().strip() or "主人",

            "napcat_image_vision_enabled": self.gateway_image_vision_enabled.isChecked(),

            "napcat_image_prompt": self.gateway_image_prompt.toPlainText().strip() or "请客观详细描述这张QQ图片的内容，并提取其中可用于回复的关键信息。",

            "napcat_voice_reply_enabled": self.gateway_voice_reply_enabled.isChecked(),

            "napcat_voice_reply_probability": int(self.gateway_voice_reply_probability.value()),

            "napcat_filter_mode": self._selected_gateway_filter_mode(),

            "napcat_user_whitelist": [item.strip() for item in re.split(r"[,，\n\s]+", self.gateway_user_whitelist.text().strip()) if item.strip()],

            "napcat_user_blacklist": [item.strip() for item in re.split(r"[,，\n\s]+", self.gateway_user_blacklist.text().strip()) if item.strip()],

            "napcat_group_whitelist": [item.strip() for item in re.split(r"[,，\n\s]+", self.gateway_group_whitelist.text().strip()) if item.strip()],

            "napcat_group_blacklist": [item.strip() for item in re.split(r"[,，\n\s]+", self.gateway_group_blacklist.text().strip()) if item.strip()],

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

            new_settings["napcat_webhook_path"] = "/" + new_settings["napcat_webhook_path"]

        update_runtime_settings(new_settings)

        mcp_access_result = self._save_mcp_tools_access_control()

        apply_result = {}

        if getattr(self.main_app, "apply_external_settings", None):

            try:

                apply_result = self.main_app.apply_external_settings(new_settings) or {}

            except Exception as e:

                apply_result = {"error": str(e)}

        restart_sensitive_keys = {"mcp_enabled", "mcp_server_configs", "napcat_enabled", "napcat_webhook_host", "napcat_webhook_port", "napcat_webhook_path", "napcat_access_token", "napcat_api_base", "napcat_api_token", "napcat_reply_enabled", "napcat_allow_private", "napcat_allow_group", "napcat_group_require_at", "napcat_owner_user_ids", "napcat_owner_label", "napcat_image_vision_enabled", "napcat_image_prompt", "napcat_voice_reply_enabled", "napcat_voice_reply_probability", "napcat_filter_mode", "napcat_user_whitelist", "napcat_user_blacklist", "napcat_group_whitelist", "napcat_group_blacklist"}

        live_apply_ok = bool(apply_result) and not apply_result.get("error") and bool(apply_result.get("mcp_live_applied")) and bool(apply_result.get("napcat_live_applied"))

        restart_needed = any(old_settings.get(key) != new_settings.get(key) for key in restart_sensitive_keys) and not live_apply_ok

        restart_needed = restart_needed or bool(mcp_access_result.get("changed") and not mcp_access_result.get("applied"))

        message_lines = ["设置已保存。", "", "- QQ 回复是否显示到本地聊天框：对新消息立即生效"]

        if mcp_access_result.get("saved"):

            if mcp_access_result.get("access_changed"):

                if mcp_access_result.get("applied"):

                    message_lines.append("- MCP 的 QQ 触发权限：已保存并更新")

                else:

                    message_lines.append("- MCP 的 QQ 触发权限：已写入配置，建议重启后确认生效")

            if mcp_access_result.get("intent_route_changed"):

                if mcp_access_result.get("applied"):

                    message_lines.append("- MCP 自然语言路由：已保存并即时生效")

                else:

                    message_lines.append("- MCP 自然语言路由：已写入配置，建议重启后确认生效")

        else:

            message_lines.append(f"- MCP 配置保存失败（{mcp_access_result.get('error') or '未知错误'}）")

        if live_apply_ok:

            tool_count = len(apply_result.get("mcp_tools") or [])

            message_lines.append(f"- MCP 本地工具桥：已即时刷新（当前 {tool_count} 个工具）")

            server_items = apply_result.get("mcp_servers") or []

            if server_items:

                connected_count = sum(1 for item in server_items if isinstance(item, dict) and item.get("connected"))

                message_lines.append(f"- 远程 MCP 服务器：已处理 {len(server_items)} 个，当前连通 {connected_count} 个")

            if new_settings.get("napcat_enabled"):

                if apply_result.get("napcat_server_running"):

                    message_lines.append("- NapCat Webhook / 回发参数：已即时刷新并开始监听")

                else:

                    message_lines.append("- NapCat 回发参数：已即时刷新；Webhook 将在异步循环就绪后监听")

            else:

                message_lines.append("- NapCat Webhook：已即时关闭")

        else:

            message_lines.append("- NapCat / MCP 运行时参数：建议重启后完全生效")

            if apply_result.get("error"):

                message_lines.append(f"- 即时应用失败：{apply_result.get('error')}")

        message = "\n".join(message_lines)

        if restart_after_save and getattr(self.main_app, "on_restart_callback", None):

            QtWidgets.QMessageBox.information(self, "外部接入", message + "\n\n即将重启应用。")

            self.main_app.on_restart_callback()

            return

        if restart_needed and getattr(self.main_app, "on_restart_callback", None):

            reply = QtWidgets.QMessageBox.question(self, "外部接入", message + "\n\n检测到 NapCat / MCP 接入参数发生变化，是否现在重启应用？", QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No)

            if reply == QtWidgets.QMessageBox.StandardButton.Yes:

                self.main_app.on_restart_callback()

                return

        QtWidgets.QMessageBox.information(self, "外部接入", message)



    def _init_mcp_page(self):

        page = QtWidgets.QWidget()

        root = QtWidgets.QVBoxLayout(page)

        root.setContentsMargins(0, 0, 0, 0)



        scroll = QtWidgets.QScrollArea()

        scroll.setWidgetResizable(True)

        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)



        container = QtWidgets.QWidget()

        layout = QtWidgets.QVBoxLayout(container)

        layout.setContentsMargins(0, 0, 0, 0)

        layout.setSpacing(14)



        state = self._load_gateway_settings()



        mcp_card = QtWidgets.QFrame()

        mcp_card.setObjectName("launchCard")

        mcp_layout = QtWidgets.QVBoxLayout(mcp_card)

        mcp_layout.setContentsMargins(20, 18, 20, 18)

        mcp_layout.setSpacing(8)

        mcp_title = QtWidgets.QLabel("MCP 当前状态")

        mcp_title.setObjectName("launchTitle")

        mcp_layout.addWidget(mcp_title)

        self.gateway_mcp_enabled = QtWidgets.QCheckBox("启用本地 MCP Bridge（当前阶段主要是本地工具桥）")

        self.gateway_mcp_enabled.setChecked(bool(state["mcp_enabled"]))

        mcp_layout.addWidget(self.gateway_mcp_enabled)

        mcp_desc = QtWidgets.QLabel("当前已接好本地 MCP 工具桥。除 `plugin.list`、`chat.process`、`chat.gateway.dispatch` 外，还提供 `mcp.list_tools`、`mcp.server_status`、`mcp.call_tool` 等管理入口；远程工具会以 `mcp.<server>.<tool>` 形式暴露。")

        mcp_desc.setObjectName("launchDesc")

        mcp_desc.setWordWrap(True)

        mcp_layout.addWidget(mcp_desc)



        mcp_editor_desc = QtWidgets.QLabel("远程 MCP 服务器现在可以直接在这里新增、编辑和排序，不用再手填 JSON。")

        mcp_editor_desc.setObjectName("launchDesc")

        mcp_editor_desc.setWordWrap(True)

        mcp_layout.addWidget(mcp_editor_desc)



        mcp_toolbar = QtWidgets.QHBoxLayout()

        btn_add_stdio = QtWidgets.QPushButton("+ 本地进程")

        btn_add_stdio.clicked.connect(lambda checked=False: self._add_gateway_mcp_server("stdio"))

        mcp_toolbar.addWidget(btn_add_stdio)



        btn_add_http = QtWidgets.QPushButton("+ HTTP 服务器")

        btn_add_http.clicked.connect(lambda checked=False: self._add_gateway_mcp_server("streamable_http"))

        mcp_toolbar.addWidget(btn_add_http)



        self.gateway_mcp_edit_btn = QtWidgets.QPushButton("编辑所选")

        self.gateway_mcp_edit_btn.clicked.connect(self._edit_gateway_mcp_server)

        mcp_toolbar.addWidget(self.gateway_mcp_edit_btn)



        self.gateway_mcp_remove_btn = QtWidgets.QPushButton("删除所选")

        self.gateway_mcp_remove_btn.clicked.connect(self._remove_gateway_mcp_server)

        mcp_toolbar.addWidget(self.gateway_mcp_remove_btn)



        self.gateway_mcp_up_btn = QtWidgets.QPushButton("上移")

        self.gateway_mcp_up_btn.clicked.connect(lambda checked=False: self._move_gateway_mcp_server(-1))

        mcp_toolbar.addWidget(self.gateway_mcp_up_btn)



        self.gateway_mcp_down_btn = QtWidgets.QPushButton("下移")

        self.gateway_mcp_down_btn.clicked.connect(lambda checked=False: self._move_gateway_mcp_server(1))

        mcp_toolbar.addWidget(self.gateway_mcp_down_btn)

        mcp_toolbar.addStretch()

        mcp_layout.addLayout(mcp_toolbar)



        self.gateway_mcp_table = QtWidgets.QTableWidget()

        self.gateway_mcp_table.setColumnCount(6)

        self.gateway_mcp_table.setHorizontalHeaderLabels(["状态", "名称", "方式", "入口", "超时", "操作"])

        self.gateway_mcp_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)

        self.gateway_mcp_table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)

        self.gateway_mcp_table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)

        self.gateway_mcp_table.verticalHeader().setVisible(False)

        self.gateway_mcp_table.setAlternatingRowColors(True)

        self.gateway_mcp_table.setMinimumHeight(190)

        self.gateway_mcp_table.horizontalHeader().setStretchLastSection(False)

        self.gateway_mcp_table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)

        self.gateway_mcp_table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)

        self.gateway_mcp_table.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)

        self.gateway_mcp_table.horizontalHeader().setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeMode.Stretch)

        self.gateway_mcp_table.horizontalHeader().setSectionResizeMode(4, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)

        self.gateway_mcp_table.horizontalHeader().setSectionResizeMode(5, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)

        self.gateway_mcp_table.itemSelectionChanged.connect(self._update_gateway_mcp_actions)

        self.gateway_mcp_table.itemDoubleClicked.connect(lambda item: self._edit_gateway_mcp_server(item.row()))

        mcp_layout.addWidget(self.gateway_mcp_table)



        self._set_gateway_mcp_server_configs(state.get("mcp_server_configs") or [])



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

        access_desc = QtWidgets.QLabel("这里控制 `mcp_tools` 插件是否能被 QQ 侧触发。这个权限只影响 MCP，不影响普通 QQ 对话。")

        access_desc.setObjectName("launchDesc")

        access_desc.setWordWrap(True)

        access_layout.addWidget(access_desc)

        access = self._normalize_mcp_access_control(self._load_mcp_tools_config().get("access_control"))

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



        intent_route_desc = QtWidgets.QLabel("当消息同时命中“品牌词 + 动作词”时，优先将请求路由到 mcp_tools；如果命中“显式联网词”，则优先走联网搜索。")

        intent_route_desc.setObjectName("launchDesc")

        intent_route_desc.setWordWrap(True)

        intent_route_layout.addWidget(intent_route_desc)



        intent_route = self._load_mcp_intent_route_settings()

        self.gateway_mcp_intent_route_enabled = QtWidgets.QCheckBox("启用 MCP 自然语言优先路由")

        self.gateway_mcp_intent_route_enabled.setChecked(bool(intent_route.get("enabled", True)))

        intent_route_layout.addWidget(self.gateway_mcp_intent_route_enabled)



        intent_form = QtWidgets.QFormLayout()

        intent_form.setLabelAlignment(QtCore.Qt.AlignmentFlag.AlignRight)

        intent_form.setFormAlignment(QtCore.Qt.AlignmentFlag.AlignTop)

        intent_form.setHorizontalSpacing(14)

        intent_form.setVerticalSpacing(10)



        self.gateway_mcp_brand_keywords = QtWidgets.QPlainTextEdit("\n".join(intent_route.get("brand_keywords") or []))

        self.gateway_mcp_brand_keywords.setPlaceholderText("每行一个，如：麦当劳")

        self.gateway_mcp_brand_keywords.setFixedHeight(78)



        self.gateway_mcp_action_keywords = QtWidgets.QPlainTextEdit("\n".join(intent_route.get("action_keywords") or []))

        self.gateway_mcp_action_keywords.setPlaceholderText("每行一个，如：查一下、优惠券、领取")

        self.gateway_mcp_action_keywords.setFixedHeight(96)



        self.gateway_mcp_web_search_override_keywords = QtWidgets.QPlainTextEdit(

            "\n".join(intent_route.get("web_search_override_keywords") or [])

        )

        self.gateway_mcp_web_search_override_keywords.setPlaceholderText("每行一个，如：联网、百度、搜索")

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

        btn_save.clicked.connect(self._save_gateway_settings)

        footer.addWidget(btn_save)

        btn_save_restart = QtWidgets.QPushButton("保存并重启")

        btn_save_restart.clicked.connect(lambda checked=False: self._save_gateway_settings(restart_after_save=True))

        btn_save_restart.setEnabled(bool(getattr(self.main_app, "on_restart_callback", None)))

        footer.addWidget(btn_save_restart)

        footer.addStretch()

        layout.addLayout(footer)

        layout.addStretch()



        self.gateway_mcp_enabled.toggled.connect(self._refresh_gateway_preview)

        self.gateway_mcp_allow_local.toggled.connect(self._refresh_gateway_preview)

        self.gateway_mcp_allow_remote_qq.toggled.connect(self._refresh_gateway_preview)

        self.gateway_mcp_allow_qq_owner.toggled.connect(self._refresh_gateway_preview)

        self.gateway_mcp_allow_qq_others.toggled.connect(self._refresh_gateway_preview)

        self.gateway_mcp_intent_route_enabled.toggled.connect(self._refresh_gateway_preview)

        self.gateway_mcp_brand_keywords.textChanged.connect(self._refresh_gateway_preview)

        self.gateway_mcp_action_keywords.textChanged.connect(self._refresh_gateway_preview)

        self.gateway_mcp_web_search_override_keywords.textChanged.connect(self._refresh_gateway_preview)

        self._refresh_gateway_preview()



        scroll.setWidget(container)

        root.addWidget(scroll, 1)

        self.stack.addWidget(page)



    def _init_gateway_page(self):

        page = QtWidgets.QWidget()

        root = QtWidgets.QVBoxLayout(page)

        root.setContentsMargins(0, 0, 0, 0)



        scroll = QtWidgets.QScrollArea()

        scroll.setWidgetResizable(True)

        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)



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



        qq_desc = QtWidgets.QLabel("这里保存的是运行时接入参数。QQ 回复显示到本地聊天框的开关会立即用于新消息；NapCat 的监听地址、Token 与回发地址现在会尝试即时刷新，重启只作为兜底。QQ 对话默认沿用当前本地激活人设与口吻，不单独切人格。")

        qq_desc.setObjectName("launchDesc")

        qq_desc.setWordWrap(True)

        qq_layout.addWidget(qq_desc)



        qq_mode_hint = QtWidgets.QLabel("接入建议：本程序现在同端口同时支持 HTTP webhook 和反向 WebSocket；NapCat 任选其一接入即可。若你开了反向 WS，程序会优先走 WS 回消息，不通再回退到 HTTP API。")

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

        self.gateway_owner_ids = QtWidgets.QLineEdit(self._format_owner_ids(state["napcat_owner_user_ids"]))

        self.gateway_owner_ids.setPlaceholderText("填你的 QQ 号；多个用逗号或空格分隔")

        self.gateway_owner_label = QtWidgets.QLineEdit(state["napcat_owner_label"])

        self.gateway_owner_label.setPlaceholderText("主人")

        self.gateway_image_prompt = QtWidgets.QPlainTextEdit(state["napcat_image_prompt"])

        self.gateway_image_prompt.setFixedHeight(82)

        self.gateway_filter_mode = QtWidgets.QComboBox()

        self.gateway_filter_mode.addItem("不过滤", "off")

        self.gateway_filter_mode.addItem("白名单", "whitelist")

        self.gateway_filter_mode.addItem("黑名单", "blacklist")

        filter_index = self.gateway_filter_mode.findData(state["napcat_filter_mode"])

        self.gateway_filter_mode.setCurrentIndex(filter_index if filter_index >= 0 else 0)

        self.gateway_user_whitelist = QtWidgets.QLineEdit(self._format_owner_ids(state["napcat_user_whitelist"]))

        self.gateway_user_whitelist.setPlaceholderText("允许通过的 QQ 号；多个用逗号或空格分隔")

        self.gateway_user_blacklist = QtWidgets.QLineEdit(self._format_owner_ids(state["napcat_user_blacklist"]))

        self.gateway_user_blacklist.setPlaceholderText("拒绝通过的 QQ 号；多个用逗号或空格分隔")

        self.gateway_group_whitelist = QtWidgets.QLineEdit(self._format_owner_ids(state["napcat_group_whitelist"]))

        self.gateway_group_whitelist.setPlaceholderText("允许通过的群号；多个用逗号或空格分隔")

        self.gateway_group_blacklist = QtWidgets.QLineEdit(self._format_owner_ids(state["napcat_group_blacklist"]))

        self.gateway_group_blacklist.setPlaceholderText("拒绝通过的群号；多个用逗号或空格分隔")



        self.gateway_reply_enabled = QtWidgets.QCheckBox("允许副号回发消息")

        self.gateway_reply_enabled.setChecked(bool(state["napcat_reply_enabled"]))

        self.gateway_allow_private = QtWidgets.QCheckBox("允许私聊")

        self.gateway_allow_private.setChecked(bool(state["napcat_allow_private"]))

        self.gateway_allow_group = QtWidgets.QCheckBox("允许群聊")

        self.gateway_allow_group.setChecked(bool(state["napcat_allow_group"]))

        self.gateway_group_require_at = QtWidgets.QCheckBox("群聊仅响应 @我")

        self.gateway_group_require_at.setChecked(bool(state["napcat_group_require_at"]))

        self.gateway_image_vision_enabled = QtWidgets.QCheckBox("启用 QQ 图片识别")

        self.gateway_image_vision_enabled.setChecked(bool(state["napcat_image_vision_enabled"]))

        self.gateway_voice_reply_enabled = QtWidgets.QCheckBox("启用 QQ 概率语音回复（命中时优先发语音，失败回退文字）")

        self.gateway_voice_reply_enabled.setChecked(bool(state["napcat_voice_reply_enabled"]))

        self.gateway_ui_append = QtWidgets.QCheckBox("QQ 回复同步显示到本地聊天框")

        self.gateway_ui_append.setChecked(bool(state["remote_chat_ui_append"]))

        self.gateway_voice_reply_probability = QtWidgets.QSpinBox()

        self.gateway_voice_reply_probability.setRange(0, 100)

        self.gateway_voice_reply_probability.setSuffix(" %")

        self.gateway_voice_reply_probability.setValue(int(state["napcat_voice_reply_probability"]))

        self.gateway_voice_reply_probability.setEnabled(self.gateway_voice_reply_enabled.isChecked())



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

        btn_save_restart.clicked.connect(lambda checked=False: self._save_gateway_settings(restart_after_save=True))

        btn_save_restart.setEnabled(bool(getattr(self.main_app, "on_restart_callback", None)))

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



        qq_profile_desc = QtWidgets.QLabel("这里维护不同 QQ 用户的身份、关系、权限和记忆范围，方便她区分“你是谁”和“该共享哪段记忆”。")

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

        self.gateway_qq_profile_edit_btn.clicked.connect(lambda checked=False: self._edit_gateway_qq_profile())

        qq_profile_toolbar.addWidget(self.gateway_qq_profile_edit_btn)



        self.gateway_qq_profile_remove_btn = QtWidgets.QPushButton("删除")

        self.gateway_qq_profile_remove_btn.clicked.connect(lambda checked=False: self._delete_gateway_qq_profile())

        qq_profile_toolbar.addWidget(self.gateway_qq_profile_remove_btn)

        qq_profile_toolbar.addStretch()

        qq_profile_layout.addLayout(qq_profile_toolbar)



        self.gateway_qq_profile_table = QtWidgets.QTableWidget()

        self.gateway_qq_profile_table.setColumnCount(8)

        self.gateway_qq_profile_table.setHorizontalHeaderLabels(["主人", "QQ", "备注", "昵称", "关系", "权限", "身份摘要", "记忆范围"])

        self.gateway_qq_profile_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)

        self.gateway_qq_profile_table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)

        self.gateway_qq_profile_table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)

        self.gateway_qq_profile_table.verticalHeader().setVisible(False)

        self.gateway_qq_profile_table.setAlternatingRowColors(True)

        self.gateway_qq_profile_table.setMinimumHeight(230)

        self.gateway_qq_profile_table.horizontalHeader().setStretchLastSection(False)

        self.gateway_qq_profile_table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)

        self.gateway_qq_profile_table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)

        self.gateway_qq_profile_table.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)

        self.gateway_qq_profile_table.horizontalHeader().setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)

        self.gateway_qq_profile_table.horizontalHeader().setSectionResizeMode(4, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)

        self.gateway_qq_profile_table.horizontalHeader().setSectionResizeMode(5, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)

        self.gateway_qq_profile_table.horizontalHeader().setSectionResizeMode(6, QtWidgets.QHeaderView.ResizeMode.Stretch)

        self.gateway_qq_profile_table.horizontalHeader().setSectionResizeMode(7, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)

        self.gateway_qq_profile_table.itemSelectionChanged.connect(self._update_gateway_qq_profile_actions)

        self.gateway_qq_profile_table.itemDoubleClicked.connect(lambda item: self._edit_gateway_qq_profile())

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

        self.gateway_filter_mode.currentIndexChanged.connect(self._refresh_gateway_preview)

        self.gateway_voice_reply_enabled.toggled.connect(self.gateway_voice_reply_probability.setEnabled)

        self.gateway_voice_reply_enabled.toggled.connect(self._refresh_gateway_preview)

        self.gateway_voice_reply_probability.valueChanged.connect(self._refresh_gateway_preview)

        self._refresh_gateway_preview()



        scroll.setWidget(container)

        root.addWidget(scroll, 1)

        self.stack.addWidget(page)



    # ---------- Other tabs ----------

    def _init_costume_page(self):
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        # 在这里加上拉伸因子 1，让形象管理面板自适应撑满整个页面的垂直空间
        layout.addWidget(CharacterEditorWidget(self.main_app), 1)
        self.stack.addWidget(page)



    def _init_plugin_page(self):

        page = QtWidgets.QWidget()

        layout = QtWidgets.QVBoxLayout(page)

        layout.setContentsMargins(0, 0, 0, 0)

        plugin_manager = getattr(self.main_app, "plugin_manager", None) if self.main_app else None

        if plugin_manager is None:

            layout.addWidget(QtWidgets.QLabel("当前上下文不支持插件管理。"))

        else:

            widget = PluginManagerDialog(parent=page, plugin_manager=plugin_manager, main_app=self.main_app, embedded=True)

            layout.addWidget(widget, 1)

        self.stack.addWidget(page)



    def _init_memory_page(self):

        page = QtWidgets.QWidget()

        layout = QtWidgets.QVBoxLayout(page)

        layout.setContentsMargins(0, 0, 0, 0)

        widget = MemoryEditorDialog(parent=page, embedded=True)

        layout.addWidget(widget, 1)

        self.stack.addWidget(page)












