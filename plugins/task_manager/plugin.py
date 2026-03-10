"""
任务管理插件
结合生活管家的提醒功能，提供完整的任务管理系统

功能：
- 添加任务
- 查看任务列表
- 完成任务
- 设置定时提醒
- 查看今日日程
"""
import sqlite3
import os
import re
import asyncio
import random
from datetime import datetime as dt, timezone, timedelta
import dateparser  # 确保安装: pip install dateparser

from core.logger import get_logger
from plugins.plugin_utils import handle_plugin_errors

logger = get_logger()


class Plugin:
    def __init__(self):
        self.db_path = os.path.join(os.path.dirname(__file__), "../../userdata.db")
        self._running = False
        self._check_interval = 300
        self._check_task = None
        self._last_urgent_signature = ""
        self._last_urgent_present = False
        self._suggestions = [
            "记得多喝水哦 💧",
            "站起来活动一下身体吧 🏃",
            "休息一下眼睛，看看远方 👀",
            "记得按时吃饭 🍚",
            "整理一下思绪，规划接下来的任务吧 📝",
            "听听音乐放松一下吧 🎵",
        ]
        self._init_db()

    def _init_db(self):
        """初始化任务表"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS tasks (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        content TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'pending',
                        priority INTEGER DEFAULT 0,
                        due_time TEXT,
                        created_at TEXT NOT NULL,
                        completed_at TEXT,
                        tags TEXT DEFAULT '[]'
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_due_time ON tasks(due_time)")
                conn.commit()
            logger.info("任务管理数据库初始化完成")
        except Exception as e:
            logger.error(f"任务数据库初始化失败: {e}")

    @handle_plugin_errors("任务管理")
    async def run(self, args, ctx):
        args = (args or "").strip()
        parts = args.split(maxsplit=1)
        action = parts[0].strip() if parts else ""
        params = parts[1] if len(parts) > 1 else ""

        if not action:
            return "请指定操作，例如：[CMD: task | add_task 买牛奶]"

        action_map = {
            "add": "add_task", "添加": "add_task", "new": "add_task",
            "list": "list_tasks", "列表": "list_tasks", "查看": "list_tasks",
            "done": "complete_task", "完成": "complete_task",
            "delete": "delete_task", "删除": "delete_task",
            "schedule": "today_schedule", "日程": "today_schedule", "today": "today_schedule",
            "remind": "set_reminder", "提醒": "set_reminder",
            "check": "check_tasks", "检查": "check_tasks", "tasks": "check_tasks",
            "summary": "daily_summary", "摘要": "daily_summary", "daily": "daily_summary",
            "suggest": "suggest", "建议": "suggest", "建议日程": "suggest",
            "overdue": "check_overdue", "逾期": "check_overdue", "overdue_tasks": "check_overdue",
            "urgent": "check_urgent", "紧急": "check_urgent", "urgent_tasks": "check_urgent",
            "start": "start_watch", "stop": "stop_watch", "status": "watch_status",
        }

        action = action_map.get(action.lower(), action)
        params = (params or "").strip()

        if action == "add_task":
            return await self._add_task(params)
        elif action == "list_tasks":
            status_filter = params if params else "pending"
            return await self._list_tasks(status_filter)
        elif action == "complete_task":
            return await self._complete_task(params)
        elif action == "delete_task":
            return await self._delete_task(params)
        elif action in ["today_schedule", "set_reminder"]:
            return await self._list_tasks("pending")
        elif action == "check_tasks":
            return await self._check_tasks(params)
        elif action == "daily_summary":
            return await self._daily_summary()
        elif action == "suggest":
            return await self._suggest_schedule()
        elif action == "check_overdue":
            return await self._check_overdue()
        elif action == "check_urgent":
            return await self._check_urgent()
        elif action == "start_watch":
            return await self.start()
        elif action == "stop_watch":
            return await self.stop()
        elif action == "watch_status":
            return self._watch_status()
        else:
            return f"未知操作: {action}"

    async def start(self):
        if self._running:
            return "✅ 任务后台检查已在运行中"
        self._running = True
        self._check_task = asyncio.create_task(self._background_check_loop())
        logger.info("📋 任务后台检查已启动")
        return f"✅ 已启动任务后台检查\n每{self._check_interval}秒巡检一次任务状态"

    async def stop(self):
        if not self._running:
            return "任务后台检查未运行"
        self._running = False
        if self._check_task and not self._check_task.done():
            self._check_task.cancel()
            try:
                await self._check_task
            except asyncio.CancelledError:
                pass
        logger.info("📋 任务后台检查已停止")
        return "已停止任务后台检查"

    def _watch_status(self):
        status = "运行中" if self._running else "未运行"
        return f"任务后台检查状态：{status}\n检查间隔：{self._check_interval}秒"

    async def _background_check_loop(self):
        while self._running:
            try:
                await asyncio.sleep(self._check_interval)
                if not self._running:
                    break
                result = await self._check_urgent()
                is_urgent = self._is_urgent_result(result)
                signature = result.strip() if is_urgent else ""
                if is_urgent and (not self._last_urgent_present or signature != self._last_urgent_signature):
                    logger.info("🔔 检测到紧急任务")
                elif self._last_urgent_present and not is_urgent:
                    logger.info("✅ 紧急任务已清空")
                self._last_urgent_present = is_urgent
                self._last_urgent_signature = signature
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"任务后台检查失败: {e}")
                await asyncio.sleep(60)

    def _is_urgent_result(self, result: str) -> bool:
        text = str(result or "").strip()
        if not text:
            return False
        lowered = text.lower()
        if "当前没有" in text or "没有 3 小时内到期" in text:
            return False
        return "紧急任务" in text or "urgent" in lowered or "🚨" in text

    async def _add_task(self, params: str) -> str:
        """添加任务"""
        if not params:
            return "请提供任务内容，例如：[CMD: task | add_task 买牛奶]"

        content = params
        due_time = None

        if "@" in content:
            parts = content.split("@", maxsplit=1)
            content = parts[0].strip()
            time_str = parts[1].strip()
            parsed_time = dateparser.parse(time_str, settings={'PREFER_DATES_FROM': 'future'})
            if parsed_time:
                due_time = parsed_time.strftime("%Y-%m-%d %H:%M")

        if not content:
            return "任务内容不能为空"

        priority = 0
        if "[!important]" in content:
            priority = 2
            content = content.replace("[!important]", "").strip()
        elif "高优先级" in content:
            priority = 2
            content = content.replace("高优先级", "").strip()
        elif "低优先级" in content:
            priority = 0
            content = content.replace("低优先级", "").strip()
        elif "中优先级" in content:
            priority = 1
            content = content.replace("中优先级", "").strip()

        tags = re.findall(r'#(\w+)', content)
        content = re.sub(r'#\w+', '', content).strip()

        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT INTO tasks(content, status, priority, due_time, created_at, tags) VALUES (?, ?, ?, ?, ?, ?)",
                    (content, "pending", priority, due_time, dt.now(timezone.utc).isoformat(), str(tags))
                )
                conn.commit()

            result_msg = f"已添加任务：{content}"
            if due_time:
                result_msg += f"，提醒时间：{due_time}"
            if tags:
                result_msg += f"，标签：{', '.join(tags)}"

            logger.info(f"添加任务: {content}, 提醒: {due_time}")
            return result_msg
        except Exception as e:
            logger.error(f"添加任务失败: {e}")
            return f"添加任务失败: {e}"

    async def _list_tasks(self, status_filter: str = "pending") -> str:
        """列出任务"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()

                if status_filter == "completed":
                    sql = "SELECT * FROM tasks WHERE status='completed' ORDER BY completed_at DESC"
                    status_display = "已完成的任务"
                    empty_msg = "🎉 还没有已完成的任务"
                    status_emoji = "✅"
                    count_display = "完成"
                    time_desc = "完成时间"
                    time_field = "completed_at"
                    time_default = "尚未完成"
                    button_text = "标记为未完成"
                    button_action = "reopen_task"
                    button_emoji = "🔄"
                    button_title = "重新打开"
                    status_color = "#9CA3AF"
                elif status_filter == "all":
                    sql = "SELECT * FROM tasks ORDER BY priority DESC, due_time ASC, created_at DESC"
                    status_display = "所有任务"
                    empty_msg = "📋 还没有任务"
                    status_emoji = "📋"
                    count_display = "总计"
                    time_desc = "创建时间"
                    time_field = "created_at"
                    time_default = "未知"
                    button_text = "标记为完成"
                    button_action = "complete_task"
                    button_emoji = "✅"
                    button_title = "标记为完成"
                    status_color = "#FBBF24"
                else:
                    sql = "SELECT * FROM tasks WHERE status='pending' ORDER BY priority DESC, due_time ASC, created_at DESC"
                    status_display = "待办任务"
                    empty_msg = "📝 还没有待办任务"
                    status_emoji = "📝"
                    count_display = "待办"
                    time_desc = "截止时间"
                    time_field = "due_time"
                    time_default = "无"
                    button_text = "标记为完成"
                    button_action = "complete_task"
                    button_emoji = "✅"
                    button_title = "标记为完成"
                    status_color = "#F59E0B"

                cursor.execute(sql)
                rows = cursor.fetchall()

                if not rows:
                    return empty_msg

                has_due = False
                normal_tasks = []
                
                for row in rows:
                    if row[time_field]:
                        has_due = True
                        normal_tasks.append(row)
                    else:
                        normal_tasks.append(row)

                result_lines = []
                result_lines.append(f"## {status_emoji} {status_display}")

                if has_due:
                    result_lines.append(f"\n### ⏰ {time_desc}任务")
                    for i, row in enumerate(normal_tasks[:10]):
                        task_id = row["id"]
                        content = row["content"]
                        task_time = row[time_field] if row[time_field] else time_default
                        priority = row["priority"]
                        if priority == 2:
                            priority_label = " [!重要]"
                        elif priority == 1:
                            priority_label = " [中]"
                        else:
                            priority_label = " [低]"

                        if time_field == "completed_at" and task_time != "未知":
                            time_str = task_time[:16].replace("T", " ")
                        elif time_field == "due_time" and task_time != "无":
                            time_str = task_time
                        else:
                            time_str = ""

                        time_left_str = ""
                        if time_field == "due_time" and task_time and task_time != "无":
                            try:
                                due_dt = dt.fromisoformat(task_time.replace("Z", "+00:00"))
                                now = dt.now(timezone.utc)
                                diff = due_dt - now
                                if diff.total_seconds() > 0:
                                    if diff.total_seconds() < 3600:
                                        time_left_str = f"（还有{int(diff.total_seconds()/60)}分钟）"
                                        status_color = "#EF4444"
                                    elif diff.total_seconds() < 86400:
                                        time_left_str = f"（还有{int(diff.total_seconds()/3600)}小时）"
                                        status_color = "#F59E0B"
                                    else:
                                        days = int(diff.total_seconds() / 86400)
                                        time_left_str = f"（还有{days}天）"
                                        status_color = "#10B981"
                                else:
                                    time_left_str = f"（已逾期{int(abs(diff.total_seconds())/3600)}小时）"
                                    status_color = "#9CA3AF"
                            except:
                                pass

                        status_emoji_display = status_emoji
                        if time_field == "due_time" and task_time and "逾期" in time_left_str:
                            status_emoji_display = "⚠️"

                        task_entry = f"**{status_emoji_display} [{time_str}] {content}{priority_label}** {time_left_str}"
                        if status_filter != "completed":
                            task_entry += f"\n   - {button_action}: {button_text}"
                        
                        result_lines.append(task_entry)

                normal_count = len(normal_tasks)
                if normal_count > 0:
                    result_lines.append(f"\n### 📝 其他任务（共{normal_count}个）")
                    for i, row in enumerate(normal_tasks[:20]):
                        task_id = row["id"]
                        content = row["content"]
                        created = row["created_at"][:16] if row["created_at"] else ""
                        time_str = created
                        priority = row["priority"]
                        if priority == 2:
                            priority_label = " [!重要]"
                        elif priority == 1:
                            priority_label = " [中]"
                        else:
                            priority_label = " [低]"

                        task_entry = f"**{status_emoji} [{time_str}] {content}{priority_label}**"
                        task_entry += f"\n   - {button_action}: {button_text}"

                        result_lines.append(task_entry)

                total_count = len(rows)
                pending_count = sum(1 for r in rows if r["status"] == "pending")
                completed_count = sum(1 for r in rows if r["status"] == "completed")
                overdue_count = 0
                for r in rows:
                    if r["status"] == "pending" and r["due_time"]:
                        try:
                            due_dt = dt.fromisoformat(r["due_time"].replace("Z", "+00:00"))
                            now = dt.now(timezone.utc)
                            if due_dt < now:
                                overdue_count += 1
                        except:
                            pass

                result_lines.append(f"\n---")
                result_lines.append(f"📊 统计：总计 {total_count} | 待办 {pending_count} | {count_display} {completed_count} | 逾期 {overdue_count}")
                result_lines.append(f"提示：输入 [CMD: task | complete_task ID] 完成任务，输入 [CMD: task | add_task 内容 @时间] 添加新任务")

                return "\n".join(result_lines)
        except Exception as e:
            logger.error(f"列出任务失败: {e}")
            return f"列出任务失败: {e}"

    async def _complete_task(self, params: str) -> str:
        """完成任务"""
        task_id = params.strip()
        if not task_id:
            return "请提供任务ID，例如：[CMD: task | complete_task 1]"

        try:
            task_id_int = int(task_id)
        except ValueError:
            return "任务ID必须是数字"

        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT content FROM tasks WHERE id=?", (task_id_int,))
                result = cursor.fetchone()
                if not result:
                    return f"未找到任务ID: {task_id}"
                content = result[0]
                cursor.execute(
                    "UPDATE tasks SET status='completed', completed_at=? WHERE id=?",
                    (dt.now(timezone.utc).isoformat(), task_id_int)
                )
                conn.commit()
                logger.info(f"完成任务: {content}")
                return f"已完成任务：{content}"
        except Exception as e:
            logger.error(f"完成任务失败: {e}")
            return f"完成任务失败: {e}"

    async def _delete_task(self, params: str) -> str:
        """删除任务"""
        task_id = params.strip()
        if not task_id:
            return "请提供任务ID，例如：[CMD: task | delete_task 1]"

        try:
            task_id_int = int(task_id)
        except ValueError:
            return "任务ID必须是数字"

        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT content FROM tasks WHERE id=?", (task_id_int,))
                result = cursor.fetchone()
                if not result:
                    return f"未找到任务ID: {task_id}"
                content = result[0]
                cursor.execute("DELETE FROM tasks WHERE id=?", (task_id_int,))
                conn.commit()
                logger.info(f"删除任务: {content}")
                return f"已删除任务：{content}"
        except Exception as e:
            logger.error(f"删除任务失败: {e}")
            return f"删除任务失败: {e}"

    async def _check_tasks(self, params: str = "") -> str:
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                now = dt.now(timezone.utc)
                soon = now + timedelta(hours=12)

                if params.lower() in {"urgent", "紧急"}:
                    cursor.execute(
                        "SELECT * FROM tasks WHERE status='pending' AND due_time IS NOT NULL AND due_time BETWEEN ? AND ? ORDER BY priority DESC, due_time ASC",
                        (now.isoformat(), soon.isoformat()),
                    )
                else:
                    cursor.execute(
                        "SELECT * FROM tasks WHERE status='pending' AND due_time IS NOT NULL AND due_time BETWEEN ? AND ? ORDER BY priority DESC, due_time ASC",
                        (now.isoformat(), (now + timedelta(days=1)).isoformat()),
                    )
                rows = cursor.fetchall()

                if not rows:
                    suggestion = random.choice(self._suggestions)
                    return f"✅ 目前没有即将到期的任务\n\n💡 {suggestion}"

                lines = [f"## ⏰ 即将到期任务（{len(rows)}个）", ""]
                for row in rows[:6]:
                    due_time = row["due_time"][:16].replace("T", " ") if row["due_time"] else "无"
                    content = row["content"]
                    priority = row["priority"]
                    priority_label = " [!重要]" if priority == 2 else (" [中]" if priority == 1 else "")
                    lines.append(f"- [{due_time}] {content}{priority_label}")
                lines.append("\n---")
                lines.append(f"💡 {random.choice(self._suggestions)}")
                return "\n".join(lines)
        except Exception as e:
            logger.error(f"检查任务失败: {e}")
            return f"检查任务失败: {e}"

    async def _daily_summary(self) -> str:
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                now = dt.now(timezone.utc)
                start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
                end_of_day = now.replace(hour=23, minute=59, second=59, microsecond=999999)

                cursor.execute("SELECT COUNT(*) as count FROM tasks WHERE status='completed' AND completed_at BETWEEN ? AND ?",
                               (start_of_day.isoformat(), end_of_day.isoformat()))
                completed_today = cursor.fetchone()["count"]

                cursor.execute("SELECT * FROM tasks WHERE status='pending' AND due_time BETWEEN ? AND ? ORDER BY due_time ASC",
                               (start_of_day.isoformat(), end_of_day.isoformat()))
                pending_today = cursor.fetchall()

                cursor.execute("SELECT * FROM tasks WHERE status='pending' AND due_time IS NOT NULL AND due_time < ? ORDER BY due_time ASC",
                               (now.isoformat(),))
                overdue_tasks = cursor.fetchall()

                result_lines = ["## 📊 今日摘要", f"✅ 今日已完成：{completed_today} 个任务"]
                if pending_today:
                    result_lines.append(f"\n### 📝 今日待办（{len(pending_today)}个）")
                    for task in pending_today[:5]:
                        due_time = task["due_time"][:16].replace("T", " ") if task["due_time"] else "无"
                        result_lines.append(f"- [{due_time}] {task['content']}")
                if overdue_tasks:
                    result_lines.append(f"\n### ⚠️ 逾期任务（{len(overdue_tasks)}个）")
                    for task in overdue_tasks[:3]:
                        due_time = task["due_time"][:16].replace("T", " ") if task["due_time"] else "无"
                        result_lines.append(f"- [{due_time}] {task['content']}")
                return "\n".join(result_lines)
        except Exception as e:
            logger.error(f"生成每日摘要失败: {e}")
            return f"生成每日摘要失败: {e}"

    async def _suggest_schedule(self) -> str:
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM tasks WHERE status='pending' ORDER BY priority DESC, due_time ASC")
                all_pending = cursor.fetchall()
                if not all_pending:
                    return "🎉 所有任务都已完成！给自己放个假吧 😊"

                high_priority = [t for t in all_pending if t["priority"] == 2]
                medium_priority = [t for t in all_pending if t["priority"] == 1]
                now = dt.now(timezone.utc)
                end_of_today = now.replace(hour=23, minute=59, second=59, microsecond=999999)
                urgent_today = []
                for task in all_pending:
                    if task["due_time"]:
                        try:
                            due_dt = dt.fromisoformat(task["due_time"].replace("Z", "+00:00"))
                            if due_dt <= end_of_today:
                                urgent_today.append(task)
                        except Exception:
                            pass

                lines = ["## 💡 日程建议"]
                if high_priority:
                    lines.append("\n### 🔥 高优先级任务")
                    for task in high_priority[:3]:
                        lines.append(f"- {task['content']}")
                if urgent_today:
                    lines.append("\n### ⚠️ 今日截止")
                    for task in urgent_today[:3]:
                        due_time = task["due_time"][:16].replace("T", " ") if task["due_time"] else "无"
                        lines.append(f"- [{due_time}] {task['content']}")
                if medium_priority:
                    lines.append("\n### 📌 中优先级任务")
                    for task in medium_priority[:3]:
                        lines.append(f"- {task['content']}")
                lines.append("\n---")
                lines.append(f"📊 待办总数：{len(all_pending)} | 高优先级：{len(high_priority)} | 今日截止：{len(urgent_today)}")
                return "\n".join(lines)
        except Exception as e:
            logger.error(f"生成日程建议失败: {e}")
            return f"生成日程建议失败: {e}"

    async def _check_overdue(self) -> str:
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                now = dt.now(timezone.utc)
                cursor.execute("SELECT * FROM tasks WHERE status='pending' AND due_time IS NOT NULL AND due_time < ? ORDER BY due_time ASC",
                               (now.isoformat(),))
                overdue_tasks = cursor.fetchall()
                if not overdue_tasks:
                    return "✅ 没有逾期任务，继续保持！"

                result_lines = [f"## ⚠️ 发现 {len(overdue_tasks)} 个逾期任务", ""]
                for task in overdue_tasks[:8]:
                    task_id = task["id"]
                    due_time = task["due_time"][:16].replace("T", " ") if task["due_time"] else "无"
                    content = task["content"]
                    result_lines.append(f"- {task_id}. [{due_time}] {content}")
                result_lines.append("\n💡 建议：尽快处理逾期任务，或删除不再需要的任务")
                return "\n".join(result_lines)
        except Exception as e:
            logger.error(f"检查逾期任务失败: {e}")
            return f"检查逾期任务失败: {e}"

    async def _check_urgent(self) -> str:
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                now = dt.now(timezone.utc)
                soon = now + timedelta(hours=3)
                cursor.execute(
                    "SELECT * FROM tasks WHERE status='pending' AND due_time IS NOT NULL AND due_time BETWEEN ? AND ? ORDER BY priority DESC, due_time ASC",
                    (now.isoformat(), soon.isoformat()),
                )
                urgent_tasks = cursor.fetchall()
                if not urgent_tasks:
                    return "✅ 当前没有 3 小时内到期的紧急任务。"
                lines = [f"## 🚨 紧急任务（{len(urgent_tasks)}个）", ""]
                for task in urgent_tasks[:6]:
                    due_time = task["due_time"][:16].replace("T", " ") if task["due_time"] else "无"
                    priority = task["priority"]
                    priority_label = " [!重要]" if priority == 2 else ""
                    lines.append(f"- [{due_time}] {task['content']}{priority_label}")
                return "\n".join(lines)
        except Exception as e:
            logger.error(f"检查紧急任务失败: {e}")
            return f"检查紧急任务失败: {e}"
