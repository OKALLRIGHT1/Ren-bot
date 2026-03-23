from modules.character_manager import character_manager


class Plugin:
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

    def should_handle_direct(
        self, user_text: str, context: dict, matched_alias: str
    ) -> bool:
        text = str(user_text or "").strip()
        if text.startswith("/角色") or text.startswith("/角色列表"):
            return True
        if text.startswith("/"):
            role_name = text[1:].strip()
            if not role_name:
                return False
            for _cid, data in character_manager.get_all_characters().items():
                if str(data.get("name") or "").strip() == role_name:
                    return True
        return False

    def _list_roles(self):
        chars = character_manager.get_all_characters() or {}
        active_id = character_manager.data.get("active_id")
        if not chars:
            return "当前还没有可切换的角色。"
        lines = ["🎭 当前可用角色："]
        for cid, data in chars.items():
            name = str(data.get("name") or cid)
            prefix = "⭐ " if cid == active_id else "- "
            lines.append(f"{prefix}{name}")
        lines.append("用法：/角色 角色名  或  /角色列表")
        lines.append("也支持：/角色 当前")
        lines.append("也支持直接发送：/丰川祥子")
        return "\n".join(lines)

    def _switch_role(self, role_name: str, ctx: dict):
        role_name = str(role_name or "").strip()
        if not role_name:
            return self._list_roles()
        chars = character_manager.get_all_characters() or {}
        target_id = None
        target_data = None
        for cid, data in chars.items():
            name = str(data.get("name") or "").strip()
            if name == role_name:
                target_id = cid
                target_data = data
                break
        if not target_id:
            return f"未找到角色：{role_name}\n\n{self._list_roles()}"

        chat_service = (ctx or {}).get("chat_service")
        if chat_service is not None:
            app_ref = self._get_app_ref(chat_service)
            try:
                print(
                    f"[QQRoleSwitch] app_ref={type(app_ref).__name__ if app_ref else 'None'} target_id={target_id}"
                )
                if app_ref and hasattr(app_ref, "switch_character_runtime"):
                    app_ref.switch_character_runtime(target_id)
                else:
                    character_manager.set_active_character(target_id)
                if app_ref and hasattr(app_ref, "event_bus") and app_ref.event_bus:
                    try:
                        import asyncio

                        asyncio.run_coroutine_threadsafe(
                            app_ref.event_bus.emit(
                                "ui.status",
                                text=f"角色已切换：{str((target_data or {}).get('name') or role_name)}",
                            ),
                            app_ref.loop,
                        )
                    except Exception:
                        pass
            except Exception:
                pass
        else:
            character_manager.set_active_character(target_id)
        current_costume = character_manager.get_current_costume_name(target_id)
        costume_suffix = f"\n已同步服装：{current_costume}" if current_costume else ""
        return f"✅ 已切换角色为：{str((target_data or {}).get('name') or role_name)}{costume_suffix}"

    async def run(self, args, ctx):
        text = str(args or "").strip()
        if text.startswith("/角色 当前"):
            active = character_manager.get_active_character() or {}
            active_name = str(active.get("name") or "未设置角色")
            current_costume = character_manager.get_current_costume_name()
            return f"当前角色：{active_name}\n当前服装：{current_costume or '未设置'}"
        if text.startswith("/角色列表"):
            return self._list_roles()
        if text.startswith("/角色"):
            return self._switch_role(text[len("/角色") :].strip(), ctx)
        if text.startswith("/"):
            return self._switch_role(text[1:].strip(), ctx)
        return self._list_roles()
