import os
import importlib.util
import re
import asyncio
import json
from typing import Dict, Any, Tuple, List, Optional, Iterable
from pathlib import Path

try:
    from config import CHAT_DEBUG_PRINTS
except Exception:
    CHAT_DEBUG_PRINTS = False


QQ_REMOTE_SOURCES = {"qq_gateway", "napcat_qq"}
DEFAULT_ACCESS_CONTROL = {
    "allow_local": True,
    "allow_remote_qq": True,
    "allow_qq_owner": True,
    "allow_qq_others": False,
}


class PluginManager:
    def __init__(self, plugin_dir="./plugins"):
        self.plugin_dir = plugin_dir
        self.plugins: Dict[str, Any] = {}
        self.react_map: Dict[str, Any] = {}
        self.direct_map: Dict[str, Any] = {}
        self.observe_map: Dict[str, Any] = {}
        self.disabled_plugins = set()  # 禁用的插件 trigger 列表
        self.plugin_configs: Dict[str, dict] = {}  # 存储每个插件的配置
        self.plugin_dirs: Dict[str, str] = {}  # ✅ 新增：存储 trigger -> 文件夹名的映射
        self.llm_command_map: Dict[str, str] = {}  # ✅ 新增：存储 llm_command -> trigger 的映射

        if not os.path.exists(plugin_dir):
            os.makedirs(plugin_dir)

        self.default_timeout_sec = 6.0
        self.debug_enabled = bool(CHAT_DEBUG_PRINTS)
        # 支持多种分隔符：| 、/ 和空格，非贪婪匹配直到右括号
        self._cmd_pattern = r"\[CMD:\s*([A-Za-z0-9_\-]+)\s*(?:[\|／\/]\s*|\s+)([^\]]*?)\]"

    def _dbg(self, message: str):
        if self.debug_enabled:
            print(message)

    def _normalize_access_control(self, raw_access: Optional[dict]) -> Dict[str, bool]:
        normalized = dict(DEFAULT_ACCESS_CONTROL)
        if isinstance(raw_access, dict):
            for key in normalized.keys():
                if key in raw_access:
                    normalized[key] = bool(raw_access.get(key))
        return normalized

    def _get_context_source(self, context: Optional[dict]) -> str:
        if not isinstance(context, dict):
            return ""
        return str(context.get("source") or "").strip().lower()

    def _is_remote_qq_context(self, context: Optional[dict]) -> bool:
        source = self._get_context_source(context)
        if source in QQ_REMOTE_SOURCES:
            return True
        if not isinstance(context, dict):
            return False
        channel_meta = context.get("channel_meta") or {}
        adapter = str(channel_meta.get("adapter") or "").strip().lower()
        return adapter == "napcat_qq"

    def _is_owner_context(self, context: Optional[dict]) -> bool:
        if not isinstance(context, dict):
            return False
        channel_meta = context.get("channel_meta") or {}
        return bool(channel_meta.get("is_owner"))

    def _build_access_summary(self, access_control: Optional[dict]) -> str:
        normalized = self._normalize_access_control(access_control)
        local_summary = "允许" if normalized["allow_local"] else "禁用"
        if not normalized["allow_remote_qq"]:
            qq_summary = "禁用"
        elif normalized["allow_qq_owner"] and normalized["allow_qq_others"]:
            qq_summary = "主人/其他人都可触发"
        elif normalized["allow_qq_owner"]:
            qq_summary = "仅主人可触发"
        elif normalized["allow_qq_others"]:
            qq_summary = "仅其他人可触发"
        else:
            qq_summary = "已接入但无人可触发"
        return f"本地：{local_summary}｜QQ：{qq_summary}"

    def _is_plugin_allowed(self, plugin, context: Optional[dict]) -> Tuple[bool, str]:
        access_control = self._normalize_access_control(getattr(plugin, "access_control", None))

        if self._is_remote_qq_context(context):
            if not access_control["allow_remote_qq"]:
                return False, "当前插件已关闭 QQ 触发"
            if self._is_owner_context(context):
                if not access_control["allow_qq_owner"]:
                    return False, "当前插件不允许 QQ 主人触发"
            elif not access_control["allow_qq_others"]:
                return False, "当前插件不允许其他 QQ 联系人触发"
            return True, ""

        if not access_control["allow_local"]:
            return False, "当前插件已关闭本地触发"
        return True, ""

    def _get_access_denied_message(self, plugin, context: Optional[dict], reason: str) -> str:
        plugin_name = getattr(plugin, "name", getattr(plugin, "plugin_trigger", "工具"))
        if self._is_remote_qq_context(context):
            sender_label = "QQ 主人" if self._is_owner_context(context) else "其他 QQ 联系人"
            return f"⚠️ 插件“{plugin_name}”当前不允许由{sender_label}触发：{reason}"
        return f"⚠️ 插件“{plugin_name}”当前不允许由本地入口触发：{reason}"

    # -------------------- Load --------------------
    def load_plugins(self):
        self.plugins = {}
        self.react_map = {}
        self.direct_map = {}
        self.plugin_configs = {}
        self.observe_map = {}  # ✅ 防止残留
        self.plugin_dirs = {}  # ✅ 重置文件夹映射
        self.llm_command_map = {}  # ✅ 重置LLM命令映射

        print(f"🔌 [系统] 正在扫描插件目录: {self.plugin_dir}")

        if not os.path.exists(self.plugin_dir):
            return

        # 只扫描子文件夹，不再支持单文件插件
        for item_name in os.listdir(self.plugin_dir):
            plugin_path = os.path.join(self.plugin_dir, item_name)

            # 跳过文件，只处理文件夹
            if not os.path.isdir(plugin_path):
                continue

            # 跳过以下划线开头的文件夹
            if item_name.startswith('_'):
                continue

            try:
                # 加载插件配置
                config_path = os.path.join(plugin_path, "config.json")
                if not os.path.exists(config_path):
                    print(f"⚠️ 插件文件夹 {item_name} 缺少 config.json，已跳过")
                    continue

                with open(config_path, 'r', encoding='utf-8') as f:
                    config = json.load(f)

                # 保存配置
                trigger = config.get("trigger")
                if not trigger:
                    print(f"⚠️ 插件 {item_name} 的配置缺少 trigger，已跳过")
                    continue

                config["access_control"] = self._normalize_access_control(config.get("access_control"))
                self.plugin_configs[trigger] = config
                self.plugin_dirs[trigger] = item_name  # ✅ 关键修复：记录 trigger 对应的真实文件夹名称

                # 尝试加载插件代码
                module_path = os.path.join(plugin_path, "plugin.py")
                if not os.path.exists(module_path):
                    print(f"⚠️ 插件 {item_name} 缺少 plugin.py，已跳过")
                    continue

                # 动态导入插件模块
                spec = importlib.util.spec_from_file_location(item_name, module_path)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)

                if not hasattr(module, "Plugin"):
                    print(f"⚠️ 插件 {item_name} 缺少 Plugin 类，已跳过")
                    continue

                # 创建插件实例
                inst = module.Plugin()

                # 获取LLM命令名称（如果配置中没有，使用trigger）
                llm_command = config.get("llm_command", trigger)
                inst.llm_command = llm_command
                inst.plugin_trigger = trigger
                inst.access_control = self._normalize_access_control(config.get("access_control"))
                inst.settings = config.get("settings", {}) if isinstance(config.get("settings", {}), dict) else {}

                # 从配置中设置显示元数据，name 始终以 config 为准，保证 UI/列表使用中文名
                inst.name = config.get("name", trigger)
                if not hasattr(inst, "type"):
                    inst.type = config.get("type", "react")
                if not hasattr(inst, "description"):
                    inst.description = config.get("description", "")
                if not hasattr(inst, "example_arg"):
                    inst.example_arg = config.get("example_arg", "")
                if not hasattr(inst, "aliases"):
                    inst.aliases = config.get("aliases", [trigger])
                if not hasattr(inst, "timeout_sec"):
                    inst.timeout_sec = config.get("timeout_sec") or self.default_timeout_sec

                self.plugins[trigger] = inst
                p_type = getattr(inst, "type", "react")
                print(f"   ✅ 加载插件 [{p_type}]: {getattr(inst, 'name', trigger)} (v{config.get('version', '1.0.0')})")

                # 构建LLM命令映射
                llm_command = config.get("llm_command", trigger)
                if llm_command:
                    self.llm_command_map[llm_command] = trigger
                    print(f"   📝 LLM命令映射: {llm_command} -> {trigger}")

                # 检查插件是否被禁用
                if trigger in self.disabled_plugins:
                    print(f"   ⚠️ 插件已禁用: {trigger}")
                    continue

                # 处理别名
                aliases = getattr(inst, "aliases", None)
                if not aliases:
                    aliases = [trigger]
                else:
                    aliases = list(aliases)
                    if trigger not in aliases:
                        aliases.append(trigger)

                # 根据类型映射到不同的命令字典
                if p_type == "direct":
                    for a in aliases:
                        self.direct_map[a] = inst
                elif p_type == "observe":  # 🆕 新增处理分支
                    for a in aliases:
                        self.observe_map[a] = inst
                else:
                    for a in aliases:
                        self.react_map[a] = inst

            except json.JSONDecodeError as e:
                print(f"❌ 插件 {item_name} 的 config.json 格式错误: {e}")
            except Exception as e:
                print(f"❌ 插件加载失败 {item_name}: {e}")
                import traceback
                traceback.print_exc()

    async def start_all_plugins(self):
        for name, plugin in self.plugins.items():
            if hasattr(plugin, "start") and asyncio.iscoroutinefunction(plugin.start):
                try:
                    await plugin.start()
                except Exception as e:
                    print(f"❌ 启动插件 {name} 后台任务失败: {e}")

    # -------------------- Config Management --------------------
    def get_plugin_config(self, trigger: str) -> Optional[dict]:
        """获取插件配置"""
        return self.plugin_configs.get(trigger)

    def save_plugin_config(self, trigger: str, config: dict) -> bool:
        """保存插件配置到文件"""
        if trigger not in self.plugins:
            return False

        config = dict(config or {})
        config["access_control"] = self._normalize_access_control(config.get("access_control"))

        # ✅ 关键修复：从 self.plugin_dirs 获取真实的文件夹名称
        # 如果找不到映射（理论上不可能），则回退到使用 trigger
        dir_name = self.plugin_dirs.get(trigger, trigger)

        config_path = os.path.join(self.plugin_dir, dir_name, "config.json")
        try:
            with open(config_path, 'w', encoding='utf-8') as f:
                json.dump(config, f, ensure_ascii=False, indent=4)

            # 更新内存中的配置
            self.plugin_configs[trigger] = config

            # 更新插件实例的属性
            plugin = self.plugins[trigger]
            if hasattr(plugin, "name"):
                plugin.name = config.get("name", trigger)
            if hasattr(plugin, "type"):
                plugin.type = config.get("type", "react")
            if hasattr(plugin, "description"):
                plugin.description = config.get("description", "")
            if hasattr(plugin, "example_arg"):
                plugin.example_arg = config.get("example_arg", "")
            if hasattr(plugin, "aliases"):
                plugin.aliases = config.get("aliases", [trigger])
            if hasattr(plugin, "timeout_sec"):
                plugin.timeout_sec = config.get("timeout_sec") or self.default_timeout_sec
            plugin.plugin_trigger = trigger
            plugin.access_control = self._normalize_access_control(config.get("access_control"))
            plugin.settings = config.get("settings", {}) if isinstance(config.get("settings", {}), dict) else {}

            # 调用插件的 reload_config 方法（如果存在）
            if hasattr(plugin, "reload_config") and callable(plugin.reload_config):
                try:
                    plugin.reload_config()
                    print(f"✅ 已调用插件 {trigger} 的 reload_config 方法")
                except Exception as e:
                    print(f"⚠️ 调用插件 {trigger} 的 reload_config 失败: {e}")

            self._rebuild_plugin_maps()

            return True
        except Exception as e:
            print(f"❌ 保存插件配置失败 {trigger}: {e}")
            return False

    def get_plugin_icon_path(self, trigger: str) -> Optional[str]:
        """获取插件图标路径"""
        if trigger not in self.plugins:
            return None

        config = self.plugin_configs.get(trigger, {})
        icon_file = config.get("icon", "icon.png")

        # ✅ 优化：使用正确的文件夹名称
        dir_name = self.plugin_dirs.get(trigger, trigger)
        icon_path = os.path.join(self.plugin_dir, dir_name, icon_file)

        if os.path.exists(icon_path):
            return icon_path
        return None

    def get_plugin_readme_path(self, trigger: str) -> Optional[str]:
        """获取插件 README 路径"""
        if trigger not in self.plugins:
            return None

        # ✅ 优化：使用正确的文件夹名称
        dir_name = self.plugin_dirs.get(trigger, trigger)
        readme_path = os.path.join(self.plugin_dir, dir_name, "README.md")
        if os.path.exists(readme_path):
            return readme_path
        return None

    # -------------------- Tool Prompt --------------------
    def _unique_react_plugins_by_keys(self, keys: Iterable[str]) -> List[Any]:
        seen = set()
        out = []
        for k in keys:
            p = self.react_map.get(k)
            if not p:
                p = self.plugins.get(k)
            if not p:
                continue
            if getattr(p, "type", "react") != "react":
                continue
            pid = id(p)
            if pid in seen:
                continue
            seen.add(pid)
            out.append(p)
        return out

    def get_tool_prompt_for_triggers(self, triggers: List[str], *, compact: bool = True, max_tools: int = 12) -> str:
        plugins = self._unique_react_plugins_by_keys(triggers)
        if not plugins:
            return ""

        if max_tools and len(plugins) > max_tools:
            plugins = plugins[:max_tools]

        if compact:
            lines = []
            for p in plugins:
                # 使用llm_command而不是trigger
                llm_cmd = getattr(p, "llm_command", "") or getattr(p, "trigger", "")
                desc = (getattr(p, "description", "") or "").strip()
                desc = desc.replace("\n", " ").strip()
                # 添加别名信息到描述中
                aliases = getattr(p, "aliases", [])
                if aliases and len(aliases) > 1:
                    alias_str = ", ".join([a for a in aliases if a != getattr(p, "trigger", "")])
                    desc = f"{desc} (别名: {alias_str})"
                if desc:
                    if len(desc) > 50:  # 增加描述长度限制以容纳别名信息
                        desc = desc[:50] + "…"
                    lines.append(f"- {llm_cmd}: {desc}")
                else:
                    lines.append(f"- {llm_cmd}")
            return (
                    "\n\n【工具】\n"
                    + "\n".join(lines)
                    + "\n\n【调用格式】\n"
                    + "只在需要工具时输出\"工具调用行\"，且必须独占一行：\n"
                    + "[CMD: 命令 | 参数]\n"
                    + "注意：必须使用上面列出的命令名称，格式为[CMD: 命令 | 参数]。\n"
                    + "工具调用行之外，正常回复里不要出现 [CMD: 字样。\n"
            )

        tools = []
        for p in plugins:
            llm_cmd = getattr(p, "llm_command", "") or getattr(p, "trigger", "")
            example_arg = getattr(p, "example_arg", "")
            desc = getattr(p, "description", "")
            name = getattr(p, "name", llm_cmd) or llm_cmd
            # 添加别名信息
            aliases = getattr(p, "aliases", [])
            alias_info = ""
            if aliases and len(aliases) > 1:
                alias_str = ", ".join([a for a in aliases if a != getattr(p, "trigger", "")])
                alias_info = f" (别名: {alias_str})"
            tools.append(f"- {name}: [CMD: {llm_cmd} | {example_arg}] ({desc}){alias_info}")

        return (
                "\n\n【可用工具能力】\n"
                + "\n".join(tools)
                + "\n\n【工具调用规则】\n"
                  "1) 只在确实需要工具时使用。\n"
                  "2) 工具调用必须单独成行，格式严格为：[CMD: 命令 | 参数]（参数多个用空格分隔）。\n"
                  "3) 重要：必须使用上面列出的命令名称（CMD:后面的第一个单词）。\n"
                  "4) 正常回复正文里不要出现 [CMD: 字样。\n"
        )

    def get_system_prompt_addition(self) -> str:
        all_triggers = list(self.plugins.keys())
        return self.get_tool_prompt_for_triggers(all_triggers, compact=False)

    # -------------------- Parse / Helpers --------------------
    def extract_commands(self, text: str) -> List[Tuple[str, str]]:
        raw = text or ""
        matches = re.findall(self._cmd_pattern, raw, flags=re.DOTALL)
        out = []
        for trigger, args in matches:
            out.append((trigger.strip(), (args or "").strip()))
        return out

    def contains_cmd(self, text: str) -> bool:
        return bool(re.search(self._cmd_pattern, text or "", flags=re.DOTALL))

    # -------------------- Execute --------------------
    async def _run_with_timeout(self, plugin, args: str, context: dict):
        timeout = getattr(plugin, "timeout_sec", None) or self.default_timeout_sec
        task = asyncio.create_task(plugin.run(args, context))

        try:
            return await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
        except asyncio.TimeoutError:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            return f"⚠️ 工具超时（>{timeout}s）"

    async def execute_direct_commands(self, user_text: str, context: dict) -> Tuple[bool, Optional[str]]:
        """
        Direct 模式：只要用户输入中包含插件定义的 aliases 关键词，就直接触发。
        """
        text = (user_text or "").strip()
        if not text:
            return False, None

        low = text.lower()

        # 按照关键词长度倒序排列，优先匹配长词（防止“看屏幕”被“看”先匹配截断）
        # 过滤掉极短的关键词（如1个字符），防止误触
        sorted_keys = sorted(self.direct_map.keys(), key=len, reverse=True)
        denied_message = None

        for key in sorted_keys:
            # 【核心修改】这里从 startswith 改为 in，实现“关键词包含即触发”
            if key.lower() in low:
                plugin = self.direct_map[key]
                plugin_name = getattr(plugin, 'name', key)

                allowed, reason = self._is_plugin_allowed(plugin, context)
                if not allowed:
                    denied_message = denied_message or self._get_access_denied_message(plugin, context, reason)
                    self._dbg(f"🔌 [Direct] 插件无权触发: {plugin_name} -> {reason}")
                    continue

                self._dbg(f"🔌 [Direct] 命中关键词 [{key}] -> 触发插件: {plugin_name}")

                # 将用户的原始整句话作为参数传给插件
                # 这样 plugin.py 里的 if "camera" in args 逻辑依然有效
                args = text

                try:
                    # 执行插件
                    res = await self._run_with_timeout(plugin, args, context)
                    self._dbg("🔌 [Direct] 执行成功")
                    return True, res
                except Exception as e:
                    self._dbg(f"🔌 [Direct] 执行失败: {e}")
                    import traceback
                    traceback.print_exc()
                    return True, f"⚠️ 视觉模块异常: {e}"

        if denied_message:
            return True, denied_message

        return False, None

    # -------------------- Enable/Disable Plugins --------------------

    def enable_plugin(self, trigger: str) -> bool:
        """启用插件"""
        if trigger in self.disabled_plugins:
            self.disabled_plugins.remove(trigger)

        plugin = self.plugins.get(trigger)
        if not plugin:
            return False

        p_type = getattr(plugin, "type", "react")
        aliases = getattr(plugin, "aliases", None) or [trigger]
        aliases = list(aliases)
        if trigger not in aliases:
            aliases.append(trigger)

        if p_type == "direct":
            for a in aliases:
                self.direct_map[a] = plugin
        elif p_type == "observe":
            for a in aliases:
                self.observe_map[a] = plugin
        else:
            for a in aliases:
                self.react_map[a] = plugin

        return True

    def disable_plugin(self, trigger: str) -> bool:
        """禁用插件"""
        if trigger not in self.plugins:
            return False

        if trigger not in self.disabled_plugins:
            self.disabled_plugins.add(trigger)

        plugin = self.plugins.get(trigger)
        if not plugin:
            return False

        p_type = getattr(plugin, "type", "react")
        aliases = getattr(plugin, "aliases", None) or [trigger]
        aliases = list(aliases)
        if trigger not in aliases:
            aliases.append(trigger)

        if p_type == "direct":
            for a in aliases:
                self.direct_map.pop(a, None)
        elif p_type == "observe":
            for a in aliases:
                self.observe_map.pop(a, None)
        else:
            for a in aliases:
                self.react_map.pop(a, None)

        return True

    def is_plugin_enabled(self, trigger: str) -> bool:
        """检查插件是否启用"""
        return trigger not in self.disabled_plugins

    def get_all_plugins_info(self) -> List[Dict[str, Any]]:
        """获取所有插件的信息（包括启用/禁用状态）"""
        info = []
        for trigger, plugin in self.plugins.items():
            config = self.plugin_configs.get(trigger, {})
            access_control = self._normalize_access_control(config.get("access_control"))
            info.append({
                "trigger": trigger,
                "name": getattr(plugin, "name", trigger),
                "type": getattr(plugin, "type", "react"),
                "description": getattr(plugin, "description", ""),
                "enabled": self.is_plugin_enabled(trigger),
                "version": config.get("version", "1.0.0"),
                "author": config.get("author", ""),
                "access_control": access_control,
                "access_summary": self._build_access_summary(access_control),
            })
        return info

    async def execute_observe_commands(self, user_text: str, context: dict) -> Tuple[bool, Any]:
        """
        Observe 模式：匹配关键词 -> 执行插件 -> 返回结果(但不阻断流程)
        """
        text = (user_text or "").strip()
        if not text:
            return False, None

        low = text.lower()
        # 排序防止短词遮蔽长词
        sorted_keys = sorted(self.observe_map.keys(), key=len, reverse=True)

        for key in sorted_keys:
            if key.lower() in low:
                plugin = self.observe_map[key]
                allowed, reason = self._is_plugin_allowed(plugin, context)
                if not allowed:
                    self._dbg(f"🔌 [Observe] 插件无权触发: {getattr(plugin, 'name', key)} -> {reason}")
                    continue
                self._dbg(f"🔌 [Observe] 命中关键词 [{key}] -> 触发观察: {getattr(plugin, 'name', key)}")

                try:
                    # 复用 run_with_timeout 逻辑
                    # 传入全句 args，方便插件做逻辑判断
                    res = await self._run_with_timeout(plugin, text, context)
                    return True, res
                except Exception as e:
                    self._dbg(f"🔌 [Observe] 执行失败: {e}")
                    return True, f"（数据获取失败: {e}）"

        return False, None
    async def execute_commands(self, text: str, context: dict, allow_tools: bool = True) -> Tuple[
        bool, str, List[str], List[str]]:
        """
        ReAct 工具执行：
        - 从 LLM 输出中解析 [CMD: trigger | args]
        - 返回：triggered, clean_text, tool_outputs, used_triggers
        """
        raw = text or ""
        
        self._dbg(f"\n{'='*60}")
        self._dbg("🔌 [ReAct] ========== 开始执行工具命令 ==========")
        self._dbg(f"🔌 [ReAct] LLM原始输出: {raw}")
        self._dbg(f"🔌 [ReAct] 允许工具: {allow_tools}")
        self._dbg(f"🔌 [ReAct] 上下文: {context}")
        
        matches = re.findall(self._cmd_pattern, raw, flags=re.DOTALL)

        clean_text = re.sub(self._cmd_pattern, "", raw, flags=re.DOTALL).strip()
        
        self._dbg(f"🔌 [ReAct] 解析到 {len(matches)} 个命令: {matches}")
        self._dbg(f"🔌 [ReAct] 清理后的文本: {clean_text}")

        tool_outputs: List[str] = []
        used_triggers: List[str] = []
        triggered = False

        if not allow_tools:
            self._dbg("🔌 [ReAct] 工具被禁用，跳过执行")
            self._dbg("🔌 [ReAct] ========== 工具执行结束 ==========\n")
            return False, clean_text, [], []

        self._dbg(f"🔌 [ReAct] 当前已注册的react插件: {list(self.react_map.keys())}")
        
        for idx, (llm_cmd, args) in enumerate(matches, 1):
            llm_cmd = (llm_cmd or "").strip()
            args = (args or "").strip()
            if not llm_cmd:
                self._dbg(f"🔌 [ReAct] 命令#{idx} trigger为空，跳过")
                continue

            self._dbg(f"\n🔌 [ReAct] ----- 处理命令#{idx}: {llm_cmd} | {args} -----")
            
            # 首先尝试通过LLM命令映射找到实际的trigger
            trigger = self.llm_command_map.get(llm_cmd, llm_cmd)
            if trigger != llm_cmd:
                self._dbg(f"🔌 [ReAct] LLM命令映射: {llm_cmd} -> {trigger}")
            
            triggered = True
            plugin = self.react_map.get(trigger) or self.plugins.get(trigger)
            
            if not plugin:
                self._dbg(f"🔌 [ReAct] 未找到插件: {trigger}")
                self._dbg(f"🔌 [ReAct] 可用的triggers: {list(self.plugins.keys())}")
                tool_outputs.append(f"【{trigger} 结果】未找到该工具（可能未安装/trigger 写错）")
                continue

            plugin_type = getattr(plugin, "type", "react")
            self._dbg(f"🔌 [ReAct] 插件类型: {plugin_type}")
            
            if plugin_type != "react":
                self._dbg("🔌 [ReAct] 插件类型不是react，跳过")
                continue

            plugin_name = getattr(plugin, 'name', trigger)
            allowed, reason = self._is_plugin_allowed(plugin, context)
            if not allowed:
                denied_message = self._get_access_denied_message(plugin, context, reason)
                self._dbg(f"🔌 [ReAct] 插件无权触发: {plugin_name} -> {reason}")
                tool_outputs.append(f"【{trigger} 不可用】{denied_message}")
                continue

            used_triggers.append(trigger)
            self._dbg(f"🔌 [ReAct] 找到插件: {plugin_name}")
            self._dbg(f"🔌 [ReAct] 开始执行插件: {plugin_name}")

            try:
                result = await self._run_with_timeout(plugin, args, context)
                self._dbg(f"🔌 [ReAct] 插件执行完成，结果: {result}")
                if result:
                    tool_outputs.append(result)
            except Exception as e:
                self._dbg(f"🔌 [ReAct] 插件执行异常: {e}")
                import traceback
                traceback.print_exc()
                tool_outputs.append(f"【{trigger} 错误】{e}")

        self._dbg("\n🔌 [ReAct] ========== 工具执行总结 ==========")
        self._dbg(f"🔌 [ReAct] 触发状态: {triggered}")
        self._dbg(f"🔌 [ReAct] 使用的插件: {used_triggers}")
        self._dbg(f"🔌 [ReAct] 工具输出数量: {len(tool_outputs)}")
        for i, output in enumerate(tool_outputs, 1):
            self._dbg(f"🔌 [ReAct] 输出#{i}: {output[:100]}..." if len(output) > 100 else f"🔌 [ReAct] 输出#{i}: {output}")
        self._dbg("🔌 [ReAct] ========== 工具执行结束 ==========\n")
        
        return triggered, clean_text, tool_outputs, used_triggers

    # 在 PluginManager 类中添加以下方法

    def reload_plugin(self, trigger: str) -> bool:
        """
        热重载单个插件

        Args:
            trigger: 插件触发词

        Returns:
            是否重载成功
        """
        if trigger not in self.plugins:
            print(f"❌ 插件 {trigger} 不存在")
            return False

        try:
            # 1. 停止旧插件
            old_plugin = self.plugins[trigger]
            if hasattr(old_plugin, 'stop') and callable(old_plugin.stop):
                try:
                    import asyncio
                    if asyncio.iscoroutinefunction(old_plugin.stop):
                        asyncio.run(old_plugin.stop())
                    else:
                        old_plugin.stop()
                except Exception as e:
                    print(f"⚠️ 停止旧插件失败: {e}")

            # 2. 获取插件目录
            dir_name = self.plugin_dirs.get(trigger, trigger)
            plugin_path = os.path.join(self.plugin_dir, dir_name)

            # 3. 重新加载配置
            config_path = os.path.join(plugin_path, "config.json")
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
            config["access_control"] = self._normalize_access_control(config.get("access_control"))

            # 4. 重新加载代码
            module_path = os.path.join(plugin_path, "plugin.py")
            spec = importlib.util.spec_from_file_location(f"{trigger}_reload", module_path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            # 5. 创建新实例
            inst = module.Plugin()

            # 6. 设置元数据
            llm_command = config.get("llm_command", trigger)
            inst.llm_command = llm_command
            inst.plugin_trigger = trigger
            inst.access_control = self._normalize_access_control(config.get("access_control"))
            inst.settings = config.get("settings", {}) if isinstance(config.get("settings", {}), dict) else {}

            inst.name = config.get("name", trigger)
            if not hasattr(inst, "type"):
                inst.type = config.get("type", "react")
            if not hasattr(inst, "description"):
                inst.description = config.get("description", "")
            if not hasattr(inst, "example_arg"):
                inst.example_arg = config.get("example_arg", "")
            if not hasattr(inst, "aliases"):
                inst.aliases = config.get("aliases", [trigger])
            if not hasattr(inst, "timeout_sec"):
                inst.timeout_sec = config.get("timeout_sec") or self.default_timeout_sec

            # 7. 更新插件
            self.plugins[trigger] = inst
            self.plugin_configs[trigger] = config

            # 8. 重建映射
            self._rebuild_plugin_maps()

            print(f"✅ 插件 [{trigger}] 已热重载")
            return True

        except Exception as e:
            print(f"❌ 热重载插件 {trigger} 失败: {e}")
            import traceback
            traceback.print_exc()
            return False

    def _rebuild_plugin_maps(self):
        """重建所有插件映射"""
        self.react_map.clear()
        self.direct_map.clear()
        self.observe_map.clear()
        self.llm_command_map.clear()

        for trigger, inst in self.plugins.items():
            # 跳过禁用的插件
            if trigger in self.disabled_plugins:
                continue

            # 获取插件类型
            p_type = getattr(inst, "type", "react")

            # 获取别名
            aliases = getattr(inst, "aliases", None)
            if not aliases:
                aliases = [trigger]
            else:
                aliases = list(aliases)
                if trigger not in aliases:
                    aliases.append(trigger)

            # 构建 LLM 命令映射
            llm_command = getattr(inst, "llm_command", trigger)
            if llm_command:
                self.llm_command_map[llm_command] = trigger

            # 根据类型映射
            if p_type == "direct":
                for a in aliases:
                    self.direct_map[a] = inst
            elif p_type == "observe":
                for a in aliases:
                    self.observe_map[a] = inst
            else:
                for a in aliases:
                    self.react_map[a] = inst

        print(
            f"✅ 插件映射已重建: react={len(self.react_map)}, direct={len(self.direct_map)}, observe={len(self.observe_map)}")



