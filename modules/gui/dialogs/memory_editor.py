
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple



from config import MEMORY_DB_PATH, EMBEDDING_CONFIG, MEMORY_SETTINGS
import chromadb
from chromadb.utils import embedding_functions

from modules.memory_sqlite import get_memory_store, MemorySQLite
from modules.advanced_memory import GraphMemory
from modules.learning_system import get_learning_system


import json
import os
import threading
from datetime import datetime
from PySide6 import QtCore, QtGui, QtWidgets
from modules.gui.styles import get_memory_dialog_styles

try:
    from modules.memory_sqlite import get_memory_store
except ImportError:
    get_memory_store = None

try:
    from modules.character_manager import character_manager
except ImportError:
    character_manager = None
PROFILE_JSON_PATH = "./memory_db/profile.json"

def _msg(parent, title: str, text: str, icon=QtWidgets.QMessageBox.Information):
    m = QtWidgets.QMessageBox(parent)
    m.setIcon(icon)
    m.setWindowTitle(title)
    m.setText(text)
    m.exec()


class MemoryEditorDialog(QtWidgets.QDialog):
    """
    SQLite-backed memory editor + vector DB viewer.

    SQLite DB (source of truth): ./memory/memory.sqlite
      - transcript
      - memory_items
      - episodes
      - profile

    Vector DB: ./memory_db (Chroma)
      - collection: waifu_memory_advanced (chat memories)
    """

    def __init__(self, parent=None, embedded: bool = False):
        super().__init__(parent)
        self.embedded = bool(embedded)

        if self.embedded:
            self.setWindowFlags(QtCore.Qt.WindowType.Widget)
            self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Expanding)
        else:
            self.setWindowFlags(
                QtCore.Qt.WindowType.Window | QtCore.Qt.WindowType.WindowMinMaxButtonsHint | QtCore.Qt.WindowType.WindowCloseButtonHint)

        # ✅ 修复：先检查 get_memory_store 是否存在再调用
        self.store = get_memory_store() if get_memory_store else None
        if not self.store:
            print("❌ [Editor] 数据库连接失败 (self.store is None)")

        self.setWindowTitle("🧠 记忆与档案管理中心")
        self.resize(1100, 750)
        self.setStyleSheet(get_memory_dialog_styles())

        # UI 布局
        root = QtWidgets.QVBoxLayout(self)
        self.tabs = QtWidgets.QTabWidget()
        root.addWidget(self.tabs, 1)

        bottom = QtWidgets.QHBoxLayout()
        self.lbl_hint = QtWidgets.QLabel("提示: 修改将直接写入 SQLite 数据库")
        bottom.addWidget(self.lbl_hint)
        bottom.addStretch()
        if not self.embedded:
            btn_close = QtWidgets.QPushButton("关闭")
            btn_close.clicked.connect(self.close)
            bottom.addWidget(btn_close)
        root.addLayout(bottom)

        # ✅ 修复：用 try-except 包裹每个 Tab 的构建，防止一个报错炸掉整个窗口
        try:
            self._build_notes_tab()
        except Exception as e:
            print(f"❌ Notes Tab Error: {e}")

        try:
            self._build_profile_tab()  # 这是你要改的重点
        except Exception as e:
            print(f"❌ Profile Tab Error: {e}")

        try:
            self._build_episodes_tab()
        except Exception as e:
            print(f"❌ Episodes Tab Error: {e}")

        try:
            self._build_transcript_tab()
        except Exception as e:
            print(f"❌ Transcript Tab Error: {e}")

        try:
            self._build_vector_tab()
        except Exception as e:
            print(f"❌ Vector Tab Error: {e}")

        # Graph 和 Learning 是可选模块
        if GraphMemory:
            try:
                self.graph_memory = GraphMemory()
                self._build_graph_tab()
            except Exception as e:
                print(f"❌ Graph Tab Error: {e}")

        if get_learning_system:
            try:
                self.learning_system = get_learning_system()
                self._build_learning_tab()
            except Exception as e:
                print(f"❌ Learning Tab Error: {e}")

        # 初始加载
        self.reload_all()

    def _open_memory_dir(self):
        try:
            mem_dir = os.path.abspath("./memory")
            os.makedirs(mem_dir, exist_ok=True)
            url = QtCore.QUrl.fromLocalFile(mem_dir)
            QtGui.QDesktopServices.openUrl(url)
        except Exception as e:
            _msg(self, "失败", f"无法打开目录: {e}", QtWidgets.QMessageBox.Warning)


    def reload_all(self):
        """安全地重载所有数据，防止因部分 Tab 初始化失败导致整个窗口崩溃"""
        if not self.store:
            return

        # 1. Notes Tab
        if hasattr(self, 'notes_list'):
            try:
                self._reload_notes()
            except Exception as e:
                print(f"⚠️ Notes reload fail: {e}")

        # 2. Episodes Tab
        if hasattr(self, 'ep_list'):
            try:
                self._reload_episodes()
            except Exception as e:
                print(f"⚠️ Episodes reload fail: {e}")

        # 3. Profile Tab
        if hasattr(self, 'list_items'):
            try:
                self._reload_profile_db()
            except Exception as e:
                print(f"⚠️ Profile reload fail: {e}")

        # 4. Transcript Tab
        if hasattr(self, 'tr_list'):
            try:
                self._reload_transcript()
            except Exception as e:
                print(f"⚠️ Transcript reload fail: {e}")

        # 5. Vector Tab
        if hasattr(self, 'vec_list'):
            try:
                self._vector_refresh_info()
            except Exception as e:
                print(f"⚠️ Vector reload fail: {e}")

        # 6. Graph Tab (最容易崩的地方)
        # 只有当 graph_nodes_list 存在（说明 UI 建好了）且 graph_memory 存在时才刷新
        if hasattr(self, 'graph_nodes_list') and hasattr(self, 'graph_memory'):
            try:
                self._reload_graph()
            except Exception as e:
                print(f"⚠️ Graph reload fail: {e}")

        # 7. Learning Tab
        if hasattr(self, 'learning_stats') and hasattr(self, 'learning_system'):
            try:
                self._reload_learning()
            except Exception as e:
                print(f"⚠️ Learning reload fail: {e}")

    # =========================
    # Tab: Notes (memory_items)
    # =========================
    def _build_notes_tab(self):
        w = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(w)

        # Left: list + filters
        left = QtWidgets.QVBoxLayout()

        flt = QtWidgets.QHBoxLayout()
        self.notes_search = QtWidgets.QLineEdit()
        self.notes_search.setPlaceholderText("搜索（FTS）…")
        self.notes_search.returnPressed.connect(self._reload_notes)
        flt.addWidget(self.notes_search, 2)

        self.notes_type = QtWidgets.QComboBox()
        self.notes_type.addItems(["(all)", "rule", "preference", "fact", "assistant_said", "other"])
        self.notes_type.currentIndexChanged.connect(self._reload_notes)
        flt.addWidget(self.notes_type, 1)

        self.notes_status = QtWidgets.QComboBox()
        self.notes_status.addItems(["active", "disabled", "archived"])
        self.notes_status.currentIndexChanged.connect(self._reload_notes)
        flt.addWidget(self.notes_status, 1)

        left.addLayout(flt)

        self.notes_list = QtWidgets.QListWidget()
        self.notes_list.currentRowChanged.connect(self._on_note_select)
        left.addWidget(self.notes_list, 1)

        nav = QtWidgets.QHBoxLayout()
        self.btn_notes_prev = QtWidgets.QPushButton("上一页")
        self.btn_notes_prev.clicked.connect(lambda: self._notes_page(-1))
        nav.addWidget(self.btn_notes_prev)
        self.lbl_notes_page = QtWidgets.QLabel("1")
        self.lbl_notes_page.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        nav.addWidget(self.lbl_notes_page)
        self.btn_notes_next = QtWidgets.QPushButton("下一页")
        self.btn_notes_next.clicked.connect(lambda: self._notes_page(+1))
        nav.addWidget(self.btn_notes_next)
        left.addLayout(nav)

        btns = QtWidgets.QHBoxLayout()
        self.btn_note_new = QtWidgets.QPushButton("➕ 新建")
        self.btn_note_new.clicked.connect(self._note_new)
        btns.addWidget(self.btn_note_new)

        # 归档/禁用 (软删除)
        self.btn_note_archive = QtWidgets.QPushButton("📁 归档")
        self.btn_note_archive.setToolTip("设为 archived 状态，不再被检索")
        self.btn_note_archive.clicked.connect(self._note_archive)
        btns.addWidget(self.btn_note_archive)

        # 🟢 新增：彻底删除按钮
        self.btn_note_del = QtWidgets.QPushButton("🗑️ 删除")
        self.btn_note_del.setStyleSheet(
            "QPushButton{background-color: #EF4444; color: white; font-weight: bold; border-radius: 4px;}")
        self.btn_note_del.setToolTip("从数据库中彻底物理删除，不可恢复")
        self.btn_note_del.clicked.connect(self._note_hard_delete)
        btns.addWidget(self.btn_note_del)

        left.addLayout(btns)

        layout.addLayout(left, 1)

        # Right: editor
        right = QtWidgets.QVBoxLayout()
        form = QtWidgets.QFormLayout()

        self.ed_id = QtWidgets.QLineEdit()
        self.ed_id.setReadOnly(True)
        form.addRow("ID", self.ed_id)

        self.ed_type = QtWidgets.QComboBox()
        self.ed_type.addItems(["rule", "preference", "fact", "assistant_said", "other"])
        form.addRow("类型", self.ed_type)

        self.ed_status = QtWidgets.QComboBox()
        self.ed_status.addItems(["active", "disabled", "archived"])
        form.addRow("状态", self.ed_status)

        self.ed_pin = QtWidgets.QCheckBox("置顶（pin）")
        form.addRow("", self.ed_pin)

        self.ed_conf = QtWidgets.QDoubleSpinBox()
        self.ed_conf.setRange(0.0, 1.0)
        self.ed_conf.setSingleStep(0.05)
        self.ed_conf.setValue(1.0)
        form.addRow("置信度", self.ed_conf)

        self.ed_tags = QtWidgets.QLineEdit()
        self.ed_tags.setPlaceholderText("tag1,tag2")
        form.addRow("标签", self.ed_tags)

        right.addLayout(form)

        self.ed_text = QtWidgets.QPlainTextEdit()
        self.ed_text.setPlaceholderText("记忆内容（会注入 prompt；也可同步到向量库）")
        right.addWidget(self.ed_text, 1)

        row = QtWidgets.QHBoxLayout()
        row.addStretch(1)
        self.btn_note_save = QtWidgets.QPushButton("💾 保存修改 / 新增")  # 改个字
        self.btn_note_save.setStyleSheet( "background-color: #3B82F6; color: white; font-weight: bold; padding: 6px 15px; border-radius: 4px;")  # 加点样式
        self.btn_note_save.clicked.connect(self._note_save)
        row.addWidget(self.btn_note_save)
        right.addLayout(row)

        layout.addLayout(right, 2)

        self.tabs.addTab(w, "Notes（SQLite）")

        self._notes_page_index = 0
        self._notes_page_size = 200
        self._notes_cache: List[Dict[str, Any]] = []

    def _notes_page(self, delta: int):
        self._notes_page_index = max(0, self._notes_page_index + delta)
        self._reload_notes()

    def _reload_notes(self):
        q = (self.notes_search.text() or "").strip()
        tp = self.notes_type.currentText()
        tp = "" if tp == "(all)" else tp
        st = self.notes_status.currentText()
        off = self._notes_page_index * self._notes_page_size

        # 1. 获取数据 (稍微多取一点，防止过滤后本页为空)
        raw_items = self.store.list_items(status=st, type_=tp, query=q, limit=self._notes_page_size * 2, offset=off)

        # 2. 🟢 核心修改：手动过滤掉档案类型
        filtered_items = []
        for it in raw_items:
            # 如果是档案类型，直接跳过，不显示在 Notes 列表里
            if it.get("type") in ["agent_profile", "user_profile"]:
                continue
            filtered_items.append(it)

        # 截取回原本的分页大小 (可选，为了UI一致性)
        self._notes_cache = filtered_items[:self._notes_page_size]

        self.notes_list.blockSignals(True)
        self.notes_list.clear()

        for it in self._notes_cache:
            pin = "📌" if int(it.get("pin", 0)) else ""
            text = (it.get("text") or "").replace("\n", " ")
            title = text[:40]
            # 显示类型，方便你确认都是非 profile 的
            self.notes_list.addItem(f"{pin}[{it.get('type')}/{it.get('status')}] {title}")

        self.notes_list.blockSignals(False)

        self.lbl_notes_page.setText(str(self._notes_page_index + 1))
        if self._notes_cache:
            self.notes_list.setCurrentRow(0)
        else:
            self._clear_note_editor()

    def _clear_note_editor(self):
        self.ed_id.setText("")
        self.ed_type.setCurrentText("other")
        self.ed_status.setCurrentText("active")
        self.ed_pin.setChecked(False)
        self.ed_conf.setValue(1.0)
        self.ed_tags.setText("")
        self.ed_text.setPlainText("")

    def _on_note_select(self, row: int):
        if row < 0 or row >= len(self._notes_cache):
            self._clear_note_editor()
            return
        it = self._notes_cache[row]
        self.ed_id.setText(str(it.get("id", "")))
        self.ed_type.setCurrentText(str(it.get("type", "other")))
        self.ed_status.setCurrentText(str(it.get("status", "active")))
        self.ed_pin.setChecked(bool(int(it.get("pin", 0))))
        self.ed_conf.setValue(float(it.get("confidence", 1.0)))
        self.ed_tags.setText(",".join(it.get("tags") or []))
        self.ed_text.setPlainText(str(it.get("text") or ""))

    def _note_new(self):
        new_id = self.store.upsert_item({
            "id": f"n_{QtCore.QDateTime.currentMSecsSinceEpoch()}",
            "type": "assistant_said",
            "status": "active",
            "pin": 0,
            "confidence": 1.0,
            "tags": [],
            "text": "（写点什么，让她记住）",
            "source": "manual",
        })
        self._notes_page_index = 0
        self._reload_notes()
        # select the created item if visible
        for i, it in enumerate(self._notes_cache):
            if it.get("id") == new_id:
                self.notes_list.setCurrentRow(i)
                break
        self.ed_text.setFocus()

    def _note_archive(self):
        item_id = (self.ed_id.text() or "").strip()
        if not item_id:
            return
        self.store.set_item_status(item_id, "archived")
        self._reload_notes()

    def _note_set_status(self, status: str):
        item_id = (self.ed_id.text() or "").strip()
        if not item_id:
            return
        self.store.set_item_status(item_id, status)
        self._reload_notes()

    def _note_save(self):
        item_id = (self.ed_id.text() or "").strip()
        if not item_id:
            _msg(self, "提示", "请先选择一条 Notes。", QtWidgets.QMessageBox.Warning)
            return
        try:
            self.store.upsert_item({
                "id": item_id,
                "type": self.ed_type.currentText().strip(),
                "status": self.ed_status.currentText().strip(),
                "pin": 1 if self.ed_pin.isChecked() else 0,
                "confidence": float(self.ed_conf.value()),
                "tags": [t.strip() for t in (self.ed_tags.text() or "").split(",") if t.strip()],
                "text": (self.ed_text.toPlainText() or "").strip(),
                "source": "manual",
            })
            _msg(self, "成功", "已保存到 SQLite。")
            self._reload_notes()
        except Exception as e:
            _msg(self, "失败", f"保存失败: {e}", QtWidgets.QMessageBox.Critical)

    def _note_hard_delete(self):
        """彻底删除当前选中的 Note"""
        # 1. 获取当前 ID
        item_id = self.ed_id.text().strip()
        if not item_id:
            QtWidgets.QMessageBox.warning(self, "提示", "请先选择一条记忆。")
            return

        # 2. 弹出确认框 (防止误删)
        preview = self.ed_text.toPlainText()[:20].replace("\n", " ")
        reply = QtWidgets.QMessageBox.question(
            self,
            "危险操作",
            f"确定要【彻底删除】这条记忆吗？\n\nID: {item_id}\n内容: {preview}...\n\n此操作不可恢复！",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
            QtWidgets.QMessageBox.StandardButton.No
        )

        if reply != QtWidgets.QMessageBox.StandardButton.Yes:
            return

        # 3. 执行数据库删除
        try:
            # 直接调用 store 的连接执行 SQL，确保万无一失
            # memory_sqlite.py 对应的表名通常是 memory_items
            with self.store._connect() as conn:
                conn.execute("DELETE FROM memory_items WHERE id = ?", (item_id,))
                conn.commit()

            # 4. 刷新界面
            QtWidgets.QMessageBox.information(self, "成功", "记忆已彻底删除。")
            self._clear_note_editor()  # 清空右侧编辑框
            self._reload_notes()  # 刷新左侧列表

        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "失败", f"删除失败: {e}")

    # =========================
    # Tab: Episodes
    # =========================
    def _build_episodes_tab(self):
        w = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(w)

        left = QtWidgets.QVBoxLayout()

        # --- 🟢 新增：角色筛选下拉框 ---
        role_layout = QtWidgets.QHBoxLayout()
        role_layout.addWidget(QtWidgets.QLabel("当前角色:"))
        self.ep_char_combo = QtWidgets.QComboBox()
        self.ep_char_combo.addItem(" (全部) ", "")  # 默认显示全部

        # 填充角色列表
        if character_manager:
            for cid, data in character_manager.get_all_characters().items():
                self.ep_char_combo.addItem(f"🤖 {data.get('name', cid)}", cid)

        self.ep_char_combo.currentIndexChanged.connect(self._reload_episodes)
        role_layout.addWidget(self.ep_char_combo, 1)
        left.addLayout(role_layout)
        # -----------------------------

        flt = QtWidgets.QHBoxLayout()
        self.ep_search = QtWidgets.QLineEdit()
        self.ep_search.setPlaceholderText("搜索 Episodes（FTS）…")
        self.ep_search.returnPressed.connect(self._reload_episodes)
        flt.addWidget(self.ep_search, 2)

        self.ep_status_filter = QtWidgets.QComboBox()
        self.ep_status_filter.addItems(["active", "disabled", "archived"])
        self.ep_status_filter.currentIndexChanged.connect(self._reload_episodes)
        flt.addWidget(self.ep_status_filter, 1)
        left.addLayout(flt)

        self.ep_list = QtWidgets.QListWidget()
        self.ep_list.currentRowChanged.connect(self._on_ep_select)
        left.addWidget(self.ep_list, 1)

        # 分页按钮
        nav = QtWidgets.QHBoxLayout()
        self.btn_ep_prev = QtWidgets.QPushButton("上一页")
        self.btn_ep_prev.clicked.connect(lambda: self._ep_page(-1))
        nav.addWidget(self.btn_ep_prev)
        self.lbl_ep_page = QtWidgets.QLabel("1")
        self.lbl_ep_page.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        nav.addWidget(self.lbl_ep_page)
        self.btn_ep_next = QtWidgets.QPushButton("下一页")
        self.btn_ep_next.clicked.connect(lambda: self._ep_page(+1))
        nav.addWidget(self.btn_ep_next)
        left.addLayout(nav)

        # 操作按钮
        btns = QtWidgets.QHBoxLayout()
        self.btn_ep_new = QtWidgets.QPushButton("新建")
        self.btn_ep_new.clicked.connect(self._ep_new)
        btns.addWidget(self.btn_ep_new)
        self.btn_ep_disable = QtWidgets.QPushButton("禁用")
        self.btn_ep_disable.clicked.connect(lambda: self._ep_set_status("disabled"))
        btns.addWidget(self.btn_ep_disable)
        self.btn_ep_enable = QtWidgets.QPushButton("启用")
        self.btn_ep_enable.clicked.connect(lambda: self._ep_set_status("active"))
        btns.addWidget(self.btn_ep_enable)
        self.btn_ep_archive = QtWidgets.QPushButton("归档")
        self.btn_ep_archive.clicked.connect(lambda: self._ep_set_status("archived"))
        btns.addWidget(self.btn_ep_archive)
        self.btn_ep_reload = QtWidgets.QPushButton("重载")
        self.btn_ep_reload.clicked.connect(self._reload_episodes)
        btns.addWidget(self.btn_ep_reload)
        left.addLayout(btns)

        layout.addLayout(left, 1)

        # 右侧编辑器
        right = QtWidgets.QVBoxLayout()
        form = QtWidgets.QFormLayout()

        self.ep_id = QtWidgets.QLineEdit()
        self.ep_id.setReadOnly(True)
        form.addRow("ID", self.ep_id)

        self.ep_status = QtWidgets.QComboBox()
        self.ep_status.addItems(["active", "disabled", "archived"])
        form.addRow("状态", self.ep_status)

        self.ep_title = QtWidgets.QLineEdit()
        form.addRow("标题", self.ep_title)

        right.addLayout(form)

        self.ep_summary = QtWidgets.QPlainTextEdit()
        self.ep_summary.setPlaceholderText("总结内容（会注入 prompt：最近3条 active）")
        right.addWidget(self.ep_summary, 1)

        self.ep_tags = QtWidgets.QLineEdit()
        self.ep_tags.setPlaceholderText("tag1,tag2")
        right.addWidget(self.ep_tags)

        self.ep_said = QtWidgets.QPlainTextEdit()
        self.ep_said.setPlaceholderText("assistant_said（每行一条承诺/计划）")
        right.addWidget(self.ep_said, 1)

        row = QtWidgets.QHBoxLayout()
        row.addStretch(1)
        self.btn_ep_save = QtWidgets.QPushButton("保存")
        self.btn_ep_save.clicked.connect(self._ep_save)
        row.addWidget(self.btn_ep_save)
        right.addLayout(row)

        layout.addLayout(right, 2)

        self.tabs.addTab(w, "Episodes（SQLite）")

        self._ep_page_index = 0
        self._ep_page_size = 50
        self._ep_cache: List[Dict[str, Any]] = []

    def _reload_episodes(self):
        q = (self.ep_search.text() or "").strip()
        st = self.ep_status_filter.currentText()
        off = self._ep_page_index * self._ep_page_size

        # 1. 获取较多数据以便手动过滤
        all_eps = self.store.list_episodes(status=st, query=q, limit=200, offset=off)

        # 2. 🟢 按角色 ID 过滤
        current_char_id = self.ep_char_combo.currentData()

        filtered_eps = []
        for ep in all_eps:
            tags = ep.get("tags") or []
            # 兼容处理 tags 可能是字符串的情况
            if isinstance(tags, str):
                try:
                    tags = json.loads(tags)
                except:
                    tags = []

            if not current_char_id:
                # 选了“(全部)”：显示所有
                filtered_eps.append(ep)
            else:
                target_tag = f"role:{current_char_id}"
                # 选了特定角色：只显示带有该角色标签的 OR 完全没有角色标签的(通用)
                if target_tag in tags or not any(t.startswith("role:") for t in tags):
                    filtered_eps.append(ep)

        self._ep_cache = filtered_eps[:self._ep_page_size]

        self.ep_list.blockSignals(True)
        self.ep_list.clear()
        for ep in self._ep_cache:
            title = (ep.get("title") or "对话总结").replace("\n", " ")
            # 显示日期和标题
            ts = (ep.get("created_at") or ep.get("updated_at") or "")[:10]
            self.ep_list.addItem(f"[{ts}] {title[:40]}")
        self.ep_list.blockSignals(False)

        self.lbl_ep_page.setText(str(self._ep_page_index + 1))
        if self._ep_cache:
            self.ep_list.setCurrentRow(0)
        else:
            self._clear_ep_editor()

    def _ep_page(self, delta: int):
        self._ep_page_index = max(0, self._ep_page_index + delta)
        self._reload_episodes()



    def _clear_ep_editor(self):
        self.ep_id.setText("")
        self.ep_status.setCurrentText("active")
        self.ep_title.setText("")
        self.ep_summary.setPlainText("")
        self.ep_tags.setText("")
        self.ep_said.setPlainText("")

    def _on_ep_select(self, row: int):
        if row < 0 or row >= len(self._ep_cache):
            self._clear_ep_editor()
            return
        ep = self._ep_cache[row]
        self.ep_id.setText(str(ep.get("id", "")))
        self.ep_status.setCurrentText(str(ep.get("status", "active")))
        self.ep_title.setText(str(ep.get("title", "")))
        self.ep_summary.setPlainText(str(ep.get("summary", "")))
        self.ep_tags.setText(",".join(ep.get("tags") or []))
        said = ep.get("assistant_said") if isinstance(ep.get("assistant_said"), list) else []
        lines=[]
        for s in said:
            if isinstance(s, dict):
                t=(s.get("text") or "").strip()
                if t:
                    lines.append(t)
            elif isinstance(s, str) and s.strip():
                lines.append(s.strip())
        self.ep_said.setPlainText("\n".join(lines))

    def _ep_new(self):
        new_id = self.store.upsert_episode({
            "id": f"e_{QtCore.QDateTime.currentMSecsSinceEpoch()}",
            "status": "active",
            "title": "（新的对话总结）",
            "summary": "",
            "tags": [],
            "assistant_said": [],
        })
        self._ep_page_index = 0
        self._reload_episodes()
        for i, ep in enumerate(self._ep_cache):
            if ep.get("id") == new_id:
                self.ep_list.setCurrentRow(i)
                break
        self.ep_title.setFocus()

    def _ep_set_status(self, status: str):
        ep_id = (self.ep_id.text() or "").strip()
        if not ep_id:
            return
        # update by re-upsert
        ep = self.store.get_episode(ep_id) or {"id": ep_id}
        ep["status"] = status
        self.store.upsert_episode(ep)
        self._reload_episodes()

    def _ep_save(self):
        ep_id = (self.ep_id.text() or "").strip()
        if not ep_id:
            QtWidgets.QMessageBox.warning(self, "提示", "请先选择一条 Episode。")
            return

        lines = [l.strip() for l in (self.ep_said.toPlainText() or "").splitlines() if l.strip()]
        said = [{"type": "commitment", "text": l} for l in lines]

        # 处理 tags
        raw_tags = (self.ep_tags.text() or "").split(",")
        tags = [t.strip() for t in raw_tags if t.strip()]

        # 🟢 自动追加当前选中角色的 tag
        current_char_id = self.ep_char_combo.currentData()
        if current_char_id:
            role_tag = f"role:{current_char_id}"
            if role_tag not in tags:
                tags.append(role_tag)

        try:
            self.store.upsert_episode({
                "id": ep_id,
                "status": self.ep_status.currentText().strip(),
                "title": (self.ep_title.text() or "").strip(),
                "summary": (self.ep_summary.toPlainText() or "").strip(),
                "tags": tags,
                "assistant_said": said,
            })
            QtWidgets.QMessageBox.information(self, "成功", "已保存到 SQLite。")
            self._reload_episodes()
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "失败", f"保存失败: {e}")

    # =========================
    # Tab: Profile
    # =========================


    def _refresh_char_list(self):
        self.combo_char.clear()
        self.combo_char.addItem("👤 User (Master)", "user")

        if character_manager:
            chars = character_manager.get_all_characters()
            for cid, data in chars.items():
                name = data.get("name", cid)
                self.combo_char.addItem(f"🤖 {name}", cid)
        else:
            self.combo_char.addItem("🤖 Default Agent", "default_char")

    def _init_cat_tree(self):
        self.tree_cats.clear()

        # 基础
        root = QtWidgets.QTreeWidgetItem(["基础"])
        for x in ["name", "status", "note", "traits"]:
            root.addChild(QtWidgets.QTreeWidgetItem([x]))
        self.tree_cats.addTopLevelItem(root)

        # 喜好
        self.node_likes = QtWidgets.QTreeWidgetItem(["likes"])
        for x in ["music", "games", "food", "general"]:
            self.node_likes.addChild(QtWidgets.QTreeWidgetItem([x]))
        self.tree_cats.addTopLevelItem(self.node_likes)

        # 雷点
        self.tree_cats.addTopLevelItem(QtWidgets.QTreeWidgetItem(["dislikes"]))
        self.tree_cats.expandAll()

    def _reload_profile_db(self):
        if not self.store: return

        target_role = self.combo_char.currentData()  # 'user' or char_id

        # 查所有 active 的 profile
        # 为了简单，我们全查出来在内存里滤
        all_items = self.store.list_items(status="active", limit=2000)
        self._db_cache = []

        for it in all_items:
            typ = it.get("type")
            tags = it.get("tags") or []

            # 过滤逻辑
            if target_role == "user":
                if typ == "user_profile" or "role:user" in tags:
                    self._db_cache.append(it)
            else:
                # 必须匹配 role:xxx
                if f"role:{target_role}" in tags:
                    self._db_cache.append(it)
                # 兼容旧数据: agent_profile 且没 role 标签 -> 视为 default_char
                elif typ == "agent_profile" and not any(t.startswith("role:") for t in tags):
                    if target_role == "default_char":  # 仅归给 default
                        self._db_cache.append(it)

        self._on_cat_select()  # 刷新列表

    def _on_cat_select(self):
        item = self.tree_cats.currentItem()
        if not item: return
        cat = item.text(0)
        parent = item.parent().text(0) if item.parent() else ""

        self.lbl_path.setText(f"{parent} > {cat}" if parent else cat)
        self.list_items.clear()

        for it in self._db_cache:
            tags = it.get("tags") or []
            text = it.get("text", "")

            # 匹配分类
            match = False
            if cat in tags: match = True  # 简单匹配 tag

            if parent == "likes" and "likes" in tags and cat in tags: match = True

            if match:
                w = QtWidgets.QListWidgetItem(text)
                w.setData(QtCore.Qt.UserRole, it["id"])
                self.list_items.addItem(w)

    def _add_db_item(self):
        txt = self.inp_text.text().strip()
        item = self.tree_cats.currentItem()
        if not txt or not item: return

        cat = item.text(0)
        parent = item.parent().text(0) if item.parent() else ""
        role_id = self.combo_char.currentData()

        # 构造
        tags = [f"role:{role_id}"]
        if parent == "likes":
            tags.extend(["likes", cat])
        else:
            tags.append(cat)

        typ = "user_profile" if role_id == "user" else "agent_profile"
        nid = f"p_{int(datetime.now().timestamp() * 1000)}"

        self.store.upsert_item({
            "id": nid, "type": typ, "text": txt, "tags": tags,
            "status": "active", "updated_at": datetime.now().isoformat()
        })
        self.inp_text.clear()
        self._reload_profile_db()

    def _del_db_item(self):
        it = self.list_items.currentItem()
        if not it: return
        self.store.set_item_status(it.data(QtCore.Qt.UserRole), "archived")
        self._reload_profile_db()

    def _edit_db_item(self, it):
        uid = it.data(QtCore.Qt.UserRole)
        txt, ok = QtWidgets.QInputDialog.getText(self, "修改", "内容:", text=it.text())
        if ok and txt:
            # 需先读原数据以保留 tags
            orig = next((x for x in self._db_cache if x["id"] == uid), None)
            if orig:
                orig["text"] = txt
                self.store.upsert_item(orig)
                self._reload_profile_db()

    def _add_custom_cat(self):
        txt, ok = QtWidgets.QInputDialog.getText(self, "新分类", "输入分类名 (如 color):")
        if ok and txt:
            self.node_likes.addChild(QtWidgets.QTreeWidgetItem([txt]))

    # ============================================================
    # 🔥 Tab 2: Profile V3 (多角色 + 数据库直连版)
    # ============================================================
    def _build_profile_tab(self):
        """
        构建档案管理标签页 (替换了旧版基于JSON的实现)
        现在的逻辑是：直接读写 SQLite 的 memory_items 表，支持多角色切换。
        """
        w = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(w)

        # --- 左侧栏：角色与分类导航 ---
        left_layout = QtWidgets.QVBoxLayout()
        left_layout.setSpacing(5)

        # 1. 角色选择下拉框
        left_layout.addWidget(QtWidgets.QLabel("当前编辑对象:"))
        self.combo_char = QtWidgets.QComboBox()
        # 填充角色列表
        self._refresh_char_list()
        self.combo_char.currentIndexChanged.connect(self._reload_profile_db)
        left_layout.addWidget(self.combo_char)

        left_layout.addSpacing(10)

        # 2. 属性分类树 (Likes/Status/Traits)
        left_layout.addWidget(QtWidgets.QLabel("属性分类:"))
        self.tree_cats = QtWidgets.QTreeWidget()
        self.tree_cats.setHeaderHidden(True)
        self.tree_cats.itemClicked.connect(self._on_cat_select)
        # 初始化树结构
        self._init_cat_tree()
        left_layout.addWidget(self.tree_cats)

        # 3. 动态添加分类按钮
        btn_add_cat = QtWidgets.QPushButton("➕ 新增分类")
        btn_add_cat.setToolTip("在当前选中的节点下添加子分类")
        btn_add_cat.clicked.connect(self._add_custom_category)
        left_layout.addWidget(btn_add_cat)

        # 刷新按钮
        btn_refresh = QtWidgets.QPushButton("🔄 刷新数据")
        btn_refresh.clicked.connect(self._reload_profile_db)
        left_layout.addWidget(btn_refresh)

        left_widget = QtWidgets.QWidget()
        left_widget.setLayout(left_layout)
        left_widget.setFixedWidth(220)
        layout.addWidget(left_widget)

        # --- 右侧栏：条目编辑区 ---
        right_layout = QtWidgets.QVBoxLayout()

        # 顶部面包屑提示
        self.lbl_path = QtWidgets.QLabel("请选择左侧分类...")
        self.lbl_path.setStyleSheet("font-size: 14px; font-weight: bold; color: #333; padding: 5px;")
        right_layout.addWidget(self.lbl_path)

        # 条目列表
        self.list_items = QtWidgets.QListWidget()
        self.list_items.setAlternatingRowColors(True)
        self.list_items.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list_items.itemDoubleClicked.connect(self._edit_db_item)  # 双击编辑
        right_layout.addWidget(self.list_items)

        # 底部操作区
        btns = QtWidgets.QHBoxLayout()

        self.input_new_item = QtWidgets.QLineEdit()
        self.input_new_item.setPlaceholderText("在此输入新条目内容，按回车快速添加...")
        self.input_new_item.returnPressed.connect(self._add_db_item)
        btns.addWidget(self.input_new_item, 3)

        btn_add = QtWidgets.QPushButton("添加")
        btn_add.setStyleSheet("background-color: #10B981; color: white; font-weight: bold;")
        btn_add.clicked.connect(self._add_db_item)
        btns.addWidget(btn_add, 1)

        btn_del = QtWidgets.QPushButton("删除选中")
        btn_del.setStyleSheet("background-color: #EF4444; color: white;")
        btn_del.clicked.connect(self._del_db_item)
        btns.addWidget(btn_del, 1)

        right_layout.addLayout(btns)

        # 底部说明
        hint = QtWidgets.QLabel("💡 提示: 数据直接存入 SQLite 数据库。双击条目可修改。")
        hint.setStyleSheet("color: #888; font-size: 11px;")
        right_layout.addWidget(hint)

        layout.addLayout(right_layout)
        self.tabs.addTab(w, "👤 角色档案 (DB)")

        # 本地缓存，用于快速筛选
        self._db_items_cache = []

    def _refresh_char_list(self):
        """加载角色列表：User + character_manager里的角色"""
        self.combo_char.blockSignals(True)
        self.combo_char.clear()

        # 1. 必有项: User
        self.combo_char.addItem("👤 User (Master)", "user")

        # 2. 读取 character_manager
        if character_manager:
            chars = character_manager.get_all_characters()
            # 获取当前激活的角色ID，方便标记
            active_id = character_manager.data.get("active_id")

            for char_id, data in chars.items():
                name = data.get("name", char_id)
                prefix = "⭐ " if char_id == active_id else "🤖 "
                self.combo_char.addItem(f"{prefix}{name}", char_id)
        else:
            # 兜底
            self.combo_char.addItem("🤖 Default Agent", "default_char")

        self.combo_char.blockSignals(False)

    def _init_cat_tree(self):
        """初始化分类树结构"""
        self.tree_cats.clear()

        # 根节点：基本信息
        root_basic = QtWidgets.QTreeWidgetItem(["基本信息"])
        # 这里定义一些常用的 tag 映射
        root_basic.addChild(QtWidgets.QTreeWidgetItem(["名字 (name)"]))
        root_basic.addChild(QtWidgets.QTreeWidgetItem(["状态 (status)"]))
        root_basic.addChild(QtWidgets.QTreeWidgetItem(["性格 (traits)"]))
        root_basic.addChild(QtWidgets.QTreeWidgetItem(["备注 (note)"]))
        self.tree_cats.addTopLevelItem(root_basic)

        # 根节点：喜好
        self.root_likes = QtWidgets.QTreeWidgetItem(["喜好 (likes)"])
        # 预设几个常用分类
        for c in ["music", "games", "food", "general", "color", "movie"]:
            self.root_likes.addChild(QtWidgets.QTreeWidgetItem([c]))
        self.tree_cats.addTopLevelItem(self.root_likes)

        # 根节点：雷点
        root_dis = QtWidgets.QTreeWidgetItem(["雷点 (dislikes)"])
        self.tree_cats.addTopLevelItem(root_dis)

        self.tree_cats.expandAll()

    def _reload_profile_db(self):
        """从数据库拉取数据并刷新界面"""
        if not self.store: return

        # 1. 获取当前选中的 role_id (如 'user', 'suzu', 'default_char')
        current_role_id = self.combo_char.currentData()
        if not current_role_id: return

        # 2. 决定查询的 type
        # 逻辑：User 用 'user_profile'，其他角色用 'agent_profile'
        target_type = "user_profile" if current_role_id == "user" else "agent_profile"

        # 3. 从数据库查询所有 active 的该类型条目
        try:
            # 假设 list_items 支持按 type 筛选
            all_items = self.store.list_items(type_=target_type, status="active", limit=2000)
        except Exception:
            # 如果不支持 type_ 参数，就全查出来再滤
            all_items = self.store.list_items(status="active", limit=2000)

        # 4. 内存筛选：只保留属于当前 role_id 的条目
        self._db_items_cache = []

        for it in all_items:
            # 确保 type 匹配
            if it.get("type") != target_type: continue

            tags = it.get("tags") or []
            # 兼容：如果 tags 是字符串(JSON)，转为 list
            if isinstance(tags, str):
                try:
                    tags = json.loads(tags)
                except:
                    tags = []

            # 将清洗后的 tags 存回去方便后续使用
            it["tags_list"] = tags

            # User 不需要检查 role:xxx 标签 (只有一个用户)
            if current_role_id == "user":
                self._db_items_cache.append(it)
            else:
                # Agent 需要检查 role 标签以区分不同角色
                role_tag = f"role:{current_role_id}"

                # 情况A: 有明确的 role:xxx 标签 -> 必须匹配
                has_role_tag = any(t.startswith("role:") for t in tags)

                if role_tag in tags:
                    self._db_items_cache.append(it)
                elif not has_role_tag:
                    # 情况B: 旧数据没有 role 标签 -> 默认显示在列表中(作为通用/默认)
                    # 或者你可以选择只显示在 default_char 下
                    if current_role_id == "default_char":
                        self._db_items_cache.append(it)

        # 5. 刷新右侧列表
        self._on_cat_select()

    def _on_cat_select(self):
        """左侧分类选中时，筛选右侧列表"""
        item = self.tree_cats.currentItem()
        if not item: return

        cat_name_raw = item.text(0)
        # 提取括号里的英文名，例如 "名字 (name)" -> "name"
        if "(" in cat_name_raw:
            cat_name = cat_name_raw.split("(")[1].split(")")[0]
        else:
            cat_name = cat_name_raw

        parent = item.parent()
        parent_name = parent.text(0) if parent else ""

        # 更新面包屑
        self.lbl_path.setText(f"{parent_name} > {cat_name_raw}" if parent else cat_name_raw)
        self.list_items.clear()

        # 筛选逻辑
        for it in self._db_items_cache:
            tags = it.get("tags_list", [])
            text = it.get("text", "")

            match = False

            # 1. 喜好特殊处理 (父节点是 Likes)
            if "喜好" in parent_name or parent_name == "likes":
                # tags 需包含 "likes" 且包含 具体分类名
                if "likes" in tags and cat_name in tags:
                    match = True

            # 2. 其他通用处理 (直接匹配 tag)
            else:
                if cat_name in tags:
                    match = True
                # 兼容旧 tag: likes 也是一种 tag
                if cat_name == "likes" and "likes" in tags:
                    match = True
                if cat_name == "dislikes" and "dislikes" in tags:
                    match = True

            if match:
                list_item = QtWidgets.QListWidgetItem(text)
                list_item.setData(QtCore.Qt.UserRole, it["id"])  # 存储 ID
                self.list_items.addItem(list_item)

    def _add_db_item(self):
        """添加新条目到数据库"""
        text = self.input_new_item.text().strip()
        if not text: return

        item = self.tree_cats.currentItem()
        if not item:
            _msg(self, "提示", "请先在左侧选择一个分类")
            return

        # 获取分类名
        cat_name_raw = item.text(0)
        if "(" in cat_name_raw:
            cat_name = cat_name_raw.split("(")[1].split(")")[0]
        else:
            cat_name = cat_name_raw

        parent = item.parent()
        parent_name = parent.text(0) if parent else ""

        # 1. 构造 Tags
        current_role_id = self.combo_char.currentData()
        tags = []

        # 角色标签 (User 可选，Agent 必带)
        if current_role_id != "user":
            tags.append(f"role:{current_role_id}")
        else:
            tags.append("role:user")  # 统一加上也好

        # 类别标签
        if "喜好" in parent_name or parent_name == "likes":
            tags.append("likes")
            tags.append(cat_name)  # e.g. music
        else:
            tags.append(cat_name)  # e.g. name, status

        # 2. 写入数据库
        type_ = "user_profile" if current_role_id == "user" else "agent_profile"

        # 生成时间戳ID
        import time
        new_id = f"p_{int(time.time() * 1000)}"

        try:
            self.store.upsert_item({
                "id": new_id,
                "type": type_,
                "text": text,
                "tags": tags,  # 这里传 list，memory_sqlite 应该会自动转 json
                "status": "active",
                "updated_at": datetime.now().isoformat()
            })

            self.input_new_item.clear()
            self._reload_profile_db()

        except Exception as e:
            _msg(self, "保存失败", str(e), QtWidgets.QMessageBox.Warning)

    def _del_db_item(self):
        """归档选中条目"""
        items = self.list_items.selectedItems()
        if not items: return

        count = 0
        for item in items:
            uid = item.data(QtCore.Qt.UserRole)
            self.store.set_item_status(uid, "archived")
            count += 1

        self._reload_profile_db()

    def _edit_db_item(self, item):
        """双击修改条目内容"""
        uid = item.data(QtCore.Qt.UserRole)
        old_text = item.text()

        new_text, ok = QtWidgets.QInputDialog.getText(self, "修改条目", "内容:", text=old_text)
        if ok and new_text and new_text != old_text:
            # 需要先找到原数据以保留 tags
            orig = next((x for x in self._db_items_cache if x["id"] == uid), None)
            if orig:
                # 构造更新数据
                update_data = {
                    "id": orig["id"],
                    "type": orig["type"],
                    "text": new_text,
                    "tags": orig["tags_list"],  # 使用 list 格式
                    "status": orig["status"],
                    "updated_at": datetime.now().isoformat()
                }
                self.store.upsert_item(update_data)
                self._reload_profile_db()

    def _add_custom_category(self):
        """在当前选中节点下添加子分类"""
        item = self.tree_cats.currentItem()
        if not item:
            _msg(self, "提示", "请先选择一个父节点（如'喜好'）")
            return

        text, ok = QtWidgets.QInputDialog.getText(self, "新分类", "输入分类英文名 (如 anime):")
        if ok and text:
            # 简单地在 UI 上添加一个节点
            # 实际上分类是由数据里的 tag 决定的，这里添加是为了方便录入
            new_item = QtWidgets.QTreeWidgetItem([text])
            item.addChild(new_item)
            item.setExpanded(True)
            self.tree_cats.setCurrentItem(new_item)

    # =========================
    # Tab: Transcript
    # =========================
    def _build_transcript_tab(self):
        w = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(w)

        top = QtWidgets.QHBoxLayout()
        self.tr_search = QtWidgets.QLineEdit()
        self.tr_search.setPlaceholderText("搜索对话（FTS）…")
        self.tr_search.returnPressed.connect(self._reload_transcript)
        top.addWidget(self.tr_search, 2)

        self.tr_role = QtWidgets.QComboBox()
        self.tr_role.addItems(["(all)", "user", "assistant", "system"])
        self.tr_role.currentIndexChanged.connect(self._reload_transcript)
        top.addWidget(self.tr_role, 1)

        layout.addLayout(top)

        self.tr_list = QtWidgets.QListWidget()
        self.tr_list.currentRowChanged.connect(self._on_tr_select)
        layout.addWidget(self.tr_list, 1)

        nav = QtWidgets.QHBoxLayout()
        self.btn_tr_prev = QtWidgets.QPushButton("上一页")
        self.btn_tr_prev.clicked.connect(lambda: self._tr_page(-1))
        nav.addWidget(self.btn_tr_prev)
        self.lbl_tr_page = QtWidgets.QLabel("1")
        self.lbl_tr_page.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        nav.addWidget(self.lbl_tr_page)
        self.btn_tr_next = QtWidgets.QPushButton("下一页")
        self.btn_tr_next.clicked.connect(lambda: self._tr_page(+1))
        nav.addWidget(self.btn_tr_next)
        self.btn_tr_delete = QtWidgets.QPushButton("删除选中")
        self.btn_tr_delete.setStyleSheet("QPushButton{background:#EF4444;color:white;border-radius:10px;padding:7px 12px;} QPushButton:hover{background:#DC2626;}")
        self.btn_tr_delete.clicked.connect(self._tr_delete)
        nav.addWidget(self.btn_tr_delete)
        self.btn_tr_clear = QtWidgets.QPushButton("清空所有")
        self.btn_tr_clear.setStyleSheet("QPushButton{background:#F59E0B;color:white;border-radius:10px;padding:7px 12px;} QPushButton:hover{background:#D97706;}")
        self.btn_tr_clear.clicked.connect(self._tr_clear_all)
        nav.addWidget(self.btn_tr_clear)
        layout.addLayout(nav)

        self.tr_view = QtWidgets.QPlainTextEdit()
        self.tr_view.setReadOnly(True)
        layout.addWidget(self.tr_view, 1)

        self.tabs.addTab(w, "Transcript（SQLite）")

        self._tr_page_index = 0
        self._tr_page_size = 200
        self._tr_cache: List[Dict[str, Any]] = []

    def _tr_page(self, delta: int):
        self._tr_page_index = max(0, self._tr_page_index + delta)
        self._reload_transcript()

    def _reload_transcript(self):
        q = (self.tr_search.text() or "").strip()
        role = self.tr_role.currentText()
        role = None if role == "(all)" else role
        off = self._tr_page_index * self._tr_page_size

        rows = self.store.list_transcript(role=role, query=q, limit=self._tr_page_size, offset=off)
        self._tr_cache = rows

        self.tr_list.blockSignals(True)
        self.tr_list.clear()
        for r in rows:
            ts = (r.get("ts_iso") or "")[:19].replace("T", " ")
            role = r.get("role")
            content = (r.get("content") or "").replace("\n", " ")
            self.tr_list.addItem(f"[{ts}] {role}: {content[:60]}")
        self.tr_list.blockSignals(False)

        self.lbl_tr_page.setText(str(self._tr_page_index + 1))
        if rows:
            self.tr_list.setCurrentRow(0)
        else:
            self.tr_view.setPlainText("")

    def _on_tr_select(self, row: int):
        if row < 0 or row >= len(self._tr_cache):
            self.tr_view.setPlainText("")
            return
        r = self._tr_cache[row]
        ts = r.get("ts_iso")
        role = r.get("role")
        meta = r.get("meta") or {}
        content = r.get("content") or ""
        tr_id = r.get("id")
        self.tr_view.setPlainText(f"ID: {tr_id}\nts: {ts}\nrole: {role}\nmeta: {meta}\n\n{content}")

    def _tr_delete(self):
        """删除选中的 transcript"""
        row = self.tr_list.currentRow()
        if row < 0 or row >= len(self._tr_cache):
            _msg(self, "提示", "请先选择一条记录。", QtWidgets.QMessageBox.Warning)
            return

        r = self._tr_cache[row]
        tr_id = r.get("id")
        role = r.get("role")
        content = (r.get("content") or "").replace("\n", " ")[:50]

        reply = QtWidgets.QMessageBox.question(
            self,
            "确认删除",
            f"确定要删除这条记录吗？\n\nID: {tr_id}\n角色: {role}\n内容: {content}...",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
            QtWidgets.QMessageBox.StandardButton.No
        )

        if reply == QtWidgets.QMessageBox.StandardButton.Yes:
            if self.store.delete_transcript(tr_id):
                _msg(self, "成功", "记录已删除。")
                self._reload_transcript()
            else:
                _msg(self, "失败", "删除失败，请查看控制台日志。", QtWidgets.QMessageBox.Critical)

    def _tr_clear_all(self):
        """清空所有 transcript"""
        reply = QtWidgets.QMessageBox.question(
            self,
            "危险操作",
            "确定要清空所有对话记录吗？此操作不可恢复！\n\n建议先备份数据库文件。",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
            QtWidgets.QMessageBox.StandardButton.No
        )

        if reply == QtWidgets.QMessageBox.StandardButton.Yes:
            # 二次确认
            reply2 = QtWidgets.QMessageBox.question(
                self,
                "再次确认",
                "真的要清空吗？此操作不可恢复！",
                QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
                QtWidgets.QMessageBox.StandardButton.No
            )

            if reply2 == QtWidgets.QMessageBox.StandardButton.Yes:
                try:
                    with self.store._connect() as conn:
                        conn.execute("DELETE FROM transcript")
                        conn.commit()
                    _msg(self, "成功", "所有对话记录已清空。")
                    self._reload_transcript()
                except Exception as e:
                    _msg(self, "失败", f"清空失败: {e}", QtWidgets.QMessageBox.Critical)

    # =========================
    # Tab: Vector DB viewer + sync
    # =========================
    def _build_vector_tab(self):
        w = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(w)

        top = QtWidgets.QHBoxLayout()
        self.vec_query = QtWidgets.QLineEdit()
        self.vec_query.setPlaceholderText("向量库搜索（query_texts）…")
        self.vec_query.returnPressed.connect(self._vector_search)
        top.addWidget(self.vec_query, 3)

        self.vec_k = QtWidgets.QSpinBox()
        self.vec_k.setRange(1, 50)
        self.vec_k.setValue(10)
        top.addWidget(self.vec_k, 1)

        btn_search = QtWidgets.QPushButton("搜索")
        btn_search.clicked.connect(self._vector_search)
        top.addWidget(btn_search)

        btn_list = QtWidgets.QPushButton("列出一些")
        btn_list.clicked.connect(self._vector_list_some)
        top.addWidget(btn_list)

        self.btn_sync = QtWidgets.QPushButton("同步手动记忆 → 向量库")
        self.btn_sync.clicked.connect(self._vector_sync_manual)
        top.addWidget(self.btn_sync)

        layout.addLayout(top)

        self.vec_list = QtWidgets.QListWidget()
        self.vec_list.currentRowChanged.connect(self._vector_select)
        layout.addWidget(self.vec_list, 1)

        self.vec_view = QtWidgets.QPlainTextEdit()
        self.vec_view.setReadOnly(True)
        layout.addWidget(self.vec_view, 1)

        self.tabs.addTab(w, "向量库（Chroma）")

        self._vec_cache: List[Dict[str, Any]] = []

        self._chroma_client = chromadb.PersistentClient(path=MEMORY_DB_PATH)
        fallback = embedding_functions.DefaultEmbeddingFunction()
        if EMBEDDING_CONFIG.get("api_url"):
            # reuse RemoteBGEFunction from advanced_memory (keeps consistent behavior)
            try:
                from modules.advanced_memory import RemoteBGEFunction
                self._embed_fn = RemoteBGEFunction(
                    api_url=EMBEDDING_CONFIG["api_url"],
                    api_key=EMBEDDING_CONFIG.get("api_key", ""),
                    model_name=EMBEDDING_CONFIG.get("model_name", ""),
                    fallback_fn=fallback,
                    timeout=int(EMBEDDING_CONFIG.get("timeout", 12)),
                    max_retries=int(EMBEDDING_CONFIG.get("max_retries", 2)),
                )
            except Exception:
                self._embed_fn = fallback
        else:
            self._embed_fn = fallback

        self._mem_collection = self._chroma_client.get_or_create_collection(
            name="waifu_memory_advanced",
            embedding_function=self._embed_fn,
        )

    def _vector_refresh_info(self):
        try:
            c = self._mem_collection.count()
            self.lbl_hint.setText(f"SQLite: {os.path.abspath('./memory/memory.sqlite')} | 向量库条目: {c}")
        except Exception:
            self.lbl_hint.setText(f"SQLite: {os.path.abspath('./memory/memory.sqlite')}")

    def _vector_search(self):
        q = (self.vec_query.text() or "").strip()
        if not q: return
        k = int(self.vec_k.value())

        try:
            # 🟢 修复：include 里删除了 "ids"
            res = self._mem_collection.query(
                query_texts=[q],
                n_results=k,
                include=["documents", "metadatas", "distances"],
            )
            self._vec_cache = []

            # query 方法返回的是嵌套列表 [[...]]
            # 既然你是单条查询，取 [0] 即可
            ids = res.get("ids", [[]])[0]
            docs = res.get("documents", [[]])[0]
            metas = res.get("metadatas", [[]])[0]
            dists = res.get("distances", [[]])[0]

            if not ids:
                _msg(self, "结果", "未找到相关记忆。")
                return

            for i in range(len(ids)):
                self._vec_cache.append({
                    "id": ids[i],
                    "doc": docs[i] if (docs and docs[i]) else "(无内容)",
                    "meta": metas[i] if (metas and metas[i]) else {},
                    "dist": dists[i] if (dists and i < len(dists)) else None,
                })
            self._vector_render_list()
        except Exception as e:
            _msg(self, "失败", f"向量库搜索失败: {e}", QtWidgets.QMessageBox.Warning)

    def _vector_list_some(self):
        try:
            # 🟢 修复：include 里删除了 "ids"
            res = self._mem_collection.get(
                include=["documents", "metadatas"],
                limit=30,
            )
            self._vec_cache = []

            # get 方法返回的是平铺的列表 (不是嵌套列表)
            ids = res.get("ids", [])
            docs = res.get("documents", [])
            metas = res.get("metadatas", [])

            # 安全地遍历
            count = len(ids)
            for i in range(count):
                self._vec_cache.append({
                    "id": ids[i],
                    # 加了 None 检查，防止 document 为 None 导致报错
                    "doc": docs[i] if (docs and docs[i]) else "(无内容)",
                    "meta": metas[i] if (metas and metas[i]) else {},
                    "dist": None,  # get 不返回距离
                })
            self._vector_render_list()
        except Exception as e:
            _msg(self, "失败", f"读取向量库失败: {e}", QtWidgets.QMessageBox.Warning)

    def _vector_render_list(self):
        self.vec_list.blockSignals(True)
        self.vec_list.clear()
        for r in self._vec_cache:
            doc = (r.get("doc") or "").replace("\n", " ")
            dist = r.get("dist")
            dist_str = f"{dist:.3f}" if isinstance(dist, (int, float)) else ""
            self.vec_list.addItem(f"{dist_str} {str(r.get('id'))[:28]} {doc[:60]}")
        self.vec_list.blockSignals(False)
        if self._vec_cache:
            self.vec_list.setCurrentRow(0)
        else:
            self.vec_view.setPlainText("")

    def _vector_select(self, row: int):
        if row < 0 or row >= len(self._vec_cache):
            self.vec_view.setPlainText("")
            return
        r = self._vec_cache[row]
        self.vec_view.setPlainText(f"id: {r.get('id')}\ndist: {r.get('dist')}\nmeta: {r.get('meta')}\n\n{r.get('doc')}")

    def _vector_sync_manual(self):
        # Run in background thread to avoid UI freeze
        self.btn_sync.setEnabled(False)
        self.btn_sync.setText("同步中…")

        def worker():
            ok, msg = self._do_sync_manual()
            QtCore.QTimer.singleShot(0, lambda: self._sync_done(ok, msg))

        threading.Thread(target=worker, daemon=True).start()

    def _do_sync_manual(self) -> Tuple[bool, str]:
        import traceback
        import math

        print("🔄 [Sync] =========================================")
        print("🔄 [Sync] 任务启动: 正在从 SQLite 读取数据...")

        try:
            if not self.store:
                return False, "数据库未连接"

            # 1. 读取数据
            notes = self.store.list_items(status="active", limit=1000, offset=0)
            eps = self.store.list_episodes(status="active", limit=200, offset=0)

            # 2. 准备列表
            all_ids = []
            all_docs = []
            all_metas = []

            for it in notes:
                all_ids.append(f"manual_note:{it['id']}")
                all_docs.append(f"[{it.get('type')}] {it.get('text')}")
                all_metas.append({
                    "source": "manual_sqlite",
                    "kind": "note",
                    "item_id": it["id"],
                    "tags": str(it.get("tags") or []),
                    "updated_at": it.get("updated_at") or "",
                })

            for ep in eps:
                all_ids.append(f"manual_episode:{ep['id']}")
                all_docs.append(f"[episode] {ep.get('title')}: {ep.get('summary')}")
                all_metas.append({
                    "source": "manual_sqlite",
                    "kind": "episode",
                    "episode_id": ep["id"],
                    "tags": str(ep.get("tags") or []),
                    "updated_at": ep.get("updated_at") or "",
                })

            total_count = len(all_ids)
            if total_count == 0:
                return True, "没有数据需要同步"

            print(f"🚀 [Sync] 准备同步 {total_count} 条数据")
            print(f"🔌 [Sync] API 模型: {EMBEDDING_CONFIG.get('model_name')}")

            # 🟢 核心修复：分批处理 (Batching)
            # SiliconFlow BGE-M3 限制 batch_size <= 64，我们设为 32 更安全
            BATCH_SIZE = 32

            for i in range(0, total_count, BATCH_SIZE):
                batch_ids = all_ids[i: i + BATCH_SIZE]
                batch_docs = all_docs[i: i + BATCH_SIZE]
                batch_metas = all_metas[i: i + BATCH_SIZE]

                print(
                    f"📦 [Sync] 正在处理批次 {i // BATCH_SIZE + 1} / {math.ceil(total_count / BATCH_SIZE)} (大小: {len(batch_ids)})...")

                # 分批上传
                self._mem_collection.upsert(
                    ids=batch_ids,
                    documents=batch_docs,
                    metadatas=batch_metas
                )
                # 稍微歇一下，防止 API 报 429 Too Many Requests
                import time
                time.sleep(0.5)

            print("✅ [Sync] 所有批次写入成功！")
            return True, f"同步完成：{total_count} 条"

        except Exception as e:
            print(f"❌ [Sync] 失败: {e}")
            traceback.print_exc()
            return False, f"同步失败：{e}"

    def _sync_done(self, ok: bool, msg: str):
        self.btn_sync.setEnabled(True)
        self.btn_sync.setText("同步手动记忆 → 向量库")
        self._vector_refresh_info()
        _msg(self, "同步结果" if ok else "同步失败", msg, QtWidgets.QMessageBox.Information if ok else QtWidgets.QMessageBox.Warning)

    # =========================
    # Tab: Graph Memory (关键词关系图)
    # =========================
    def _build_graph_tab(self):
        w = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(w)

        # Top: Stats and controls
        top = QtWidgets.QHBoxLayout()
        self.graph_stats = QtWidgets.QLabel("图统计: 节点: 0, 边: 0")
        top.addWidget(self.graph_stats)

        top.addStretch(1)

        self.graph_search = QtWidgets.QLineEdit()
        self.graph_search.setPlaceholderText("搜索关键词/边...")
        self.graph_search.textChanged.connect(self._filter_graph)
        top.addWidget(self.graph_search, 2)

        btn_reload = QtWidgets.QPushButton("重载")
        btn_reload.clicked.connect(self._reload_graph)
        top.addWidget(btn_reload)

        btn_decay = QtWidgets.QPushButton("手动衰减")
        btn_decay.setToolTip("应用权重衰减（权重 < 0.05 的边会被删除）")
        btn_decay.clicked.connect(self._graph_apply_decay)
        top.addWidget(btn_decay)

        layout.addLayout(top)

        # Split view: nodes list and edges list
        split = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)

        # Left: Nodes list
        left_widget = QtWidgets.QWidget()
        left_layout = QtWidgets.QVBoxLayout(left_widget)
        left_layout.addWidget(QtWidgets.QLabel("节点（关键词）:"))

        self.graph_nodes_list = QtWidgets.QListWidget()
        self.graph_nodes_list.currentTextChanged.connect(self._on_node_selected)
        left_layout.addWidget(self.graph_nodes_list, 1)

        # Node info
        node_info_layout = QtWidgets.QHBoxLayout()
        node_info_layout.addWidget(QtWidgets.QLabel("相邻节点数:"))
        self.lbl_neighbor_count = QtWidgets.QLabel("0")
        node_info_layout.addWidget(self.lbl_neighbor_count)
        node_info_layout.addStretch(1)
        left_layout.addLayout(node_info_layout)

        split.addWidget(left_widget)

        # Right: Edges list and editor
        right_widget = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout(right_widget)
        right_layout.addWidget(QtWidgets.QLabel("边（关系）:"))

        self.graph_edges_list = QtWidgets.QListWidget()
        self.graph_edges_list.currentRowChanged.connect(self._on_edge_selected)
        right_layout.addWidget(self.graph_edges_list, 1)

        # Edge editor
        edge_form = QtWidgets.QFormLayout()
        self.edge_source = QtWidgets.QLineEdit()
        self.edge_source.setReadOnly(True)
        edge_form.addRow("源节点", self.edge_source)

        self.edge_target = QtWidgets.QLineEdit()
        self.edge_target.setReadOnly(True)
        edge_form.addRow("目标节点", self.edge_target)

        self.edge_weight = QtWidgets.QDoubleSpinBox()
        self.edge_weight.setRange(0.0, 12.0)
        self.edge_weight.setSingleStep(0.5)
        self.edge_weight.setValue(1.0)
        edge_form.addRow("权重", self.edge_weight)

        right_layout.addLayout(edge_form)

        # Edge buttons
        edge_btns = QtWidgets.QHBoxLayout()
        self.btn_edge_save = QtWidgets.QPushButton("保存权重")
        self.btn_edge_save.clicked.connect(self._graph_save_edge)
        edge_btns.addWidget(self.btn_edge_save)

        self.btn_edge_delete = QtWidgets.QPushButton("删除边")
        self.btn_edge_delete.setStyleSheet("QPushButton{background:#EF4444;color:white;border-radius:10px;padding:7px 12px;} QPushButton:hover{background:#DC2626;}")
        self.btn_edge_delete.clicked.connect(self._graph_delete_edge)
        edge_btns.addWidget(self.btn_edge_delete)

        right_layout.addLayout(edge_btns)

        # Add new edge
        add_edge_layout = QtWidgets.QHBoxLayout()
        add_edge_layout.addWidget(QtWidgets.QLabel("添加新边:"))
        self.new_edge_node1 = QtWidgets.QLineEdit()
        self.new_edge_node1.setPlaceholderText("节点1")
        add_edge_layout.addWidget(self.new_edge_node1, 1)
        self.new_edge_node2 = QtWidgets.QLineEdit()
        self.new_edge_node2.setPlaceholderText("节点2")
        add_edge_layout.addWidget(self.new_edge_node2, 1)
        self.btn_add_edge = QtWidgets.QPushButton("添加")
        self.btn_add_edge.clicked.connect(self._graph_add_edge)
        add_edge_layout.addWidget(self.btn_add_edge)
        right_layout.addLayout(add_edge_layout)

        split.addWidget(right_widget)
        layout.addWidget(split, 1)

        # Bottom: Clear button
        bottom = QtWidgets.QHBoxLayout()
        bottom.addStretch(1)
        self.btn_graph_clear = QtWidgets.QPushButton("清空图")
        self.btn_graph_clear.setStyleSheet("QPushButton{background:#DC2626;color:white;border-radius:10px;padding:7px 12px;} QPushButton:hover{background:#B91C1C;}")
        self.btn_graph_clear.clicked.connect(self._graph_clear)
        bottom.addWidget(self.btn_graph_clear)
        layout.addLayout(bottom)

        self.tabs.addTab(w, "图记忆（Graph）")

        self._graph_edges_cache: List[Dict[str, Any]] = []

    def _reload_graph(self):
        """重新加载图数据"""
        # 重新加载 GraphMemory 以获取最新数据
        self.graph_memory.load_graph()

        G = self.graph_memory.G
        nodes = list(G.nodes())
        edges = list(G.edges(data=True))

        # 更新统计
        self.graph_stats.setText(f"图统计: 节点: {len(nodes)}, 边: {len(edges)}")

        # 显示节点
        self.graph_nodes_list.blockSignals(True)
        self.graph_nodes_list.clear()
        for node in sorted(nodes):
            degree = G.degree(node)
            self.graph_nodes_list.addItem(f"{node} (度: {degree})")
        self.graph_nodes_list.blockSignals(False)

        # 显示边（按权重降序）
        self.graph_edges_list.blockSignals(True)
        self.graph_edges_list.clear()
        self._graph_edges_cache = []
        sorted_edges = sorted(edges, key=lambda x: x[2].get('weight', 1.0), reverse=True)
        for u, v, data in sorted_edges:
            weight = data.get('weight', 1.0)
            self._graph_edges_cache.append({'source': u, 'target': v, 'weight': weight})
            self.graph_edges_list.addItem(f"{u} ↔ {v} (权重: {weight:.1f})")
        self.graph_edges_list.blockSignals(False)

        # 清空编辑器
        self._clear_edge_editor()

    def _clear_edge_editor(self):
        self.edge_source.setText("")
        self.edge_target.setText("")
        self.edge_weight.setValue(1.0)

    def _on_node_selected(self, node_text: str):
        """节点选中时过滤边列表"""
        if not node_text:
            self.graph_edges_list.blockSignals(True)
            self.graph_edges_list.clear()
            self.graph_edges_list.blockSignals(False)
            self.lbl_neighbor_count.setText("0")
            return

        # 提取节点名（去掉度数信息）
        node = node_text.split(' (度:')[0]

        # 显示该节点的相邻边
        G = self.graph_memory.G
        neighbors = list(G.neighbors(node))
        self.lbl_neighbor_count.setText(str(len(neighbors)))

        self.graph_edges_list.blockSignals(True)
        self.graph_edges_list.clear()

        # 收集所有相关边（包括该节点作为源或目标）
        related_edges = []
        for u, v, data in G.edges(data=True):
            if u == node or v == node:
                weight = data.get('weight', 1.0)
                related_edges.append({'source': u, 'target': v, 'weight': weight})

        # 按权重排序
        related_edges.sort(key=lambda x: x['weight'], reverse=True)

        self._graph_edges_cache = related_edges
        for edge in related_edges:
            self.graph_edges_list.addItem(f"{edge['source']} ↔ {edge['target']} (权重: {edge['weight']:.1f})")

        self.graph_edges_list.blockSignals(False)

    def _on_edge_selected(self, row: int):
        """边选中时显示详情"""
        if row < 0 or row >= len(self._graph_edges_cache):
            self._clear_edge_editor()
            return

        edge = self._graph_edges_cache[row]
        self.edge_source.setText(edge['source'])
        self.edge_target.setText(edge['target'])
        self.edge_weight.setValue(edge['weight'])

    def _graph_save_edge(self):
        """保存边的权重修改"""
        source = self.edge_source.text()
        target = self.edge_target.text()
        weight = self.edge_weight.value()

        if not source or not target:
            _msg(self, "提示", "请先选择一条边。", QtWidgets.QMessageBox.Warning)
            return

        try:
            if self.graph_memory.G.has_edge(source, target):
                self.graph_memory.G[source][target]['weight'] = weight
                self.graph_memory.save_graph()
                _msg(self, "成功", f"边权重已更新为 {weight:.1f}。")
                self._reload_graph()
            else:
                _msg(self, "失败", "边不存在。", QtWidgets.QMessageBox.Warning)
        except Exception as e:
            _msg(self, "失败", f"保存失败: {e}", QtWidgets.QMessageBox.Critical)

    def _graph_delete_edge(self):
        """删除选中的边"""
        source = self.edge_source.text()
        target = self.edge_target.text()

        if not source or not target:
            _msg(self, "提示", "请先选择一条边。", QtWidgets.QMessageBox.Warning)
            return

        reply = QtWidgets.QMessageBox.question(
            self,
            "确认删除",
            f"确定要删除边 {source} ↔ {target} 吗？",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
            QtWidgets.QMessageBox.StandardButton.No
        )

        if reply == QtWidgets.QMessageBox.StandardButton.Yes:
            try:
                self.graph_memory.G.remove_edge(source, target)
                self.graph_memory.save_graph()
                _msg(self, "成功", "边已删除。")
                self._reload_graph()
            except Exception as e:
                _msg(self, "失败", f"删除失败: {e}", QtWidgets.QMessageBox.Critical)

    def _graph_add_edge(self):
        """添加新的边"""
        node1 = self.new_edge_node1.text().strip()
        node2 = self.new_edge_node2.text().strip()

        if not node1 or not node2:
            _msg(self, "提示", "请输入两个节点名称。", QtWidgets.QMessageBox.Warning)
            return

        if node1 == node2:
            _msg(self, "提示", "节点不能相同。", QtWidgets.QMessageBox.Warning)
            return

        try:
            self.graph_memory.add_concept_link(node1, node2)
            _msg(self, "成功", f"边 {node1} ↔ {node2} 已添加。")
            self.new_edge_node1.clear()
            self.new_edge_node2.clear()
            self._reload_graph()
        except Exception as e:
            _msg(self, "失败", f"添加失败: {e}", QtWidgets.QMessageBox.Critical)

    def _graph_apply_decay(self):
        """手动应用衰减"""
        reply = QtWidgets.QMessageBox.question(
            self,
            "确认衰减",
            "确定要应用权重衰减吗？权重 < 0.05 的边将被删除。",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
            QtWidgets.QMessageBox.StandardButton.No
        )

        if reply == QtWidgets.QMessageBox.StandardButton.Yes:
            try:
                self.graph_memory.maybe_apply_decay()
                _msg(self, "成功", "衰减已应用。")
                self._reload_graph()
            except Exception as e:
                _msg(self, "失败", f"衰减失败: {e}", QtWidgets.QMessageBox.Critical)

    def _graph_clear(self):
        """清空整个图"""
        reply = QtWidgets.QMessageBox.question(
            self,
            "危险操作",
            "确定要清空整个图记忆吗？此操作不可恢复！",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
            QtWidgets.QMessageBox.StandardButton.No
        )

        if reply == QtWidgets.QMessageBox.StandardButton.Yes:
            # 二次确认
            reply2 = QtWidgets.QMessageBox.question(
                self,
                "再次确认",
                "真的要清空图记忆吗？所有关键词关系将被删除！",
                QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
                QtWidgets.QMessageBox.StandardButton.No
            )

            if reply2 == QtWidgets.QMessageBox.StandardButton.Yes:
                try:
                    self.graph_memory.G.clear()
                    self.graph_memory.save_graph()
                    _msg(self, "成功", "图记忆已清空。")
                    self._reload_graph()
                except Exception as e:
                    _msg(self, "失败", f"清空失败: {e}", QtWidgets.QMessageBox.Critical)

    def _filter_graph(self):
        """根据搜索框内容过滤显示"""
        search_text = self.graph_search.text().strip().lower()

        # 过滤节点
        for i in range(self.graph_nodes_list.count()):
            item = self.graph_nodes_list.item(i)
            if not search_text:
                item.setHidden(False)
            else:
                node_text = item.text().lower()
                item.setHidden(search_text not in node_text)

        # 过滤边
        for i in range(self.graph_edges_list.count()):
            item = self.graph_edges_list.item(i)
            if not search_text:
                item.setHidden(False)
            else:
                edge_text = item.text().lower()
                item.setHidden(search_text not in edge_text)

    # =========================
    # Tab: Learning System (学习系统)
    # =========================
    def _build_learning_tab(self):
        w = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(w)

        # Top: Overview
        top = QtWidgets.QHBoxLayout()
        self.learning_stats = QtWidgets.QLabel("学习系统概览")
        self.learning_stats.setStyleSheet("font-weight:bold;font-size:12px;color:#1F2937;")
        top.addWidget(self.learning_stats)

        top.addStretch(1)

        btn_reload = QtWidgets.QPushButton("刷新")
        btn_reload.clicked.connect(self._reload_learning)
        top.addWidget(btn_reload)

        btn_save = QtWidgets.QPushButton("保存修改")
        btn_save.clicked.connect(self._save_learning_changes)
        top.addWidget(btn_save)

        btn_reset = QtWidgets.QPushButton("重置默认")
        btn_reset.setStyleSheet("QPushButton{background:#F59E0B;color:white;border-radius:10px;padding:7px 12px;} QPushButton:hover{background:#D97706;}")
        btn_reset.clicked.connect(self._reset_learning)
        top.addWidget(btn_reset)

        layout.addLayout(top)

        # Split view: weights and preferences
        split = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)

        # Left: Personality Weights
        left_widget = QtWidgets.QWidget()
        left_layout = QtWidgets.QVBoxLayout(left_widget)
        left_layout.addWidget(QtWidgets.QLabel("性格权重 (0.0-1.0)"))

        # Personality weight sliders
        weights_form = QtWidgets.QFormLayout()

        self.lbl_politeness = QtWidgets.QLabel("0.5")
        self.slider_politeness = QtWidgets.QDoubleSpinBox()
        self.slider_politeness.setRange(0.0, 1.0)
        self.slider_politeness.setSingleStep(0.05)
        self.slider_politeness.setValue(0.5)
        weights_form.addRow("礼貌:", self.slider_politeness)

        self.lbl_humor = QtWidgets.QLabel("0.3")
        self.slider_humor = QtWidgets.QDoubleSpinBox()
        self.slider_humor.setRange(0.0, 1.0)
        self.slider_humor.setSingleStep(0.05)
        self.slider_humor.setValue(0.3)
        weights_form.addRow("幽默:", self.slider_humor)

        self.lbl_seriousness = QtWidgets.QLabel("0.7")
        self.slider_seriousness = QtWidgets.QDoubleSpinBox()
        self.slider_seriousness.setRange(0.0, 1.0)
        self.slider_seriousness.setSingleStep(0.05)
        self.slider_seriousness.setValue(0.7)
        weights_form.addRow("严肃:", self.slider_seriousness)

        self.lbl_emotional = QtWidgets.QLabel("0.4")
        self.slider_emotional = QtWidgets.QDoubleSpinBox()
        self.slider_emotional.setRange(0.0, 1.0)
        self.slider_emotional.setSingleStep(0.05)
        self.slider_emotional.setValue(0.4)
        weights_form.addRow("情感:", self.slider_emotional)

        self.lbl_curiosity = QtWidgets.QLabel("0.6")
        self.slider_curiosity = QtWidgets.QDoubleSpinBox()
        self.slider_curiosity.setRange(0.0, 1.0)
        self.slider_curiosity.setSingleStep(0.05)
        self.slider_curiosity.setValue(0.6)
        weights_form.addRow("好奇:", self.slider_curiosity)

        self.lbl_patience = QtWidgets.QLabel("0.7")
        self.slider_patience = QtWidgets.QDoubleSpinBox()
        self.slider_patience.setRange(0.0, 1.0)
        self.slider_patience.setSingleStep(0.05)
        self.slider_patience.setValue(0.7)
        weights_form.addRow("耐心:", self.slider_patience)

        self.lbl_energy = QtWidgets.QLabel("0.8")
        self.slider_energy = QtWidgets.QDoubleSpinBox()
        self.slider_energy.setRange(0.0, 1.0)
        self.slider_energy.setSingleStep(0.05)
        self.slider_energy.setValue(0.8)
        weights_form.addRow("活力:", self.slider_energy)

        left_layout.addLayout(weights_form)

        # Character state
        self.lbl_character_state = QtWidgets.QLabel("角色状态: 温和自然, 状态: 精神不错")
        self.lbl_character_state.setStyleSheet("padding:10px;background:#E5E7EB;border-radius:5px;color:#374151;")
        left_layout.addWidget(self.lbl_character_state)

        # Interaction count
        self.lbl_interaction_count = QtWidgets.QLabel("交互次数: 0")
        left_layout.addWidget(self.lbl_interaction_count)

        split.addWidget(left_widget)

        # Right: User Preferences & Topics
        right_widget = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout(right_widget)

        # User preferences
        right_layout.addWidget(QtWidgets.QLabel("用户偏好"))

        pref_form = QtWidgets.QFormLayout()

        self.combo_response_length = QtWidgets.QComboBox()
        self.combo_response_length.addItems(["short", "medium", "long"])
        pref_form.addRow("回复长度:", self.combo_response_length)

        self.lbl_emoji_usage = QtWidgets.QLabel("0.3")
        self.slider_emoji_usage = QtWidgets.QDoubleSpinBox()
        self.slider_emoji_usage.setRange(0.0, 1.0)
        self.slider_emoji_usage.setSingleStep(0.05)
        self.slider_emoji_usage.setValue(0.3)
        pref_form.addRow("表情使用:", self.slider_emoji_usage)

        right_layout.addLayout(pref_form)

        # Top topics
        right_layout.addWidget(QtWidgets.QLabel("热门话题 (兴趣度)"))
        self.topics_list = QtWidgets.QListWidget()
        right_layout.addWidget(self.topics_list, 1)

        # Learning progress
        right_layout.addWidget(QtWidgets.QLabel("学习进度"))
        self.learning_progress_text = QtWidgets.QPlainTextEdit()
        self.learning_progress_text.setReadOnly(True)
        self.learning_progress_text.setMaximumHeight(200)
        right_layout.addWidget(self.learning_progress_text)

        split.addWidget(right_widget)
        layout.addWidget(split, 1)

        # Bottom: Recent feedback
        bottom = QtWidgets.QHBoxLayout()
        bottom.addWidget(QtWidgets.QLabel("最近反馈 (最近20条)"))
        bottom.addStretch(1)
        layout.addLayout(bottom)

        self.feedback_list = QtWidgets.QListWidget()
        self.feedback_list.setMaximumHeight(150)
        layout.addWidget(self.feedback_list, 1)

        self.tabs.addTab(w, "学习系统 (Learning)")

    def _reload_learning(self):
        """重新加载学习系统数据"""
        try:
            progress = self.learning_system.get_learning_progress()

            # 更新性格权重
            weights = progress.get("personality_weights", {})
            self.slider_politeness.setValue(weights.get("politeness", 0.5))
            self.slider_humor.setValue(weights.get("humor", 0.3))
            self.slider_seriousness.setValue(weights.get("seriousness", 0.7))
            self.slider_emotional.setValue(weights.get("emotional", 0.4))
            self.slider_curiosity.setValue(weights.get("curiosity", 0.6))
            self.slider_patience.setValue(weights.get("patience", 0.7))
            self.slider_energy.setValue(weights.get("energy", 0.8))

            # 更新用户偏好
            prefs = progress.get("user_preferences", {})
            self.combo_response_length.setCurrentText(prefs.get("response_length", "medium"))
            self.slider_emoji_usage.setValue(prefs.get("emoji_usage", 0.3))

            # 更新话题兴趣
            topics = progress.get("top_topics", {})
            self.topics_list.clear()
            for topic, score in sorted(topics.items(), key=lambda x: x[1], reverse=True)[:10]:
                self.topics_list.addItem(f"{topic} ({score:.2f})")

            # 更新角色状态
            character_state = self.learning_system.get_character_state()
            self.lbl_character_state.setText(character_state)

            # 更新交互次数
            interaction_count = progress.get("interaction_count", 0)
            self.lbl_interaction_count.setText(f"交互次数: {interaction_count}")
            self.learning_stats.setText(f"学习系统概览 - 交互: {interaction_count}")

            # 更新学习进度
            progress_text = json.dumps(progress, indent=2, ensure_ascii=False)
            self.learning_progress_text.setPlainText(progress_text)

            # 更新最近反馈
            recent_feedback = self.learning_system.db.get_recent_feedback(limit=20)
            self.feedback_list.clear()
            for fb in recent_feedback:
                emoji = "✅" if fb.reaction == "positive" else ("❌" if fb.reaction == "negative" else "➡️")
                feedback_preview = (fb.response or "")[:50] + "..." if len(fb.response or "") > 50 else (fb.response or "")
                self.feedback_list.addItem(f"{emoji} [{fb.emotion}] {feedback_preview}")

        except Exception as e:
            _msg(self, "失败", f"加载学习数据失败: {e}", QtWidgets.QMessageBox.Critical)

    def _save_learning_changes(self):
        """保存学习系统的修改"""
        try:
            # 保存性格权重
            from modules.learning_system import PersonalityWeights, UserPreferences
            new_weights = PersonalityWeights(
                politeness=self.slider_politeness.value(),
                humor=self.slider_humor.value(),
                seriousness=self.slider_seriousness.value(),
                emotional=self.slider_emotional.value(),
                curiosity=self.slider_curiosity.value(),
                patience=self.slider_patience.value(),
                energy=self.slider_energy.value()
            )
            self.learning_system.db.save_weights(new_weights)
            self.learning_system.weights = new_weights

            # 保存用户偏好
            new_prefs = UserPreferences(
                response_length=self.combo_response_length.currentText(),
                emoji_usage=self.slider_emoji_usage.value()
            )
            self.learning_system.db.save_preferences(new_prefs)
            self.learning_system.preferences = new_prefs

            # 更新角色状态显示
            character_state = self.learning_system.get_character_state()
            self.lbl_character_state.setText(character_state)

            _msg(self, "成功", "学习数据已保存。")
            self._reload_learning()
        except Exception as e:
            _msg(self, "失败", f"保存失败: {e}", QtWidgets.QMessageBox.Critical)

    def _reset_learning(self):
        """重置学习系统为默认值"""
        reply = QtWidgets.QMessageBox.question(
            self,
            "确认重置",
            "确定要重置学习系统吗？所有学习进度将丢失！",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
            QtWidgets.QMessageBox.StandardButton.No
        )

        if reply == QtWidgets.QMessageBox.StandardButton.Yes:
            try:
                import os
                from config import MEMORY_DB_PATH
                db_path = os.path.join(MEMORY_DB_PATH, "learning.db")
                from modules.learning_system import reset_learning_system
                self.learning_system = reset_learning_system(db_path=db_path, remove_db=True)

                _msg(self, "成功", "学习系统已重置为默认值。")
                self._reload_learning()
            except Exception as e:
                _msg(self, "失败", f"重置失败: {e}", QtWidgets.QMessageBox.Critical)
