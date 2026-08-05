"""Local Qt confirmation dialog for ActionGate high-risk actions."""

from __future__ import annotations

from typing import Any


def show_action_confirm_dialog(
    parent: Any,
    *,
    title: str,
    summary: str,
) -> str:
    """Show a modal confirm dialog.

    Returns:
        "confirm" | "cancel" | "error"
    """
    try:
        from PySide6 import QtCore, QtWidgets
    except Exception:
        return "error"

    dialog = QtWidgets.QMessageBox(parent)
    dialog.setIcon(QtWidgets.QMessageBox.Icon.Warning)
    dialog.setWindowTitle(str(title or "确认操作").strip() or "确认操作")
    dialog.setText("需要你的确认后才能继续执行。")
    body = str(summary or "").strip() or "（无摘要）"
    # Keep dialog readable
    if len(body) > 1200:
        body = body[:1200] + "\n..."
    dialog.setInformativeText(body)
    dialog.setStandardButtons(
        QtWidgets.QMessageBox.StandardButton.Yes
        | QtWidgets.QMessageBox.StandardButton.No
    )
    dialog.setDefaultButton(QtWidgets.QMessageBox.StandardButton.No)
    yes_btn = dialog.button(QtWidgets.QMessageBox.StandardButton.Yes)
    no_btn = dialog.button(QtWidgets.QMessageBox.StandardButton.No)
    if yes_btn is not None:
        yes_btn.setText("确认执行")
    if no_btn is not None:
        no_btn.setText("取消")
    dialog.setWindowFlag(QtCore.Qt.WindowType.WindowStaysOnTopHint, True)

    result = dialog.exec()
    if result == QtWidgets.QMessageBox.StandardButton.Yes:
        return "confirm"
    return "cancel"
