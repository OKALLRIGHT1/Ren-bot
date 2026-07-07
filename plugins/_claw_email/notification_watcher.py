import asyncio
import logging
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from .claw_mailer import ClawMailer
from .email_formatter import format_notification

logger = logging.getLogger("ClawEmailWatcher")


class NotificationWatcher:
    """后台轮询新邮件并推送通知。"""

    def __init__(
        self,
        mailer: ClawMailer,
        interval_sec: int = 120,
        on_new_mail: Optional[Callable] = None,
        target_sessions: Optional[List[str]] = None,
    ):
        self._mailer = mailer
        self._interval = max(30, interval_sec)
        self._on_new_mail = on_new_mail
        self._target_sessions = target_sessions or []
        self._last_seen_ids: set = set()
        self._max_seen_ids = 200  # 防止无限增长
        self._task: Optional[asyncio.Task] = None
        self._paused = False
        self._auth_fail_count = 0
        self._paused_since: Optional[float] = None
        self._resume_interval = 300  # 暂停后每 5 分钟尝试恢复

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self):
        if self.running:
            return
        self._task = asyncio.create_task(self._poll_loop())
        logger.info("邮件通知轮询已启动，间隔 %ds", self._interval)

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        logger.info("邮件通知轮询已停止")

    def update_targets(self, sessions: List[str]):
        self._target_sessions = list(sessions or [])

    def update_mailer(self, mailer: ClawMailer):
        """热更新 mailer 实例（配置变更后调用）。"""
        self._mailer = mailer

    async def _poll_loop(self):
        while True:
            try:
                await self._check_once()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("邮件轮询异常: %s", e)
            await asyncio.sleep(self._interval)

    async def _check_once(self):
        if self._paused:
            # 自动恢复：暂停后定期重试
            if self._paused_since and (asyncio.get_event_loop().time() - self._paused_since) >= self._resume_interval:
                logger.info("暂停超过 %ds，尝试恢复轮询", self._resume_interval)
                self._paused = False
                self._paused_since = None
                self._auth_fail_count = 0
            else:
                return

        result = await self._mailer.list_mails(folder="INBOX", limit=10)
        if not result.get("ok", True) and "error" in result:
            err = result.get("error", "")
            if "auth" in err.lower() or "token" in err.lower() or "401" in err:
                self._auth_fail_count += 1
                if self._auth_fail_count >= 3:
                    self._paused = True
                    self._paused_since = asyncio.get_event_loop().time()
                    logger.warning("邮件认证连续失败 %d 次，暂停轮询（%ds 后自动重试）", self._auth_fail_count, self._resume_interval)
                return
            return

        self._auth_fail_count = 0
        mails = result.get("mails", result.get("data", []))
        if not isinstance(mails, list):
            if isinstance(result.get("raw"), str):
                return
            mails = []

        if not self._last_seen_ids:
            for m in mails:
                mid = str(m.get("id", m.get("message_id", m.get("uid", ""))))
                if mid:
                    self._last_seen_ids.add(mid)
            return

        new_mails = []
        for m in mails:
            mid = str(m.get("id", m.get("message_id", m.get("uid", ""))))
            if mid and mid not in self._last_seen_ids:
                new_mails.append(m)
                self._last_seen_ids.add(mid)

        # 防止集合无限增长：保留最近的 ID
        if len(self._last_seen_ids) > self._max_seen_ids:
            ids_list = list(self._last_seen_ids)
            self._last_seen_ids = set(ids_list[-self._max_seen_ids:])

        for m in new_mails:
            try:
                notification = format_notification(m)
                if self._on_new_mail:
                    await self._on_new_mail(notification, self._target_sessions)
            except Exception as e:
                logger.warning("发送邮件通知失败: %s", e)

    def reset_seen(self):
        self._last_seen_ids.clear()
        self._paused = False
        self._paused_since = None
        self._auth_fail_count = 0
