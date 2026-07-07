from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets
from modules.gui.status_screen_codec import text_to_bitmap_hex


def image_to_icon_bits(image: QtGui.QImage) -> tuple[str, tuple[int, int], str]:
    scaled = image.scaled(
        32,
        32,
        QtCore.Qt.AspectRatioMode.KeepAspectRatio,
        QtCore.Qt.TransformationMode.FastTransformation,
    )
    canvas = QtGui.QImage(32, 32, QtGui.QImage.Format.Format_ARGB32)
    canvas.fill(QtGui.QColor("white"))
    painter = QtGui.QPainter(canvas)
    x = (32 - scaled.width()) // 2
    y = (32 - scaled.height()) // 2
    painter.drawImage(x, y, scaled)
    painter.end()

    bits = []
    for yy in range(32):
        byte_val = 0
        bit_count = 0
        for xx in range(32):
            color = QtGui.QColor(canvas.pixel(xx, yy))
            gray = (color.red() + color.green() + color.blue()) // 3
            bit = 1 if gray < 190 else 0
            byte_val = (byte_val << 1) | bit
            bit_count += 1
            if bit_count == 8:
                bits.append(f"{byte_val:02x}")
                byte_val = 0
                bit_count = 0
    rgb565 = []
    for yy in range(32):
        for xx in range(32):
            color = QtGui.QColor(canvas.pixel(xx, yy))
            r = color.red() >> 3
            g = color.green() >> 2
            b = color.blue() >> 3
            value = (r << 11) | (g << 5) | b
            rgb565.append(f"{value:04x}")
    return "".join(bits), (32, 32), "".join(rgb565)


class StatusScreenManagerDialog(QtWidgets.QDialog):
    def __init__(self, parent=None, main_app=None):
        super().__init__(parent)
        self.main_app = main_app
        self._icon_bits = ""
        self._icon_size = (0, 0)
        self._icon_rgb565 = ""
        self._default_icon_bits = ""
        self._default_icon_size = (0, 0)
        self._default_icon_rgb565 = ""
        self._emotion_icons: dict[str, dict] = {}
        self.setWindowTitle("状态屏管理")
        self.resize(760, 620)
        self.setMinimumSize(680, 520)
        self._setup_ui()
        self._load_saved_config()
        self._refresh_ready_state()

    def _backend_app(self):
        candidate = getattr(self.main_app, "app", None)
        if candidate is not None:
            return candidate
        return self.main_app

    def _refresh_ready_state(self):
        backend = self._backend_app()
        if backend and hasattr(backend, "get_display_mqtt_status_text"):
            self.ready_label.setText(f"状态：{backend.get_display_mqtt_status_text()}")
        elif backend and hasattr(backend, "is_display_mqtt_ready"):
            ready = bool(backend.is_display_mqtt_ready())
            self.ready_label.setText(
                "状态：已连接 Mosquitto，可推送" if ready else "状态：MQTT 未连接"
            )
        else:
            self.ready_label.setText("状态：主程序未就绪")

    def _setup_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        title = QtWidgets.QLabel("ESP32 状态屏")
        title.setStyleSheet("font-size: 20px; font-weight: 700; color: #111827;")
        layout.addWidget(title)

        desc = QtWidgets.QLabel(
            "支持自动状态推送、默认表情图、情绪差分图，以及右下指标模式切换。"
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #4B5563; font-size: 13px;")
        layout.addWidget(desc)

        self.ready_label = QtWidgets.QLabel("状态：检查中...")
        self.ready_label.setStyleSheet(
            "color: #2563EB; font-size: 12px; font-weight: 600;"
        )
        layout.addWidget(self.ready_label)

        form = QtWidgets.QFormLayout()
        self.role_edit = QtWidgets.QLineEdit("丰川祥子")
        self.emotion_edit = QtWidgets.QLineEdit("[happy]")
        self.status_edit = QtWidgets.QLineEdit("欢迎回来")
        self.metric_edit = QtWidgets.QLineEdit("RAM 42%")
        self.metric_mode = QtWidgets.QComboBox()
        self.metric_mode.addItems(["auto_ram", "status_priority", "custom"])
        form.addRow("角色:", self.role_edit)
        form.addRow("情绪:", self.emotion_edit)
        form.addRow("状态:", self.status_edit)
        form.addRow("右下指标:", self.metric_edit)
        form.addRow("指标模式:", self.metric_mode)
        layout.addLayout(form)

        default_row = QtWidgets.QHBoxLayout()
        self.default_icon_info = QtWidgets.QLabel("未设置默认表情图")
        btn_pick_default = QtWidgets.QPushButton("选择默认图")
        btn_clear_default = QtWidgets.QPushButton("清除默认图")
        btn_pick_default.clicked.connect(self._pick_default_icon)
        btn_clear_default.clicked.connect(self._clear_default_icon)
        default_row.addWidget(self.default_icon_info, 1)
        default_row.addWidget(btn_pick_default)
        default_row.addWidget(btn_clear_default)
        layout.addLayout(default_row)

        emo_box = QtWidgets.QGroupBox("情绪差分图")
        emo_layout = QtWidgets.QVBoxLayout(emo_box)
        row = QtWidgets.QHBoxLayout()
        self.emo_name = QtWidgets.QLineEdit()
        self.emo_name.setPlaceholderText("例如 happy / sad / angry / think")
        btn_pick_emo = QtWidgets.QPushButton("选择情绪图")
        btn_pick_emo.clicked.connect(self._pick_emotion_icon)
        btn_remove_emo = QtWidgets.QPushButton("删除选中情绪图")
        btn_remove_emo.clicked.connect(self._remove_emotion_icon)
        row.addWidget(self.emo_name, 1)
        row.addWidget(btn_pick_emo)
        row.addWidget(btn_remove_emo)
        emo_layout.addLayout(row)
        self.emo_table = QtWidgets.QTableWidget(0, 2)
        self.emo_table.setHorizontalHeaderLabels(["情绪", "已配置"])
        self.emo_table.horizontalHeader().setStretchLastSection(True)
        self.emo_table.verticalHeader().setVisible(False)
        emo_layout.addWidget(self.emo_table)
        layout.addWidget(emo_box)

        icon_row = QtWidgets.QHBoxLayout()
        self.icon_info = QtWidgets.QLabel(
            "未设置这次手动发送的临时图片，将按默认/差分规则显示"
        )
        self.icon_info.setStyleSheet("color: #6B7280;")
        btn_pick = QtWidgets.QPushButton("选择本次图片")
        btn_clear = QtWidgets.QPushButton("清除本次图片")
        btn_pick.clicked.connect(self._pick_icon)
        btn_clear.clicked.connect(self._clear_icon)
        icon_row.addWidget(self.icon_info, 1)
        icon_row.addWidget(btn_pick)
        icon_row.addWidget(btn_clear)
        layout.addLayout(icon_row)

        self.preview = QtWidgets.QLabel()
        self.preview.setFixedSize(96, 96)
        self.preview.setStyleSheet("border: 1px solid #D1D5DB; background: #FFF;")
        self.preview.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.preview, alignment=QtCore.Qt.AlignmentFlag.AlignLeft)

        note = QtWidgets.QLabel(
            "默认图用于未配置差分的情绪；手动发送时若选择了临时图片，会优先覆盖本次显示。"
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #6B7280; font-size: 12px;")
        layout.addWidget(note)

        btns = QtWidgets.QHBoxLayout()
        btns.addStretch()
        btn_auto = QtWidgets.QPushButton("推送当前主程序状态")
        btn_auto.clicked.connect(self._send_current_runtime_state)
        btn_save = QtWidgets.QPushButton("保存默认规则")
        btn_save.clicked.connect(self._save_config)
        btn_send = QtWidgets.QPushButton("发送到状态屏")
        btn_send.setObjectName("save_btn")
        btn_send.clicked.connect(self._send_payload)
        btn_close = QtWidgets.QPushButton("关闭")
        btn_close.clicked.connect(self.accept)
        btns.addWidget(btn_auto)
        btns.addWidget(btn_save)
        btns.addWidget(btn_send)
        btns.addWidget(btn_close)
        layout.addLayout(btns)

    def _pick_image_file(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "选择表情图片",
            "",
            "图片文件 (*.png *.jpg *.jpeg *.bmp *.webp);;所有文件 (*.*)",
        )
        if not path:
            return None, None, None
        image = QtGui.QImage(path)
        if image.isNull():
            QtWidgets.QMessageBox.warning(self, "失败", "无法读取图片。")
            return None, None, None, None
        bits, size, rgb565 = image_to_icon_bits(image)
        preview = QtGui.QPixmap.fromImage(image.scaled(96, 96))
        return path, bits, size, rgb565, preview

    def _pick_icon(self):
        result = self._pick_image_file()
        if not result or result[0] is None:
            return
        path, bits, size, rgb565, preview = result
        self._icon_bits = bits
        self._icon_size = size
        self._icon_rgb565 = rgb565
        self.preview.setPixmap(preview)
        self.icon_info.setText(f"已设置本次图片：{path}")

    def _clear_icon(self):
        self._icon_bits = ""
        self._icon_size = (0, 0)
        self._icon_rgb565 = ""
        self.preview.clear()
        self.icon_info.setText("未设置这次手动发送的临时图片，将按默认/差分规则显示")

    def _pick_default_icon(self):
        result = self._pick_image_file()
        if not result or result[0] is None:
            return
        path, bits, size, rgb565, _preview = result
        self._default_icon_bits = bits
        self._default_icon_size = size
        self._default_icon_rgb565 = rgb565
        self.default_icon_info.setText(f"默认图：{path}")

    def _clear_default_icon(self):
        self._default_icon_bits = ""
        self._default_icon_size = (0, 0)
        self._default_icon_rgb565 = ""
        self.default_icon_info.setText("未设置默认表情图")

    def _pick_emotion_icon(self):
        emo = self.emo_name.text().strip().lower().strip("[]")
        if not emo:
            QtWidgets.QMessageBox.information(self, "提示", "请先输入情绪名。")
            return
        result = self._pick_image_file()
        if not result or result[0] is None:
            return
        path, bits, size, rgb565, _preview = result
        self._emotion_icons[emo] = {
            "path": path,
            "icon_bits": bits,
            "icon_rgb565": rgb565,
            "icon_w": size[0],
            "icon_h": size[1],
        }
        self._refresh_emotion_table()

    def _remove_emotion_icon(self):
        rows = self.emo_table.selectionModel().selectedRows()
        if not rows:
            return
        emo = self.emo_table.item(rows[0].row(), 0).text().strip()
        self._emotion_icons.pop(emo, None)
        self._refresh_emotion_table()

    def _refresh_emotion_table(self):
        self.emo_table.setRowCount(0)
        for emo, cfg in sorted(self._emotion_icons.items()):
            row = self.emo_table.rowCount()
            self.emo_table.insertRow(row)
            self.emo_table.setItem(row, 0, QtWidgets.QTableWidgetItem(emo))
            self.emo_table.setItem(
                row, 1, QtWidgets.QTableWidgetItem(cfg.get("path", ""))
            )

    def _config_payload(self):
        return {
            "metric_mode": self.metric_mode.currentText().strip(),
            "metric_text": self.metric_edit.text().strip(),
            "default_icon_bits": self._default_icon_bits,
            "default_icon_rgb565": self._default_icon_rgb565,
            "default_icon_w": self._default_icon_size[0],
            "default_icon_h": self._default_icon_size[1],
            "emotion_icons": {
                emo: {
                    "icon_bits": cfg.get("icon_bits", ""),
                    "icon_rgb565": cfg.get("icon_rgb565", ""),
                    "icon_w": cfg.get("icon_w", 0),
                    "icon_h": cfg.get("icon_h", 0),
                }
                for emo, cfg in self._emotion_icons.items()
            },
        }

    def _load_saved_config(self):
        backend = self._backend_app()
        if not backend or not hasattr(backend, "load_display_state_config"):
            return
        cfg = backend.load_display_state_config()
        self.metric_mode.setCurrentText(str(cfg.get("metric_mode", "auto_ram")))
        self.metric_edit.setText(str(cfg.get("metric_text", "")))
        self._default_icon_bits = str(cfg.get("default_icon_bits", ""))
        self._default_icon_rgb565 = str(cfg.get("default_icon_rgb565", ""))
        self._default_icon_size = (
            int(cfg.get("default_icon_w", 0) or 0),
            int(cfg.get("default_icon_h", 0) or 0),
        )
        self._emotion_icons = {}
        for emo, icon_cfg in (cfg.get("emotion_icons") or {}).items():
            self._emotion_icons[emo] = {
                "path": f"已保存({emo})",
                "icon_bits": icon_cfg.get("icon_bits", ""),
                "icon_rgb565": icon_cfg.get("icon_rgb565", ""),
                "icon_w": icon_cfg.get("icon_w", 0),
                "icon_h": icon_cfg.get("icon_h", 0),
            }
        if self._default_icon_bits:
            self.default_icon_info.setText("默认图：已保存")
        self._refresh_emotion_table()

    def _save_config(self):
        backend = self._backend_app()
        if not backend or not hasattr(backend, "save_display_state_config"):
            QtWidgets.QMessageBox.warning(self, "失败", "主程序未就绪，无法保存配置。")
            return
        try:
            backend.save_display_state_config(self._config_payload())
            QtWidgets.QMessageBox.information(self, "成功", "状态屏默认规则已保存。")
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "失败", f"保存失败: {e}")

    def _send_payload(self):
        backend = self._backend_app()
        if not backend or not hasattr(backend, "publish_display_state"):
            QtWidgets.QMessageBox.warning(self, "失败", "主程序未就绪，无法发送状态。")
            return
        if (
            hasattr(backend, "is_display_mqtt_ready")
            and not backend.is_display_mqtt_ready()
        ):
            QtWidgets.QMessageBox.warning(
                self, "失败", "状态屏 MQTT 未连接，请先确认主程序已连接 Mosquitto。"
            )
            return
        payload = {
            "role": self.role_edit.text().strip() or "未命名角色",
            "emotion": self.emotion_edit.text().strip() or "[idle]",
            "status": self.status_edit.text().strip() or "Ready",
            "metric": self.metric_edit.text().strip() or "",
        }
        if self._icon_bits and self._icon_size[0] > 0 and self._icon_size[1] > 0:
            payload["icon_bits"] = self._icon_bits
            payload["icon_rgb565"] = self._icon_rgb565
            payload["icon_w"] = self._icon_size[0]
            payload["icon_h"] = self._icon_size[1]
        try:
            backend.publish_display_state(payload)
            QtWidgets.QMessageBox.information(self, "成功", "已发送到状态屏。")
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "失败", f"发送失败: {e}")

    def _send_current_runtime_state(self):
        backend = self._backend_app()
        qt_ui = getattr(backend, "qt_ui", None)
        if qt_ui is None:
            QtWidgets.QMessageBox.warning(self, "失败", "当前 GUI 未就绪。")
            return
        if (
            hasattr(backend, "is_display_mqtt_ready")
            and not backend.is_display_mqtt_ready()
        ):
            QtWidgets.QMessageBox.warning(
                self, "失败", "状态屏 MQTT 未连接，请先确认主程序已连接 Mosquitto。"
            )
            return
        try:
            if hasattr(qt_ui, "publish_display_snapshot"):
                qt_ui.publish_display_snapshot()
            QtWidgets.QMessageBox.information(
                self, "成功", "已推送当前主程序状态到状态屏。"
            )
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "失败", f"推送失败: {e}")
