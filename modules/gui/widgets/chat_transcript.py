"""QQ-style chat transcript: left assistant, right user, centered system."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable, Optional

from PySide6 import QtCore, QtGui, QtWidgets

from modules.gui.styles import get_ui_palette

TIMESTAMP_GAP_SEC = 5 * 60
AVATAR_SIZE = 32


def _palette():
    try:
        return get_ui_palette()
    except Exception:
        return {
            "accent": "#0A84FF",
            "bg_card": "#FFFFFF",
            "bg_soft": "#F4F5F7",
            "text_primary": "#1D1D1F",
            "text_secondary": "#6E6E73",
            "border": "#D8DBE0",
        }


def _make_letter_avatar(
    letter: str, size: int = AVATAR_SIZE, bg_color: str = "#94A3B8"
) -> QtGui.QPixmap:
    pm = QtGui.QPixmap(size, size)
    pm.fill(QtCore.Qt.GlobalColor.transparent)
    painter = QtGui.QPainter(pm)
    painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
    painter.setBrush(QtGui.QColor(bg_color))
    painter.setPen(QtCore.Qt.PenStyle.NoPen)
    painter.drawEllipse(0, 0, size, size)
    painter.setPen(QtGui.QPen(QtGui.QColor("#FFFFFF")))
    font = painter.font()
    font.setBold(True)
    font.setPixelSize(max(12, size // 2))
    painter.setFont(font)
    painter.drawText(pm.rect(), QtCore.Qt.AlignmentFlag.AlignCenter, (letter or "?")[:1])
    painter.end()
    return pm


def load_avatar_pixmap(
    path_or_badge: Any,
    letter: str = "?",
    size: int = AVATAR_SIZE,
    bg_color: str = "#94A3B8",
) -> QtGui.QPixmap:
    scale = 1.0
    offset_x = 0.0
    offset_y = 0.0

    if isinstance(path_or_badge, dict):
        raw = str(path_or_badge.get("path") or "").strip()
        try:
            scale = float(path_or_badge.get("scale", 1.0) or 1.0)
        except (ValueError, TypeError):
            scale = 1.0
        try:
            offset_x = float(path_or_badge.get("offset_x", 0.0) or 0.0)
        except (ValueError, TypeError):
            offset_x = 0.0
        try:
            offset_y = float(path_or_badge.get("offset_y", 0.0) or 0.0)
        except (ValueError, TypeError):
            offset_y = 0.0
    else:
        raw = str(path_or_badge or "").strip()

    if raw:
        p = Path(raw)
        if not p.is_absolute():
            p = Path.cwd() / p
        if p.exists():
            image = QtGui.QPixmap(str(p))
            if not image.isNull() and image.width() > 0 and image.height() > 0:
                out = QtGui.QPixmap(size, size)
                out.fill(QtCore.Qt.GlobalColor.transparent)
                painter = QtGui.QPainter(out)
                painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
                painter.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform, True)

                path_clip = QtGui.QPainterPath()
                path_clip.addEllipse(0, 0, size, size)
                painter.setClipPath(path_clip)

                rect = QtCore.QRectF(0, 0, size, size)
                base = max(rect.width() / image.width(), rect.height() / image.height())
                w = image.width() * base * scale
                h = image.height() * base * scale
                cx = rect.center().x() - w / 2.0 + offset_x * (rect.width() / 2.0)
                cy = rect.center().y() - h / 2.0 + offset_y * (rect.height() / 2.0)
                painter.drawPixmap(
                    QtCore.QRectF(cx, cy, w, h),
                    image,
                    QtCore.QRectF(image.rect()),
                )
                painter.end()
                return out
    return _make_letter_avatar(letter, size, bg_color)


class _Bubble(QtWidgets.QLabel):
    # Stylesheet padding is 8x13; keep extra slack so CJK last glyph does not wrap.
    _PAD_X = 40
    _PAD_Y = 20

    def __init__(self, text: str, *, kind: str, parent=None):
        super().__init__(parent)
        self.setTextInteractionFlags(
            QtCore.Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.setObjectName(f"chatBubble_{kind}")
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Maximum,
            QtWidgets.QSizePolicy.Policy.Minimum,
        )
        self.setText(text)

    def setText(self, text: str) -> None:  # noqa: N802
        super().setText(str(text or ""))
        self._refresh_wrap()
        self.updateGeometry()

    def _max_content_width(self) -> int:
        max_w = self.maximumWidth()
        if max_w >= 16777215 or max_w <= 0:
            return 600
        return max_w

    def _unwrapped_width(self, raw_text: str) -> int:
        return self.fontMetrics().horizontalAdvance(raw_text)

    def _needs_wrap(self, raw_text: str) -> bool:
        if "\n" in raw_text:
            return True
        return self._unwrapped_width(raw_text) + self._PAD_X > self._max_content_width()

    def _refresh_wrap(self) -> None:
        raw_text = self.text()
        wrap = self._needs_wrap(raw_text)
        self.setWordWrap(wrap)
        if not wrap and raw_text:
            self.setMinimumWidth(min(self._max_content_width(), self._unwrapped_width(raw_text) + self._PAD_X))
        else:
            self.setMinimumWidth(0)

    def sizeHint(self) -> QtCore.QSize:
        raw_text = self.text()
        if not raw_text:
            return QtCore.QSize(36, 28)

        max_w = self._max_content_width()
        fm = self.fontMetrics()
        if not self._needs_wrap(raw_text):
            return QtCore.QSize(
                min(max_w, self._unwrapped_width(raw_text) + self._PAD_X),
                max(fm.height() + self._PAD_Y, 32),
            )

        wrap_w = max(60, max_w - self._PAD_X)
        wrapped_rect = fm.boundingRect(
            QtCore.QRect(0, 0, wrap_w, 100000),
            QtCore.Qt.TextFlag.TextWordWrap,
            raw_text,
        )
        final_w = min(max_w, max(wrapped_rect.width() + self._PAD_X, 60))
        final_h = max(wrapped_rect.height() + self._PAD_Y, 32)
        return QtCore.QSize(final_w, final_h)

    def append_text(self, extra: str) -> None:
        current = self.text()
        glue = "" if current.endswith("\n") or extra.startswith("\n") else ""
        self.setText(current + glue + extra)


class ChatTranscript(QtWidgets.QScrollArea):
    regenerateRequested = QtCore.Signal()
    newTopicRequested = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("chatTranscript")
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        inner = QtWidgets.QWidget()
        inner.setObjectName("chatTranscriptInner")
        self._layout = QtWidgets.QVBoxLayout(inner)
        self._layout.setContentsMargins(8, 8, 8, 8)
        self._layout.setSpacing(8)
        self._layout.addStretch(1)
        self.setWidget(inner)
        self._last_role = ""
        self._last_ts = 0.0
        self._last_assistant_bubble: Optional[_Bubble] = None
        self._last_user_text = ""
        self._assistant_name = "助手"
        self._assistant_avatar = ""
        self._user_name = "主人"
        self._user_avatar = ""
        self.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_menu)

    def set_assistant_identity(self, name: str, avatar_path: Any = "") -> None:
        self._assistant_name = str(name or "助手").strip() or "助手"
        if isinstance(avatar_path, dict):
            self._assistant_avatar = avatar_path
        else:
            self._assistant_avatar = str(avatar_path or "").strip()

    def set_user_identity(self, name: str, avatar_path: Any = "") -> None:
        self._user_name = str(name or "主人").strip() or "主人"
        if isinstance(avatar_path, dict):
            self._user_avatar = avatar_path
        else:
            self._user_avatar = str(avatar_path or "").strip()

    def _bubble_max_width(self) -> int:
        vw = self.viewport().width()
        if vw <= 0:
            vw = self.width() if self.width() > 0 else 600
        # QQ风格：允许气泡充分展开至视口约88%宽度，无需强行限制在一半，自然留出对侧微呼吸区
        return max(240, int(vw * 0.88))

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        max_w = self._bubble_max_width()
        for bubble in self.findChildren(_Bubble):
            bubble.setMaximumWidth(max_w)
            bubble._refresh_wrap()
            bubble.updateGeometry()
        if self.isVisible():
            self._scroll_to_bottom()

    def showEvent(self, event: QtGui.QShowEvent) -> None:
        super().showEvent(event)
        self._scroll_to_bottom()

    def append_message(self, role: str, text: str) -> None:
        body = str(text or "").replace("\r\n", "\n").rstrip("\n")
        if not body.strip() and str(role or "").lower() != "system":
            return
        role_key = str(role or "system").strip().lower()
        now = time.time()
        if (
            role_key == "user"
            and self._last_role == "user"
            and body.strip() == self._last_user_text
            and now - self._last_ts < 3
        ):
            return
        if now - self._last_ts >= TIMESTAMP_GAP_SEC or (
            self._last_role and role_key != self._last_role and role_key in {"user", "assistant"}
        ):
            if self._last_ts:
                self._add_time_label(now)
            elif not self._last_role:
                self._add_time_label(now)
        if role_key == "user":
            self._add_user(body)
            self._last_user_text = body.strip()
            self._last_assistant_bubble = None
        elif role_key == "assistant":
            bubble = self._add_assistant(body)
            self._last_assistant_bubble = bubble
        elif role_key == "topic":
            self._add_system("— 新话题 —")
            self._last_assistant_bubble = None
        else:
            self._add_system(body)
            self._last_assistant_bubble = None
        self._last_role = role_key
        self._last_ts = now
        self._scroll_to_bottom()

    def clear_messages(self) -> None:
        while self._layout.count() > 1:
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._last_role = ""
        self._last_ts = 0.0
        self._last_assistant_bubble = None

    def last_user_text(self) -> str:
        return self._last_user_text

    def mark_new_topic(self) -> None:
        self._last_assistant_bubble = None
        self.append_message("topic", "— 新话题 —")

    def _add_time_label(self, ts: float) -> None:
        row = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(row)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.addStretch(1)
        label = QtWidgets.QLabel(time.strftime("%H:%M", time.localtime(ts)))
        label.setObjectName("chatTimeLabel")
        label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(label)
        layout.addStretch(1)
        self._layout.insertWidget(self._layout.count() - 1, row)

    def _add_system(self, text: str) -> None:
        label = QtWidgets.QLabel(text)
        label.setObjectName("chatSystemLabel")
        label.setWordWrap(True)
        label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self._layout.insertWidget(self._layout.count() - 1, label)

    def _add_user(self, text: str) -> _Bubble:
        row = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(row)
        layout.setContentsMargins(6, 2, 4, 2)
        layout.setSpacing(8)
        layout.addStretch(1)

        col = QtWidgets.QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(2)

        name = QtWidgets.QLabel(self._user_name)
        name.setObjectName("chatUserName")
        name.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
        col.addWidget(name)

        bubble = _Bubble(text, kind="user")
        bubble.setMaximumWidth(self._bubble_max_width())
        bubble._refresh_wrap()
        col.addWidget(bubble, 0, QtCore.Qt.AlignmentFlag.AlignRight)

        layout.addLayout(col, 0)

        avatar = QtWidgets.QLabel()
        avatar.setFixedSize(AVATAR_SIZE, AVATAR_SIZE)
        pix = load_avatar_pixmap(
            self._user_avatar,
            letter=self._user_name[:1] if self._user_name else "主",
            bg_color="#60A5FA",
        )
        avatar.setPixmap(pix)
        layout.addWidget(avatar, 0, QtCore.Qt.AlignmentFlag.AlignTop)

        self._layout.insertWidget(self._layout.count() - 1, row)
        return bubble

    def _add_assistant(self, text: str) -> _Bubble:
        row = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(row)
        layout.setContentsMargins(4, 2, 6, 2)
        layout.setSpacing(8)

        avatar = QtWidgets.QLabel()
        avatar.setFixedSize(AVATAR_SIZE, AVATAR_SIZE)
        pix = load_avatar_pixmap(
            self._assistant_avatar,
            letter=self._assistant_name[:1] if self._assistant_name else "助",
            bg_color="#F472B6",
        )
        avatar.setPixmap(pix)
        layout.addWidget(avatar, 0, QtCore.Qt.AlignmentFlag.AlignTop)

        col = QtWidgets.QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(2)

        name = QtWidgets.QLabel(self._assistant_name)
        name.setObjectName("chatAssistantName")
        name.setAlignment(QtCore.Qt.AlignmentFlag.AlignLeft)
        col.addWidget(name)

        bubble = _Bubble(text, kind="assistant")
        bubble.setMaximumWidth(self._bubble_max_width())
        bubble._refresh_wrap()
        col.addWidget(bubble, 0, QtCore.Qt.AlignmentFlag.AlignLeft)

        layout.addLayout(col, 0)
        layout.addStretch(1)

        self._layout.insertWidget(self._layout.count() - 1, row)
        return bubble

    def scroll_to_bottom(self) -> None:
        self._scroll_to_bottom()

    def _scroll_to_bottom(self) -> None:
        bar = self.verticalScrollBar()
        def _jump() -> None:
            bar.setValue(bar.maximum())
        QtCore.QTimer.singleShot(0, _jump)
        QtCore.QTimer.singleShot(50, _jump)

    def _show_menu(self, pos: QtCore.QPoint) -> None:
        menu = QtWidgets.QMenu(self)
        copy_action = menu.addAction("复制")
        regen = menu.addAction("重新生成")
        topic = menu.addAction("开始新话题")
        chosen = menu.exec(self.mapToGlobal(pos))
        if chosen is copy_action:
            widget = self.childAt(pos)
            bubble = widget
            while bubble is not None and not isinstance(bubble, _Bubble):
                bubble = bubble.parentWidget()
            text = bubble.text() if isinstance(bubble, _Bubble) else ""
            if text:
                QtWidgets.QApplication.clipboard().setText(text)
        elif chosen is regen:
            self.regenerateRequested.emit()
        elif chosen is topic:
            self.newTopicRequested.emit()
