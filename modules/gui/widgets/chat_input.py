"""Multiline chat input: Enter sends, Shift+Enter inserts a newline."""

from __future__ import annotations

from typing import List, Optional

from PySide6 import QtCore, QtGui, QtWidgets


class ChatInput(QtWidgets.QPlainTextEdit):
    sendRequested = QtCore.Signal()

    def __init__(self, parent=None, *, history_limit: int = 50):
        super().__init__(parent)
        self.setObjectName("chatInput")
        self.setTabChangesFocus(True)
        self.setUndoRedoEnabled(True)
        self.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.document().setDocumentMargin(3)
        self.setFixedHeight(32)
        self._history: List[str] = []
        self._history_index = -1
        self._draft = ""
        self._history_limit = max(8, int(history_limit))
        self.textChanged.connect(self._adapt_height)

    def keyPressEvent(self, event: QtGui.QKeyEvent):
        key = event.key()
        mods = event.modifiers()
        if key in (QtCore.Qt.Key.Key_Return, QtCore.Qt.Key.Key_Enter):
            if mods & QtCore.Qt.KeyboardModifier.ShiftModifier:
                super().keyPressEvent(event)
                return
            self.sendRequested.emit()
            return
        if key == QtCore.Qt.Key.Key_Up and self._cursor_at_document_start():
            self._history_step(-1)
            return
        if key == QtCore.Qt.Key.Key_Down and self._cursor_at_document_end():
            self._history_step(1)
            return
        super().keyPressEvent(event)

    def take_text(self) -> str:
        text = self.toPlainText().strip()
        if text:
            self.remember(text)
        self.clear()
        self._history_index = -1
        self._draft = ""
        self._adapt_height()
        return text

    def remember(self, text: str) -> None:
        value = str(text or "").strip()
        if not value:
            return
        if self._history and self._history[-1] == value:
            return
        self._history.append(value)
        if len(self._history) > self._history_limit:
            self._history = self._history[-self._history_limit :]

    def _cursor_at_document_start(self) -> bool:
        cursor = self.textCursor()
        return cursor.position() == 0 and not cursor.hasSelection()

    def _cursor_at_document_end(self) -> bool:
        cursor = self.textCursor()
        return cursor.position() == len(self.toPlainText()) and not cursor.hasSelection()

    def _history_step(self, delta: int) -> None:
        if not self._history:
            return
        if self._history_index < 0:
            self._draft = self.toPlainText()
            index = len(self._history) - 1 if delta < 0 else -1
        else:
            index = self._history_index + delta
        if index < 0:
            self._history_index = -1
            self.setPlainText(self._draft)
            return
        if index >= len(self._history):
            return
        self._history_index = index
        self.setPlainText(self._history[index])
        cursor = self.textCursor()
        cursor.movePosition(QtGui.QTextCursor.MoveOperation.End)
        self.setTextCursor(cursor)

    def _adapt_height(self) -> None:
        doc = self.document()
        margins = self.contentsMargins()
        line = self.fontMetrics().lineSpacing()
        needed = int(doc.size().height()) + margins.top() + margins.bottom() + 8
        height = min(max(32, needed), line * 3 + 16)
        if abs(self.height() - height) >= 2:
            self.setFixedHeight(height)
