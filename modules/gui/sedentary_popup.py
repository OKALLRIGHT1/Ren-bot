from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


DEFAULT_SEDENTARY_POPUP_MESSAGE = (
    "已经连续使用 {app_name} {active_minutes} 分钟了，起来活动一下吧。"
)


@dataclass(frozen=True)
class SedentaryPopupOptions:
    enabled: bool
    title: str
    message: str
    image_path: Optional[str]
    snooze_minutes: int
    auto_close_seconds: int


class _FormatDict(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def _resolve_existing_image(path_value: Any) -> Optional[str]:
    raw = str(path_value or "").strip()
    if not raw:
        return None

    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    if path.is_file():
        return str(path)
    return None


def _safe_int(value: Any, default: int, minimum: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = default
    return max(minimum, parsed)


def build_sedentary_popup_options(
    cfg: Any,
    *,
    app_name: str,
    active_minutes: int,
    image_path_override: Any = None,
) -> SedentaryPopupOptions:
    message_template = str(
        getattr(cfg, "SEDENTARY_POPUP_MESSAGE", DEFAULT_SEDENTARY_POPUP_MESSAGE)
        or DEFAULT_SEDENTARY_POPUP_MESSAGE
    ).strip()
    format_values = _FormatDict(
        app_name=str(app_name or "当前应用"),
        active_minutes=max(0, int(active_minutes or 0)),
    )
    try:
        message = message_template.format_map(format_values)
    except Exception:
        message = message_template

    return SedentaryPopupOptions(
        enabled=bool(getattr(cfg, "SEDENTARY_POPUP_ENABLED", True)),
        title=str(getattr(cfg, "SEDENTARY_POPUP_TITLE", "久坐提醒") or "久坐提醒"),
        message=message,
        image_path=_resolve_existing_image(image_path_override)
        or _resolve_existing_image(getattr(cfg, "SEDENTARY_POPUP_IMAGE_PATH", "")),
        snooze_minutes=_safe_int(
            getattr(cfg, "SEDENTARY_POPUP_SNOOZE_MINUTES", 10),
            default=10,
            minimum=1,
        ),
        auto_close_seconds=_safe_int(
            getattr(cfg, "SEDENTARY_POPUP_AUTO_CLOSE_SECONDS", 20),
            default=20,
            minimum=0,
        ),
    )


def show_sedentary_popup_dialog(parent: Any, options: SedentaryPopupOptions) -> str:
    if not options.enabled:
        return "disabled"

    from PySide6 import QtCore, QtGui, QtWidgets

    dialog = QtWidgets.QDialog(parent)
    dialog.setWindowTitle(options.title)
    dialog.setWindowFlags(
        QtCore.Qt.WindowType.Dialog
        | QtCore.Qt.WindowType.WindowStaysOnTopHint
        | QtCore.Qt.WindowType.CustomizeWindowHint
        | QtCore.Qt.WindowType.WindowTitleHint
    )
    dialog.setModal(False)
    dialog.setMinimumWidth(360)
    dialog.setStyleSheet(
        """
        QDialog {
            background: #fbfaf7;
            border: 1px solid #d8d1c2;
            border-radius: 8px;
        }
        QLabel#titleLabel {
            color: #2f3a3d;
            font-size: 18px;
            font-weight: 700;
        }
        QLabel#messageLabel {
            color: #3f474a;
            font-size: 14px;
            line-height: 1.45;
        }
        QPushButton {
            min-width: 92px;
            min-height: 30px;
            padding: 5px 12px;
            border-radius: 6px;
            border: 1px solid #b9c2bd;
            background: #ffffff;
            color: #2f3a3d;
        }
        QPushButton#primaryButton {
            border-color: #3e7c71;
            background: #3e7c71;
            color: #ffffff;
            font-weight: 600;
        }
        """
    )

    result = {"value": "dismiss"}
    layout = QtWidgets.QVBoxLayout(dialog)
    layout.setContentsMargins(20, 18, 20, 16)
    layout.setSpacing(12)

    content_layout = QtWidgets.QHBoxLayout()
    content_layout.setSpacing(14)

    image_label = QtWidgets.QLabel()
    image_label.setFixedSize(112, 112)
    image_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
    image_label.setStyleSheet(
        "background:#f1eee6; border:1px solid #ded7c8; border-radius:8px; color:#6d7673;"
    )
    if options.image_path:
        image_path = str(options.image_path)
        if image_path.lower().endswith(".gif"):
            movie = QtGui.QMovie(image_path)
            movie.setScaledSize(QtCore.QSize(104, 104))
            image_label.setMovie(movie)
            image_label._sedentary_movie = movie
            movie.start()
        else:
            pixmap = QtGui.QPixmap(image_path)
            if not pixmap.isNull():
                image_label.setPixmap(
                    pixmap.scaled(
                        104,
                        104,
                        QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                        QtCore.Qt.TransformationMode.SmoothTransformation,
                    )
                )
            else:
                image_label.setText("图片不可用")
    else:
        image_label.setText("休息一下")
    content_layout.addWidget(image_label)

    text_layout = QtWidgets.QVBoxLayout()
    text_layout.setSpacing(8)
    title_label = QtWidgets.QLabel(options.title)
    title_label.setObjectName("titleLabel")
    message_label = QtWidgets.QLabel(options.message)
    message_label.setObjectName("messageLabel")
    message_label.setWordWrap(True)
    text_layout.addWidget(title_label)
    text_layout.addWidget(message_label)
    text_layout.addStretch(1)
    content_layout.addLayout(text_layout, 1)
    layout.addLayout(content_layout)

    button_layout = QtWidgets.QHBoxLayout()
    button_layout.addStretch(1)
    snooze_btn = QtWidgets.QPushButton(f"{options.snooze_minutes} 分钟后提醒")
    ok_btn = QtWidgets.QPushButton("知道了")
    ok_btn.setObjectName("primaryButton")
    button_layout.addWidget(snooze_btn)
    button_layout.addWidget(ok_btn)
    layout.addLayout(button_layout)

    def _accept() -> None:
        result["value"] = "dismiss"
        dialog.accept()

    def _snooze() -> None:
        result["value"] = "snooze"
        dialog.accept()

    ok_btn.clicked.connect(_accept)
    snooze_btn.clicked.connect(_snooze)
    if options.auto_close_seconds > 0:
        QtCore.QTimer.singleShot(options.auto_close_seconds * 1000, dialog.accept)

    dialog.adjustSize()
    screen = QtWidgets.QApplication.screenAt(QtGui.QCursor.pos())
    if screen is None:
        screen = QtWidgets.QApplication.primaryScreen()
    if screen is not None:
        geometry = screen.availableGeometry()
        frame = dialog.frameGeometry()
        frame.moveCenter(geometry.center())
        dialog.move(frame.topLeft())

    dialog.exec()
    return result["value"]
