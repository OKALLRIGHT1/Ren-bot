import asyncio
import json
import logging
import os
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("ClawMailer")


class ClawMailer:
    """封装 @clawemail/mail-cli 的子进程调用。

    实际 CLI 接口（v0.2.4）：
      auth test/login/logout
      folder list
      mail list --fid <id> --limit <n> --start <n> [--unread]
      mail get --ids <ids> [--fid <folder>]
      mail search --keyword <text> --fid <id> --limit <n> [--since <date>] [--unread] [--fts]
      mail mark --ids <ids> --read|--unread [--fid <id>]
      mail watch --fid <id>  (NDJSON 流)
      compose send --to <addrs> --subject <text> --body <text> [--cc/--bcc/--attach]
      compose reply --id <id> --fid <folder> --body <text> [--all]
      compose forward --id <id> --fid <folder> --to <addrs>
    """

    def __init__(
        self,
        cli_path: str = "",
        account: str = "",
        auth_token: str = "",
        profile: str = "",
    ):
        self._account = str(account or "").strip()
        self._profile = str(profile or "").strip()
        self._auth_token = auth_token
        self._apikey_ready = False
        self._cli_path = self._resolve_cli_path(cli_path)

    def _resolve_cli_path(self, explicit: str = "") -> str:
        if explicit:
            p = Path(explicit)
            if p.exists():
                return str(p)

        project_root = Path(__file__).resolve().parent.parent.parent
        candidates = [
            project_root
            / "node_modules"
            / "@clawemail"
            / "mail-cli"
            / "bin"
            / "mail-cli-binary.exe",
            project_root
            / "node_modules"
            / "@clawemail"
            / "mail-cli"
            / "bin"
            / "mail-cli",
        ]
        for c in candidates:
            if c.exists():
                return str(c)

        on_path = shutil.which("mail-cli")
        if on_path:
            return on_path

        return str(
            project_root
            / "node_modules"
            / "@clawemail"
            / "mail-cli"
            / "bin"
            / "mail-cli-binary.exe"
        )

    def _build_env(self) -> Dict[str, str]:
        env = os.environ.copy()
        token = str(self._auth_token or "").strip()
        if token:
            # mail-cli reads its auth token from environment; keep it out of argv/logs.
            env.setdefault("CLAWEMAIL_AUTH_TOKEN", token)
            env.setdefault("CLAW_MAIL_AUTH_TOKEN", token)
            env.setdefault("MAIL_CLI_AUTH_TOKEN", token)
        return env

    async def _run_cli(
        self, *args: str, timeout: int = 30
    ) -> Dict[str, Any]:
        cmd = [self._cli_path, "--json"]
        if self._profile:
            cmd.extend(["--profile", self._profile])
        cmd.extend(args)

        log_cmd = list(cmd)
        if len(log_cmd) >= 2 and log_cmd[-2:] == ["set", str(self._auth_token or "").strip()]:
            log_cmd[-1] = "<redacted>"
        logger.debug("mail-cli cmd: %s", " ".join(log_cmd))

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self._build_env(),
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
            return {"ok": False, "error": f"命令超时 ({timeout}s)"}
        except FileNotFoundError:
            return {"ok": False, "error": f"找不到 mail-cli: {self._cli_path}"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

        out = stdout.decode("utf-8", errors="replace").strip()
        err = stderr.decode("utf-8", errors="replace").strip()

        if proc.returncode != 0:
            return {"ok": False, "error": err or out or f"退出码 {proc.returncode}"}

        try:
            return json.loads(out)
        except json.JSONDecodeError:
            return {"ok": True, "raw": out}

    async def _ensure_apikey(self) -> Optional[Dict[str, Any]]:
        token = str(self._auth_token or "").strip()
        if not token or self._apikey_ready:
            return None
        result = await self._run_cli("auth", "apikey", "set", token, timeout=60)
        if result.get("ok", True) or result.get("success") is True:
            self._apikey_ready = True
            return None
        return result

    async def _run_mail_cli(self, *args: str, timeout: int = 30) -> Dict[str, Any]:
        setup_error = await self._ensure_apikey()
        if setup_error:
            return setup_error
        return await self._run_cli(*args, timeout=timeout)

    # ── auth ──

    async def auth_test(self) -> Dict[str, Any]:
        return await self._run_mail_cli("auth", "test")

    async def auth_login(self) -> Dict[str, Any]:
        args = ["auth", "login"]
        if self._account:
            args.extend(["--user", self._account])
        return await self._run_cli(*args, timeout=120)

    # ── folder ──

    async def list_folders(self) -> Dict[str, Any]:
        return await self._run_mail_cli("folder", "list")

    # ── mail ──

    async def list_mails(
        self, folder: str = "INBOX", limit: int = 10, offset: int = 0
    ) -> Dict[str, Any]:
        args = ["mail", "list", "--fid", folder, "--limit", str(limit)]
        if offset > 0:
            args.extend(["--start", str(offset)])
        return await self._run_mail_cli(*args)

    async def read_mail(self, message_id: str, folder: str = "") -> Dict[str, Any]:
        args = ["mail", "get", "--ids", message_id]
        if folder:
            args.extend(["--fid", folder])
        return await self._run_mail_cli(*args)

    async def read_mail_body(self, message_id: str, folder: str = "") -> Dict[str, Any]:
        args = ["read", "body", "--id", message_id]
        if folder:
            args.extend(["--fid", folder])
        return await self._run_mail_cli(*args)

    async def search_mails(
        self,
        query: str,
        folder: str = "INBOX",
        limit: int = 10,
        since: str = "",
        unread_only: bool = False,
        fts: bool = False,
    ) -> Dict[str, Any]:
        args = [
            "mail", "search",
            "--keyword", query,
            "--fid", folder,
            "--limit", str(limit),
        ]
        if since:
            args.extend(["--since", since])
        if unread_only:
            args.append("--unread")
        if fts:
            args.append("--fts")
        return await self._run_mail_cli(*args)

    async def mark_mail(
        self, message_id: str, read: bool = True, folder: str = ""
    ) -> Dict[str, Any]:
        args = ["mail", "mark", "--ids", message_id]
        args.append("--read" if read else "--unread")
        if folder:
            args.extend(["--fid", folder])
        return await self._run_mail_cli(*args)

    async def watch_mails(self, folder: str = "INBOX", timeout: int = 60):
        """流式监听新邮件（NDJSON），返回第一条新邮件或超时。"""
        setup_error = await self._ensure_apikey()
        if setup_error:
            return setup_error
        args = ["mail", "watch", "--fid", folder]
        cmd = [self._cli_path, "--json"]
        if self._profile:
            cmd.extend(["--profile", self._profile])
        cmd.extend(args)

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self._build_env(),
            )
            line = await asyncio.wait_for(
                proc.stdout.readline(), timeout=timeout
            )
            proc.kill()
            out = line.decode("utf-8", errors="replace").strip()
            if out:
                try:
                    return {"ok": True, "data": json.loads(out)}
                except json.JSONDecodeError:
                    return {"ok": True, "raw": out}
            return {"ok": False, "error": "无新邮件"}
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
            return {"ok": False, "error": "监听超时"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ── compose ──

    async def send_mail(
        self,
        to: str,
        subject: str,
        body: str,
        cc: Optional[str] = None,
        bcc: Optional[str] = None,
        attachments: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        args = [
            "compose", "send",
            "--to", to,
            "--subject", subject,
            "--body", body,
        ]
        if cc:
            args.extend(["--cc", cc])
        if bcc:
            args.extend(["--bcc", bcc])
        if attachments:
            for att in attachments:
                args.extend(["--attach", att])
        return await self._run_mail_cli(*args)

    async def reply_mail(
        self,
        message_id: str,
        body: str,
        folder: str = "",
        reply_all: bool = False,
    ) -> Dict[str, Any]:
        args = ["compose", "reply", "--id", message_id, "--body", body]
        if folder:
            args.extend(["--fid", folder])
        if reply_all:
            args.append("--all")
        return await self._run_mail_cli(*args)

    async def forward_mail(
        self,
        message_id: str,
        to: str,
        folder: str = "",
    ) -> Dict[str, Any]:
        args = ["compose", "forward", "--id", message_id, "--to", to]
        if folder:
            args.extend(["--fid", folder])
        return await self._run_mail_cli(*args)
