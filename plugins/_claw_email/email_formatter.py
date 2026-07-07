import html
import re
from datetime import datetime
from typing import Any, Dict, List


def format_mail_list(mails: List[Dict[str, Any]], folder: str) -> str:
    if not mails:
        return f"📭 {folder} 没有邮件。"
    lines = [f"📬 {folder} 共 {len(mails)} 封邮件：\n"]
    for i, m in enumerate(mails, 1):
        sender = m.get("from", m.get("sender", "未知"))
        subject = m.get("subject", "（无主题）")
        date = m.get("date", m.get("received_at", ""))
        unread = "●" if not m.get("read", True) else "○"
        lines.append(f"  {unread} {i}. [{date}] {sender} — {subject}")
    return "\n".join(lines)


def format_mail_detail(mail: Dict[str, Any]) -> str:
    sender = mail.get("from", mail.get("sender", "未知"))
    to = mail.get("to", "")
    cc = mail.get("cc", "")
    subject = mail.get("subject", "（无主题）")
    date = mail.get("date", mail.get("received_at", ""))
    body = _body_source(mail)
    body = _plain_body(body)

    lines = [
        f"📧 邮件详情",
        f"发件人: {sender}",
        f"收件人: {to}",
    ]
    if cc:
        lines.append(f"抄送: {cc}")
    lines.extend([
        f"日期: {date}",
        f"主题: {subject}",
        f"---",
        body if body else "（无正文内容）",
    ])
    return "\n".join(lines)


def _plain_body(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("content", "") or value.get("text", "") or value.get("raw", "")
    if not value:
        return ""
    text = str(value)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</(div|p|li|tr|h[1-6])>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def _body_source(mail: Dict[str, Any]) -> Any:
    for key in ("body", "text", "html"):
        value = mail.get(key)
        if isinstance(value, dict):
            if value.get("content") or value.get("text") or value.get("raw"):
                return value
        elif value:
            return value
    return mail.get("raw", "")


def format_mail_summary(mails: List[Dict[str, Any]], period_days: int = 1) -> str:
    if not mails:
        return f"📭 过去 {period_days} 天没有邮件。"

    sender_count: Dict[str, int] = {}
    subjects: List[str] = []
    unread = 0
    for m in mails:
        sender = m.get("from", m.get("sender", "未知"))
        sender_count[sender] = sender_count.get(sender, 0) + 1
        subj = m.get("subject", "")
        if subj:
            subjects.append(subj)
        if not m.get("read", True):
            unread += 1

    lines = [f"📊 邮件摘要（过去 {period_days} 天）"]
    lines.append(f"  总计: {len(mails)} 封 | 未读: {unread} 封\n")

    lines.append("  按发件人：")
    for sender, count in sorted(
        sender_count.items(), key=lambda x: x[1], reverse=True
    )[:10]:
        lines.append(f"    {sender}: {count} 封")

    if subjects:
        lines.append("\n  主要主题：")
        for s in subjects[:8]:
            lines.append(f"    - {s}")

    return "\n".join(lines)


def format_notification(mail: Dict[str, Any]) -> str:
    sender = mail.get("from", mail.get("sender", "未知"))
    subject = mail.get("subject", "（无主题）")
    preview = mail.get("body_preview", mail.get("preview", ""))
    if preview:
        preview = preview[:80].replace("\n", " ")
    text = f"📧 新邮件\n发件人: {sender}\n主题: {subject}"
    if preview:
        text += f"\n预览: {preview}"
    return text


def format_auth_result(result: Dict[str, Any]) -> str:
    if result.get("ok") or result.get("success"):
        account = result.get("account", result.get("email", "未知"))
        return f"✅ 认证有效。账号: {account}"
    error = result.get("error", result.get("message", "未知错误"))
    return f"❌ 认证失败: {error}。请重新登录。"


def format_error(action: str, error: str) -> str:
    return f"❌ 邮件操作 [{action}] 失败: {error}"
