from __future__ import annotations

from typing import Dict, Optional


class Plugin:
    name = "技能运行时"
    type = "direct"
    description = "管理运行时 Skill 系统，支持列出、启用、停用、重载兼容 SKILL.md 的技能目录。"
    aliases = [
        "技能列表",
        "当前技能",
        "重载技能",
        "启用技能",
        "停用技能",
        "禁用技能",
        "关闭技能系统",
        "开启技能系统",
        "skill list",
        "skill current",
        "skill reload",
        "skill enable",
        "skill disable",
        "skill on",
        "skill off",
        "skills",
    ]

    def _get_app_ref(self, chat_service):
        if chat_service is None:
            return None
        app_ref = getattr(chat_service, "app", None)
        if app_ref is not None:
            return app_ref
        try:
            import __main__

            return getattr(__main__, "app_instance", None)
        except Exception:
            return None

    def _get_manager(self, ctx: Dict):
        chat_service = (ctx or {}).get("chat_service")
        if chat_service is None:
            return None
        manager = getattr(chat_service, "skill_manager", None)
        if manager is not None:
            return manager
        app_ref = self._get_app_ref(chat_service)
        return getattr(app_ref, "skill_manager", None) if app_ref is not None else None

    def should_handle_direct(self, text: str, context: Dict, matched_alias: str) -> bool:
        raw = str(text or "").strip().lower()
        if not raw:
            return False
        prefixes = (
            "技能",
            "当前技能",
            "启用技能",
            "停用技能",
            "禁用技能",
            "重载技能",
            "开启技能系统",
            "关闭技能系统",
            "skill ",
            "skills",
        )
        return raw.startswith(prefixes)

    async def run(self, args: str, ctx: Dict) -> str:
        text = str(args or "").strip()
        manager = self._get_manager(ctx)
        if manager is None:
            return "SkillManager 尚未初始化。"

        lowered = text.lower()
        if not text or lowered in {"技能", "skills", "skill", "skill list", "技能列表"}:
            return self._render_list(manager)
        if lowered in {"当前技能", "skill current"}:
            return self._render_current(manager)
        if lowered in {"重载技能", "skill reload"}:
            count = manager.reload()
            self._persist(ctx, manager)
            return f"已重载技能库，当前共发现 {count} 个技能。"
        if lowered in {"开启技能系统", "skill on"}:
            manager.enabled = True
            self._persist(ctx, manager)
            return "技能系统已开启。"
        if lowered in {"关闭技能系统", "skill off"}:
            manager.enabled = False
            self._persist(ctx, manager)
            return "技能系统已关闭。"

        enable_target = self._extract_target(text, ("启用技能", "skill enable"))
        if enable_target:
            record = manager.enable_skill(enable_target)
            if record is None:
                return f"没有找到技能：{enable_target}"
            self._persist(ctx, manager)
            return f"已启用技能：{record.name} ({record.skill_id})"

        disable_target = self._extract_target(
            text, ("停用技能", "禁用技能", "skill disable")
        )
        if disable_target:
            record = manager.disable_skill(disable_target)
            if record is None:
                return f"没有找到技能：{disable_target}"
            self._persist(ctx, manager)
            return f"已停用技能：{record.name} ({record.skill_id})"

        detail_target = self._extract_target(text, ("技能详情", "skill show"))
        if detail_target:
            record = manager.describe_skill(detail_target)
            if record is None:
                return f"没有找到技能：{detail_target}"
            return self._render_detail(record, manager)

        return self._render_help()

    def _persist(self, ctx: Dict, manager) -> None:
        chat_service = (ctx or {}).get("chat_service")
        app_ref = self._get_app_ref(chat_service)
        if app_ref is None:
            return
        try:
            loader = getattr(app_ref, "_load_runtime_settings", None)
            saver = getattr(app_ref, "_save_runtime_settings", None)
            applier = getattr(app_ref, "apply_external_settings", None)
            if not callable(loader) or not callable(saver):
                return
            settings = loader() or {}
            if not isinstance(settings, dict):
                settings = {}
            settings.update(manager.runtime_payload())
            saver(settings)
            if callable(applier):
                applier(settings)
        except Exception:
            return

    def _extract_target(self, text: str, prefixes: tuple[str, ...]) -> str:
        raw = str(text or "").strip()
        raw_lower = raw.lower()
        for prefix in prefixes:
            prefix_lower = prefix.lower()
            if not raw_lower.startswith(prefix_lower):
                continue
            return raw[len(prefix) :].strip()
        return ""

    def _render_list(self, manager) -> str:
        records = manager.list_skills()
        active = set(manager.active_skills or [])
        lines = [
            f"技能系统：{'开启' if manager.enabled else '关闭'}",
            f"已发现技能：{len(records)}",
            f"已启用技能：{len(active)}",
        ]
        if manager.search_paths:
            lines.append("搜索目录：")
            for path in manager.search_paths[:6]:
                lines.append(f"- {path}")
        if not records:
            lines.append("当前没有发现任何 SKILL.md。可把技能放进 ./skills 或 ~/.codex/skills")
            return "\n".join(lines)
        lines.append("技能列表：")
        for record in records[:20]:
            marker = "ON" if record.skill_id in active else "--"
            desc = f" | {record.description}" if record.description else ""
            lines.append(f"- [{marker}] {record.name} ({record.skill_id}){desc}")
        if len(records) > 20:
            lines.append(f"- 其余 {len(records) - 20} 个技能未展开")
        lines.append("用法：启用技能 <名称> / 停用技能 <名称> / 重载技能 / 当前技能")
        return "\n".join(lines)

    def _render_current(self, manager) -> str:
        active = manager.get_active_records()
        lines = [f"技能系统：{'开启' if manager.enabled else '关闭'}"]
        if not active:
            lines.append("当前没有启用任何技能。")
            lines.append("可用：启用技能 <名称>")
            return "\n".join(lines)
        lines.append("当前启用技能：")
        for record in active:
            desc = f" | {record.description}" if record.description else ""
            lines.append(f"- {record.name} ({record.skill_id}){desc}")
        return "\n".join(lines)

    def _render_detail(self, record, manager) -> str:
        enabled = "是" if record.skill_id in set(manager.active_skills or []) else "否"
        lines = [
            f"技能：{record.name}",
            f"- id: {record.skill_id}",
            f"- 已启用: {enabled}",
            f"- 文件: {record.path}",
        ]
        if record.description:
            lines.append(f"- 简介: {record.description}")
        if record.aliases:
            lines.append(f"- 别名: {', '.join(record.aliases[:8])}")
        return "\n".join(lines)

    def _render_help(self) -> str:
        return "\n".join(
            [
                "技能命令：",
                "- 技能列表 / skill list",
                "- 当前技能 / skill current",
                "- 启用技能 <名称> / skill enable <名称>",
                "- 停用技能 <名称> / skill disable <名称>",
                "- 重载技能 / skill reload",
                "- 开启技能系统 / skill on",
                "- 关闭技能系统 / skill off",
            ]
        )
