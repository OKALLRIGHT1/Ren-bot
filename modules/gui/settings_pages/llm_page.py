"""Model catalog and task-router settings page."""

from __future__ import annotations

from typing import Callable, Optional

from PySide6 import QtCore, QtGui, QtWidgets

from modules.gui.settings_pages.catalog_io import (
    catalog_path,
    provider_label,
    retarget_model_id,
    save_model_catalog,
)
from modules.gui.utils import wrap_in_scroll_area

try:
    from config import LLM_ROUTER, MODELS
except ImportError:
    LLM_ROUTER = {}
    MODELS = {}

try:
    from modules.model_catalog import format_purposes_label, get_model_purposes
except Exception:

    def get_model_purposes(model_cfg):
        if not isinstance(model_cfg, dict):
            return []
        raw = model_cfg.get("purposes") or model_cfg.get("purpose") or []
        if isinstance(raw, str):
            return [x.strip() for x in raw.replace("，", ",").split(",") if x.strip()]
        if isinstance(raw, (list, tuple, set)):
            return [str(x).strip() for x in raw if str(x).strip()]
        return []

    def format_purposes_label(purposes):
        return "、".join(purposes) if purposes else "未设置"


class LlmSettingsPage(QtWidgets.QWidget):
    def __init__(
        self,
        parent=None,
        on_open_providers: Optional[Callable[[], None]] = None,
        plugin_manager=None,
    ):
        super().__init__(parent)
        self._on_open_providers = on_open_providers
        self._plugin_manager = plugin_manager
        self.router_ui = {}
        self.router_desc_ui = {}
        self._build_ui()
        self.reload_from_disk()

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        body = QtWidgets.QWidget()
        body_layout = QtWidgets.QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(10)

        top_card = QtWidgets.QFrame()
        top_card.setObjectName("settingsHeaderCard")
        top_layout = QtWidgets.QVBoxLayout(top_card)
        top_layout.setContentsMargins(14, 10, 14, 10)
        top_layout.setSpacing(8)

        title_wrap = QtWidgets.QVBoxLayout()
        title_wrap.setSpacing(2)
        title_wrap.addWidget(QtWidgets.QLabel("聊天模型", objectName="header"))
        page_hint = QtWidgets.QLabel(
            "先加一个聊天用的模型。提供商和 Key 可以在「添加模型」里一起填；任务路由给进阶使用。"
        )
        page_hint.setStyleSheet("color:#6B7280;")
        page_hint.setWordWrap(True)
        title_wrap.addWidget(page_hint)
        top_layout.addLayout(title_wrap)

        top_actions = QtWidgets.QHBoxLayout()
        top_actions.setContentsMargins(0, 0, 0, 0)
        top_actions.setSpacing(8)
        top_actions.addStretch()

        btn_routing_overview = QtWidgets.QPushButton("打开路由总览")
        btn_routing_overview.clicked.connect(self._open_routing_overview)
        top_actions.addWidget(btn_routing_overview)

        btn_provider = QtWidgets.QPushButton("管理提供商")
        btn_provider.clicked.connect(self._open_providers)
        btn_add = QtWidgets.QPushButton("+ 添加模型")
        btn_add.clicked.connect(self._on_add_model)
        top_actions.addWidget(btn_provider)
        top_actions.addWidget(btn_add)
        top_layout.addLayout(top_actions)
        body_layout.addWidget(top_card)

        model_card = QtWidgets.QFrame()
        model_card.setObjectName("settingsHeaderCard")
        model_layout = QtWidgets.QVBoxLayout(model_card)
        model_layout.setContentsMargins(14, 10, 14, 10)
        model_layout.setSpacing(6)

        model_head = QtWidgets.QHBoxLayout()
        model_head.addWidget(QtWidgets.QLabel("模型清单", objectName="header"))
        model_head.addStretch()
        model_tip = QtWidgets.QLabel("维护模型清单，路由区只引用模型 ID。")
        model_tip.setStyleSheet("color:#6B7280;")
        model_tip.setWordWrap(True)
        model_layout.addLayout(model_head)
        model_layout.addWidget(model_tip)

        self.llm_table = QtWidgets.QTableWidget()
        self.llm_table.setColumnCount(6)
        self.llm_table.setHorizontalHeaderLabels(
            ["ID", "模型名", "供应商", "用途", "API 地址", "操作"]
        )
        header = self.llm_table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setMinimumSectionSize(48)
        # 列宽策略：
        # - ID/模型名/API 地址是辨认模型的关键，尽量给空间；
        # - 模型名与 API 地址共享剩余宽度（都设为 Stretch）；
        # - 操作列只放两个按钮，收紧到最小可用宽度。
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(4, QtWidgets.QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(5, QtWidgets.QHeaderView.ResizeMode.Fixed)
        self.llm_table.setColumnWidth(0, 110)
        self.llm_table.setColumnWidth(2, 76)
        self.llm_table.setColumnWidth(3, 120)
        self.llm_table.setColumnWidth(5, 124)
        # 省略号从尾部截断：保留开头最可辨认的部分
        # （如 “gemini-3-fla…” / “https://api.tomo…”），不再用中间省略。
        self.llm_table.setTextElideMode(QtCore.Qt.TextElideMode.ElideRight)
        self.llm_table.setWordWrap(False)
        self.llm_table.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.llm_table.setHorizontalScrollMode(
            QtWidgets.QAbstractItemView.ScrollMode.ScrollPerPixel
        )
        self.llm_table.setShowGrid(False)
        self.llm_table.setAlternatingRowColors(True)
        self.llm_table.verticalHeader().setVisible(False)
        self.llm_table.setMinimumHeight(160)
        self.llm_table.verticalHeader().setDefaultSectionSize(34)
        model_layout.addWidget(self.llm_table)
        body_layout.addWidget(model_card)

        route_card = QtWidgets.QFrame()
        route_card.setObjectName("settingsHeaderCard")
        route_layout = QtWidgets.QVBoxLayout(route_card)
        route_layout.setContentsMargins(14, 10, 14, 10)
        route_layout.setSpacing(6)
        route_layout.addWidget(QtWidgets.QLabel("任务路由", objectName="header"))
        route_hint = QtWidgets.QLabel("日常优先关注闲聊、推理、总结。")
        route_hint.setStyleSheet("color:#6B7280; font-size:11px;")
        route_hint.setWordWrap(True)
        route_layout.addWidget(route_hint)

        route_grid = QtWidgets.QGridLayout()
        route_grid.setContentsMargins(0, 0, 0, 0)
        route_grid.setHorizontalSpacing(8)
        route_grid.setVerticalSpacing(8)
        route_grid.setColumnStretch(0, 1)
        route_grid.setColumnStretch(1, 1)

        route_items = [
            ("default", "闲聊", "主回复"),
            ("reply_polish", "润色", "回复润色/情绪兜底"),
            ("tool_reasoning", "推理", "工具规划"),
            ("summary", "总结", "摘要压缩"),
            ("gatekeeper", "看门人", "是否触发"),
            ("translation", "翻译", "语言转换"),
            ("screen_classify", "屏幕分类", "画面分类"),
            ("image_gen", "画图", "点编辑自选顺序，不会自动全选"),
            ("image_edit", "图生图", "点编辑自选顺序，空则回退画图链"),
        ]
        for index, (task, label, desc) in enumerate(route_items):
            row_card = QtWidgets.QFrame()
            row_card.setObjectName("launchCard")
            row_layout = QtWidgets.QVBoxLayout(row_card)
            row_layout.setContentsMargins(8, 6, 8, 6)
            row_layout.setSpacing(3)

            head = QtWidgets.QHBoxLayout()
            head.setSpacing(6)
            title = QtWidgets.QLabel(label)
            title.setObjectName("launchTitle")
            title.setStyleSheet("font-size: 13px; font-weight: 700;")
            desc_label = QtWidgets.QLabel(desc)
            desc_label.setObjectName("launchDesc")
            desc_label.setStyleSheet("color: #6B7280; font-size: 11px;")
            desc_label.setToolTip(desc)
            self.router_desc_ui[task] = desc_label
            head.addWidget(title)
            head.addWidget(desc_label, 1)
            row_layout.addLayout(head)

            line_wrap = QtWidgets.QHBoxLayout()
            line_wrap.setContentsMargins(0, 0, 0, 0)
            line_wrap.setSpacing(6)
            line = QtWidgets.QLineEdit()
            line.setReadOnly(True)
            line.setPlaceholderText("未配置")
            line.setFixedHeight(28)
            line.setStyleSheet("background:#f3f4f6; font-size: 11px; padding: 2px 6px;")
            self.router_ui[task] = line
            btn = QtWidgets.QPushButton("编辑")
            btn.setObjectName("routerConfigBtn")
            btn.setFixedHeight(28)
            btn.clicked.connect(lambda c=False, t=task: self._on_conf_router(t))
            line_wrap.addWidget(line, 1)
            line_wrap.addWidget(btn)
            row_layout.addLayout(line_wrap)
            route_grid.addWidget(row_card, index // 2, index % 2)

        route_layout.addLayout(route_grid)
        body_layout.addWidget(route_card)
        body_layout.addStretch(1)
        layout.addWidget(wrap_in_scroll_area(body), 1)

        llm_action_bar = QtWidgets.QFrame()
        llm_action_bar.setObjectName("settingsActionBar")
        llm_action_shadow = QtWidgets.QGraphicsDropShadowEffect(llm_action_bar)
        llm_action_shadow.setBlurRadius(18)
        llm_action_shadow.setOffset(0, 4)
        llm_action_shadow.setColor(QtGui.QColor(15, 23, 42, 28))
        llm_action_bar.setGraphicsEffect(llm_action_shadow)
        llm_action_layout = QtWidgets.QHBoxLayout(llm_action_bar)
        llm_action_layout.setContentsMargins(14, 10, 14, 10)
        llm_action_layout.setSpacing(10)

        action_hint = QtWidgets.QLabel(f"保存到 {catalog_path()}。")
        action_hint.setObjectName("settingsPageDesc")
        action_hint.setWordWrap(True)
        llm_action_layout.addWidget(action_hint, 1)

        btn_reload = QtWidgets.QPushButton("刷新路由")
        btn_reload.clicked.connect(self.refresh_router)
        btn_reload_models = QtWidgets.QPushButton("刷新模型")
        btn_reload_models.clicked.connect(self.refresh_models)
        btn_save_models = QtWidgets.QPushButton("保存当前配置")
        btn_save_models.setObjectName("primaryAction")
        btn_save_models.clicked.connect(self.save_state)
        llm_action_layout.addWidget(btn_reload)
        llm_action_layout.addWidget(btn_reload_models)
        llm_action_layout.addWidget(btn_save_models)
        layout.addWidget(llm_action_bar, 0)

    def reload_from_disk(self):
        self.refresh_models()
        self.refresh_router()

    def refresh_models(self):
        self.llm_table.setRowCount(0)
        for mid, cfg in MODELS.items():
            row = self.llm_table.rowCount()
            self.llm_table.insertRow(row)
            self.llm_table.setItem(row, 0, QtWidgets.QTableWidgetItem(str(mid)))
            self.llm_table.setItem(
                row, 1, QtWidgets.QTableWidgetItem(str((cfg or {}).get("model", "")))
            )
            self.llm_table.setItem(
                row, 2, QtWidgets.QTableWidgetItem(provider_label(cfg))
            )
            purpose_text = format_purposes_label(get_model_purposes(cfg or {}))
            self.llm_table.setItem(row, 3, QtWidgets.QTableWidgetItem(purpose_text))
            self.llm_table.setItem(
                row, 4, QtWidgets.QTableWidgetItem(str((cfg or {}).get("base_url", "")))
            )

            action_widget = QtWidgets.QWidget()
            h = QtWidgets.QHBoxLayout(action_widget)
            h.setContentsMargins(4, 2, 4, 2)
            h.setSpacing(6)
            h.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)

            btn_edit = QtWidgets.QPushButton("编辑")
            btn_edit.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            btn_edit.clicked.connect(lambda c=False, m=mid: self._on_edit_model(m))

            btn_del = QtWidgets.QPushButton("删除")
            btn_del.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            btn_edit.setObjectName("tableActionBtn")
            btn_del.setObjectName("tableDangerBtn")
            btn_del.clicked.connect(lambda c=False, m=mid: self._on_del_model(m))
            h.addWidget(btn_edit)
            h.addWidget(btn_del)

            for col in (0, 1, 2, 4):
                item = self.llm_table.item(row, col)
                if item is not None and item.text():
                    item.setToolTip(item.text())
            self.llm_table.setCellWidget(row, 5, action_widget)

    def refresh_router(self):
        for task, line in self.router_ui.items():
            chain = LLM_ROUTER.get(task, [])
            if isinstance(chain, str):
                chain = [chain]
            line.setText(" -> ".join(chain) if chain else "(未配置)")

    def save_state(self):
        save_model_catalog(parent=self)
        QtWidgets.QMessageBox.information(
            self, "模型与路由", "当前模型与路由配置已保存。"
        )

    def _open_providers(self):
        if callable(self._on_open_providers):
            self._on_open_providers()

    def _model_dialog(self, *args, **kwargs):
        from modules.gui.dialogs.settings import ModelEditDialog

        return ModelEditDialog(*args, **kwargs)

    def _router_dialog(self, *args, **kwargs):
        from modules.gui.dialogs.settings import RouterConfigDialog

        return RouterConfigDialog(*args, **kwargs)

    def _on_add_model(self):
        dlg = self._model_dialog(self, on_provider_saved=lambda: save_model_catalog(parent=self))
        if dlg.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            data = dlg.result_data
            mid = data["id"]
            if mid in MODELS:
                QtWidgets.QMessageBox.warning(self, "错误", "ID 已存在")
                return
            MODELS[mid] = data["config"]
            save_model_catalog(parent=self)
            self.refresh_models()

    def _on_edit_model(self, mid):
        dlg = self._model_dialog(
            self, mid, MODELS.get(mid, {}), on_provider_saved=lambda: save_model_catalog(parent=self)
        )
        if dlg.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            data = dlg.result_data or {}
            new_id = str(data.get("id") or "").strip()
            cfg = data.get("config") if isinstance(data.get("config"), dict) else {}
            if not new_id:
                QtWidgets.QMessageBox.warning(self, "错误", "模型 ID 不能为空")
                return
            if new_id != mid:
                result = retarget_model_id(
                    mid,
                    new_id,
                    plugin_manager=self._plugin_manager,
                )
                if not result.get("ok"):
                    if result.get("error") == "id_exists":
                        QtWidgets.QMessageBox.warning(
                            self, "错误", f"ID {new_id} 已存在"
                        )
                    else:
                        QtWidgets.QMessageBox.warning(
                            self, "错误", "无法重命名模型 ID"
                        )
                    return
            MODELS[new_id] = cfg
            save_model_catalog(parent=self)
            self.refresh_models()
            self.refresh_router()

    def _on_del_model(self, mid):
        reply = QtWidgets.QMessageBox.question(
            self,
            "删除",
            f"删除模型 {mid} 吗？",
            QtWidgets.QMessageBox.StandardButton.Yes
            | QtWidgets.QMessageBox.StandardButton.No,
        )
        if reply == QtWidgets.QMessageBox.StandardButton.Yes:
            MODELS.pop(mid, None)
            save_model_catalog(parent=self)
            self.refresh_models()

    def _on_conf_router(self, task):
        chain = LLM_ROUTER.get(task, [])
        if isinstance(chain, str):
            chain = [chain]
        dlg = self._router_dialog(self, task, list(chain))
        if dlg.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            LLM_ROUTER[task] = dlg.result_chain
            save_model_catalog(parent=self)
            self.refresh_router()

    def _open_routing_overview(self):
        from modules.gui.dialogs.model_routing_overview import ModelRoutingOverviewDialog

        if not hasattr(self, "_routing_overview_dlg") or self._routing_overview_dlg is None:
            self._routing_overview_dlg = ModelRoutingOverviewDialog(self)
        else:
            self._routing_overview_dlg._refresh()
        self._routing_overview_dlg.show()
        self._routing_overview_dlg.raise_()
        self._routing_overview_dlg.activateWindow()
