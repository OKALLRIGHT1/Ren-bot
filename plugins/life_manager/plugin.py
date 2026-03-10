import sqlite3
import os
import datetime
import asyncio
import shutil
import dateparser  # 确保安装: pip install dateparser
from core.logger import get_logger
from plugins.plugin_utils import handle_plugin_errors, async_io_operation, safe_get_context

logger = get_logger()


class Plugin:
    def __init__(self):
        base_dir = os.path.dirname(__file__)
        self.db_path = os.path.abspath(os.path.join(base_dir, "../../userdata.db"))
        self.legacy_db_path = os.path.abspath(os.path.join(base_dir, "../userdata.db"))
        self._migrate_legacy_db_if_needed()
        self._init_db()
        # 存储 send_bubble 的引用，供后台任务使用
        self._send_bubble = None

    def _migrate_legacy_db_if_needed(self):
        """兼容旧版本数据库路径，避免提醒/账单历史丢失。"""
        try:
            if os.path.exists(self.db_path):
                if os.path.exists(self.legacy_db_path) and self.legacy_db_path != self.db_path:
                    logger.warning(
                        f"检测到旧库仍存在：{self.legacy_db_path}，当前使用主库：{self.db_path}"
                    )
                return

            if os.path.exists(self.legacy_db_path) and self.legacy_db_path != self.db_path:
                os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
                shutil.copy2(self.legacy_db_path, self.db_path)
                logger.info(f"已迁移生活管家数据库：{self.legacy_db_path} -> {self.db_path}")
        except Exception as e:
            logger.warning(f"数据库迁移检查失败，继续使用当前路径：{e}")

    def _init_db(self):
        """初始化数据库表结构"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute('''CREATE TABLE IF NOT EXISTS reminders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    content TEXT NOT NULL,
                    trigger_time TEXT NOT NULL,
                    status INTEGER DEFAULT 0
                )''')
                conn.execute('''CREATE TABLE IF NOT EXISTS bills (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    item TEXT NOT NULL,
                    amount REAL NOT NULL,
                    created_at TEXT NOT NULL
                )''')
            logger.info("生活管家数据库初始化完成")
        except Exception as e:
            logger.error(f"数据库初始化失败: {e}")

    async def start(self):
        """插件加载时启动后台轮询"""
        logger.info("生活管家后台提醒服务已启动")
        asyncio.create_task(self._check_reminders_loop())

    async def _check_reminders_loop(self):
        """后台检查提醒的循环"""
        while True:
            try:
                now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

                # 数据库查询放到线程池，防止卡顿
                def _query_due():
                    try:
                        with sqlite3.connect(self.db_path) as conn:
                            cur = conn.cursor()
                            cur.execute("SELECT id, content FROM reminders WHERE trigger_time <= ? AND status = 0",
                                        (now_str,))
                            return cur.fetchall()
                    except Exception as e:
                        logger.error(f"查询提醒失败: {e}")
                        return []

                rows = await asyncio.to_thread(_query_due)

                if rows:
                    def _mark_done(ids):
                        try:
                            with sqlite3.connect(self.db_path) as conn:
                                for rid in ids:
                                    conn.execute("UPDATE reminders SET status = 1 WHERE id = ?", (rid,))
                        except Exception as e:
                            logger.error(f"标记提醒失败: {e}")

                    # 标记为已读
                    await asyncio.to_thread(_mark_done, [r[0] for r in rows])

                    for _, content in rows:
                        logger.info(f"提醒触发: {content}")
                        
                        # 如果有 send_bubble 引用则发送气泡
                        if self._send_bubble:
                            try:
                                await self._send_bubble(f"🔔 提醒时间到！\n{content}")
                            except Exception as e:
                                logger.error(f"发送提醒气泡失败: {e}")
                        else:
                            # 降级到 print
                            print(f"🔔 提醒时间到！\n{content}")

            except Exception as e:
                logger.error(f"生活管家循环错误: {e}")

            await asyncio.sleep(10)  # 每10秒检查一次

    @handle_plugin_errors("生活管家")
    async def run(self, args, ctx):
        """主入口：处理用户请求"""
        # 保存 send_bubble 引用供后台任务使用
        self._send_bubble = safe_get_context(ctx, 'send_bubble')
        
        # 将同步的数据库操作包装成异步函数
        return await asyncio.to_thread(self._sync_run, args)

    def _sync_run(self, args):
        """同步执行数据库操作（会在线程池中运行）"""
        args = (args or "").strip()
        parts = args.split(' ', 1)
        action = parts[0].strip() if parts else ""
        params = parts[1] if len(parts) > 1 else ""

        # 别名兼容
        if action in ["add_record", "记账"]: action = "add_bill"
        if action in ["提醒", "闹钟"]: action = "add_reminder"
        if action in ["查账", "账单"]: action = "list_bills"

        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

                if action == "add_reminder":
                    if not params: 
                        logger.warning("提醒参数为空")
                        return "❌ 请提供时间，例如：[CMD: database | add_reminder 10分钟后 关火]"

                    # 简单分割：第一次出现的空格分隔时间和内容
                    sub_parts = params.split(' ', 1)
                    if len(sub_parts) < 2:
                        logger.warning("提醒格式错误")
                        return "❌ 格式错误，请使用：时间 + 空格 + 内容"

                    raw_time, content = sub_parts[0], sub_parts[1]
                    dt = dateparser.parse(raw_time, settings={'PREFER_DATES_FROM': 'future'})

                    if not dt:
                        logger.warning(f"时间解析失败: {raw_time}")
                        return f"❌ 无法识别时间：{raw_time}"

                    final_time = dt.strftime("%Y-%m-%d %H:%M")
                    cursor.execute("INSERT INTO reminders (trigger_time, content) VALUES (?, ?)", (final_time, content))
                    logger.info(f"添加提醒: {final_time} -> {content}")
                    return f"✅ 已设定提醒：{final_time} -> {content}"

                elif action == "add_bill":
                    # 支持 "15 奶茶" 或 "奶茶 15"
                    p_parts = params.split()
                    if len(p_parts) < 2: 
                        logger.warning("账单格式错误")
                        return "❌ 格式错误，请提供金额和名称"

                    item, amount = None, None
                    for p in p_parts:
                        try:
                            amount = float(p)
                        except:
                            item = p

                    if amount is None or item is None:
                        logger.warning(f"账单解析失败: {params}")
                        return "❌ 无法识别金额"

                    cursor.execute("INSERT INTO bills (item, amount, created_at) VALUES (?, ?, ?)",
                                   (item, amount, current_time))
                    logger.info(f"添加账单: {item} {amount}元")
                    return f"💰 已记录：{item} {amount}元"

                elif action == "list_bills":
                    cursor.execute("SELECT item, amount, created_at FROM bills ORDER BY id DESC LIMIT 5")
                    rows = cursor.fetchall()
                    if not rows: 
                        logger.debug("账单为空")
                        return "📒 账本目前是空的。"

                    res = ["📒 最近5笔支出："]
                    for r in rows:
                        # r[2] 是时间 2023-xx-xx
                        short_time = r[2][5:]
                        res.append(f"- {short_time} {r[0]}: {r[1]}元")
                    return "\n".join(res)

                else:
                    logger.warning(f"未知指令: {action}")
                    return f"未知的数据库指令: {action}"

        except Exception as e:
            logger.error(f"数据库错误: {e}")
            return f"数据库错误: {e}"
