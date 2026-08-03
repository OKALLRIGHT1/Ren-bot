import os
import importlib.util
import re
import asyncio
import json
import inspect
import sys
import types
from typing import Dict, Any, Tuple, List, Optional, Iterable
from pathlib import Path
from modules.plugin_secret_store import PluginSecretStore
from modules.plugin_model_gateway import get_plugin_model_gateway
from modules.plugin_security_audit import (
    build_plugin_security_matrix,
    summarize_plugin_security_matrix,
)
from modules.security_redaction import is_secret_setting

try:
    from modules.config_schema import build_plugin_config_schema
except Exception:
    build_plugin_config_schema = None

try:
    from config import CHAT_DEBUG_PRINTS
except Exception:
    CHAT_DEBUG_PRINTS = False


QQ_REMOTE_SOURCES = {"qq_gateway", "napcat_qq"}
DIRECT_COMMAND_PREFIXES = ("/", "!", "！")
DEFAULT_ACCESS_CONTROL = {
    "allow_local": True,
    "allow_remote_qq": False,
    "allow_qq_owner": False,
    "allow_qq_others": False,
    "allow_group_without_at": False,
}


def _safe_print(message: Any = "") -> None:
    text = str(message)
    stream = getattr(sys, "stdout", None)
    encoding = getattr(stream, "encoding", None) or "utf-8"
    try:
        print(text)
    except UnicodeEncodeError:
        safe = text.encode(encoding, errors="replace").decode(encoding, errors="replace")
        print(safe)


class PluginManager:
    def __init__(self, plugin_dir="./plugins"):
        self.plugin_dir = plugin_dir
        self.plugins: Dict[str, Any] = {}
        self.react_map: Dict[str, Any] = {}
        self.delegate_map: Dict[str, Any] = {}
        self.direct_map: Dict[str, Any] = {}
        self.observe_map: Dict[str, Any] = {}
        self.disabled_plugins = set()  # 禁用的插件 trigger 列表
        self.plugin_configs: Dict[str, dict] = {}  # 存储每个插件的配置
        self.plugin_dirs: Dict[str, str] = {}  # ✅ 新增：存储 trigger -> 文件夹名的映射
        self.llm_command_map: Dict[
            str, str
        ] = {}  # ✅ 新增：存储 llm_command -> trigger 的映射
        self.secret_store = PluginSecretStore()
        self.model_gateway = get_plugin_model_gateway()
        self.deferred_tool_stats: Dict[str, Dict[str, int]] = {}
        self.load_errors: List[Dict[str, str]] = []

        if not os.path.exists(plugin_dir):
            os.makedirs(plugin_dir)

        self.default_timeout_sec = 6.0
        self.debug_enabled = bool(CHAT_DEBUG_PRINTS)
        # 支持多种分隔符：| 、/ 和空格，非贪婪匹配直到右括号
        self._cmd_pattern = (
            r"\[CMD:\s*([A-Za-z0-9_\-]+)\s*(?:[\|／\/]\s*|\s+)([^\]]*?)\]"
        )

    def _dbg(self, message: str):
        if self.debug_enabled:
            _safe_print(message)

    def _load_plugin_module(self, item_name: str, module_path: str):
        """Load plugin.py with a package context so relative imports work."""
        safe_name = re.sub(r"\W+", "_", str(item_name or "plugin")).strip("_") or "plugin"
        root_name = "_live2d_plugins"
        package_name = f"{root_name}.{safe_name}"
        module_name = f"{package_name}.plugin"

        root_pkg = sys.modules.get(root_name)
        if root_pkg is None:
            root_pkg = types.ModuleType(root_name)
            root_pkg.__path__ = []
            sys.modules[root_name] = root_pkg

        pkg = sys.modules.get(package_name)
        if pkg is None:
            pkg = types.ModuleType(package_name)
            sys.modules[package_name] = pkg
        pkg.__path__ = [os.path.abspath(os.path.dirname(module_path))]
        pkg.__package__ = package_name

        sys.modules.pop(module_name, None)
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"无法加载插件模块: {module_path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module

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

    def _is_secret_setting(self, setting_key: str, setting_info: Any) -> bool:
        return is_secret_setting(setting_key, setting_info)

    def _resolve_plugin_type(
        self,
        trigger: str,
        config: dict,
        plugin: Any,
        *,
        source: str,
        plugin_dir: Optional[str] = None,
    ) -> str:
        config_type = ""
        if isinstance(config, dict):
            config_type = str(config.get("type") or "").strip()
        class_type = str(getattr(plugin, "type", "") or "").strip()
        if config_type and class_type and config_type != class_type:
            warning = {
                "plugin": str(trigger),
                "code": "plugin_type_mismatch",
                "source": str(source),
                "config_type": config_type,
                "class_type": class_type,
            }
            if plugin_dir:
                warning["plugin_dir"] = str(plugin_dir)
            self.load_errors.append(warning)
            _safe_print(
                f"WARNING plugin type mismatch for {trigger}: "
                f"config={config_type}, class={class_type}; using config"
            )
        return config_type or class_type or "react"

    def _log_plugin_security_summary(self) -> None:
        try:
            matrix = build_plugin_security_matrix(
                self.plugin_configs,
                self._normalize_access_control,
            )
            summary = summarize_plugin_security_matrix(matrix)
        except Exception as exc:
            _safe_print(f"WARNING plugin security audit failed: {exc}")
            return
        owner_high = summary.get("owner_remote_high_risk_plugins") or []
        other_qq = summary.get("other_qq_plugins") or []
        group_no_at = summary.get("group_without_at_plugins") or []
        _safe_print(
            "[PluginSecurity] "
            f"owner_high_risk={len(owner_high)} {owner_high}; "
            f"other_qq={len(other_qq)} {other_qq}; "
            f"group_without_at={len(group_no_at)} {group_no_at}"
        )

    def _apply_secret_overrides(self, trigger: str, config: dict) -> dict:
        if not isinstance(config, dict):
            return config
        settings = config.get("settings")
        if not isinstance(settings, dict):
            return config
        config = dict(config)
        settings_copy = dict(settings)
        try:
            secrets = self.secret_store.get_all_for_plugin(trigger)
        except Exception as exc:
            message = f"secret_override_failed: {exc}"
            self.load_errors.append({"plugin": str(trigger), "error": message})
            _safe_print(f"⚠️ 插件 {trigger} 的加密配置读取失败，已使用配置文件默认值: {exc}")
            secrets = {}
        for key, value in list(settings_copy.items()):
            if not self._is_secret_setting(key, value):
                continue
            if key not in secrets:
                continue
            secret_value = secrets.get(key, "")
            if isinstance(value, dict):
                new_value = dict(value)
                new_value["default"] = secret_value
                settings_copy[key] = new_value
            else:
                settings_copy[key] = secret_value
        config["settings"] = settings_copy
        return config

    def _is_owner_context(self, context: Optional[dict]) -> bool:
        if not isinstance(context, dict):
            return False
        channel_meta = context.get("channel_meta") or {}
        return bool(channel_meta.get("is_owner"))

    def _is_group_context(self, context: Optional[dict]) -> bool:
        if not isinstance(context, dict):
            return False
        channel_meta = context.get("channel_meta") or {}
        message_type = str(channel_meta.get("message_type") or "").strip().lower()
        return message_type == "group" or bool(channel_meta.get("group_id"))

    def _is_group_mentioned_context(self, context: Optional[dict]) -> bool:
        if not isinstance(context, dict):
            return False
        channel_meta = context.get("channel_meta") or {}
        for key in (
            "mentioned",
            "is_mentioned",
            "is_at",
            "at_me",
            "to_me",
            "targets_self",
        ):
            if bool(channel_meta.get(key)):
                return True
        return False

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
        access_control = self._normalize_access_control(
            getattr(plugin, "access_control", None)
        )

        if self._is_remote_qq_context(context):
            if not access_control["allow_remote_qq"]:
                return False, "当前插件已关闭 QQ 触发"
            if self._is_owner_context(context):
                if not access_control["allow_qq_owner"]:
                    return False, "当前插件不允许 QQ 主人触发"
            elif not access_control["allow_qq_others"]:
                return False, "当前插件不允许其他 QQ 联系人触发"
            if (
                self._is_group_context(context)
                and not access_control["allow_group_without_at"]
                and not self._is_group_mentioned_context(context)
            ):
                return False, "当前插件在 QQ 群聊中需要 @ 机器人后触发"
            return True, ""

        if not access_control["allow_local"]:
            return False, "当前插件已关闭本地触发"
        return True, ""

    def _get_access_denied_message(
        self, plugin, context: Optional[dict], reason: str
    ) -> str:
        plugin_name = getattr(plugin, "name", getattr(plugin, "plugin_trigger", "工具"))
        if self._is_remote_qq_context(context):
            sender_label = (
                "QQ 主人" if self._is_owner_context(context) else "其他 QQ 联系人"
            )
            return f"⚠️ 插件“{plugin_name}”当前不允许由{sender_label}触发：{reason}"
        return f"⚠️ 插件“{plugin_name}”当前不允许由本地入口触发：{reason}"

    def _strip_direct_command_prefix(self, text: str) -> Tuple[str, bool]:
        raw = str(text or "").strip()
        if not raw:
            return "", False
        for prefix in DIRECT_COMMAND_PREFIXES:
            if raw.startswith(prefix):
                return raw[len(prefix) :].strip(), True
        return raw, False

    def _normalize_alias_list(self, value: Any) -> List[str]:
        if not value:
            return []
        if isinstance(value, str):
            rows = [value]
        else:
            try:
                rows = list(value)
            except TypeError:
                rows = [value]
        return list(dict.fromkeys(str(row).strip() for row in rows if str(row).strip()))

    def _plugin_aliases(self, trigger: str, plugin: Any) -> List[str]:
        aliases = self._normalize_alias_list(getattr(plugin, "aliases", None))
        if not aliases:
            aliases = [trigger]
        if trigger not in aliases:
            aliases.append(trigger)
        return aliases

    def _direct_command_aliases(self, plugin: Any) -> List[str]:
        return self._normalize_alias_list(
            getattr(plugin, "direct_command_aliases", None)
        )

    def _map_plugin(self, trigger: str, plugin: Any) -> None:
        p_type = getattr(plugin, "type", "react")
        aliases = self._plugin_aliases(trigger, plugin)

        if p_type == "direct":
            for alias in aliases:
                self.direct_map[alias] = plugin
        elif p_type == "delegate":
            for alias in aliases:
                self.delegate_map[alias] = plugin
        elif p_type == "observe":
            for alias in aliases:
                self.observe_map[alias] = plugin
        else:
            for alias in aliases:
                self.react_map[alias] = plugin

        for alias in self._direct_command_aliases(plugin):
            self.direct_map[alias] = plugin

    def _unmap_plugin(self, trigger: str, plugin: Any) -> None:
        p_type = getattr(plugin, "type", "react")
        aliases = self._plugin_aliases(trigger, plugin)

        maps = []
        if p_type == "direct":
            maps.append(self.direct_map)
        elif p_type == "delegate":
            maps.append(self.delegate_map)
        elif p_type == "observe":
            maps.append(self.observe_map)
        else:
            maps.append(self.react_map)

        for alias in aliases:
            for target_map in maps:
                if target_map.get(alias) is plugin:
                    target_map.pop(alias, None)

        for alias in self._direct_command_aliases(plugin):
            if self.direct_map.get(alias) is plugin:
                self.direct_map.pop(alias, None)

    # -------------------- Load --------------------
    def load_plugins(self):
        self.plugins = {}
        self.react_map = {}
        self.direct_map = {}
        self.plugin_configs = {}
        self.observe_map = {}  # ✅ 防止残留
        self.plugin_dirs = {}  # ✅ 重置文件夹映射
        self.llm_command_map = {}  # ✅ 重置LLM命令映射
        self.load_errors = []

        _safe_print(f"🔌 [系统] 正在扫描插件目录: {self.plugin_dir}")

        if not os.path.exists(self.plugin_dir):
            return

        # 只扫描子文件夹，不再支持单文件插件
        for item_name in os.listdir(self.plugin_dir):
            plugin_path = os.path.join(self.plugin_dir, item_name)

            # 跳过文件，只处理文件夹
            if not os.path.isdir(plugin_path):
                continue

            # 跳过以下划线开头的文件夹
            if item_name.startswith("_"):
                continue

            try:
                # 加载插件配置
                config_path = os.path.join(plugin_path, "config.json")
                if not os.path.exists(config_path):
                    _safe_print(f"⚠️ 插件文件夹 {item_name} 缺少 config.json，已跳过")
                    continue

                with open(config_path, "r", encoding="utf-8-sig") as f:
                    config = json.load(f)

                # 保存配置
                trigger = config.get("trigger")
                if not trigger:
                    _safe_print(f"⚠️ 插件 {item_name} 的配置缺少 trigger，已跳过")
                    continue

                config["access_control"] = self._normalize_access_control(
                    config.get("access_control")
                )
                config = self._apply_secret_overrides(trigger, config)
                self.plugin_configs[trigger] = config
                self.plugin_dirs[trigger] = (
                    item_name  # ✅ 关键修复：记录 trigger 对应的真实文件夹名称
                )

                # 尝试加载插件代码
                module_path = os.path.join(plugin_path, "plugin.py")
                if not os.path.exists(module_path):
                    _safe_print(f"⚠️ 插件 {item_name} 缺少 plugin.py，已跳过")
                    continue

                # 动态导入插件模块
                module = self._load_plugin_module(item_name, module_path)

                if not hasattr(module, "Plugin"):
                    _safe_print(f"⚠️ 插件 {item_name} 缺少 Plugin 类，已跳过")
                    continue

                # 创建插件实例
                inst = module.Plugin()

                # 获取LLM命令名称（如果配置中没有，使用trigger）
                llm_command = config.get("llm_command", trigger)
                inst.llm_command = llm_command
                inst.plugin_trigger = trigger
                inst.access_control = self._normalize_access_control(
                    config.get("access_control")
                )
                inst.settings = (
                    config.get("settings", {})
                    if isinstance(config.get("settings", {}), dict)
                    else {}
                )
                inst.tool_examples = config.get("tool_examples", [])
                if not isinstance(inst.tool_examples, list):
                    inst.tool_examples = [str(inst.tool_examples)]
                inst.direct_command_aliases = self._normalize_alias_list(
                    config.get(
                        "direct_command_aliases",
                        getattr(inst, "direct_command_aliases", []),
                    )
                )

                # 从配置中设置显示元数据，name 始终以 config 为准，保证 UI/列表使用中文名
                resolved_type = self._resolve_plugin_type(
                    trigger,
                    config,
                    inst,
                    source="load_plugins",
                    plugin_dir=item_name,
                )
                config["type"] = resolved_type
                self.plugin_configs[trigger] = config

                inst.name = config.get("name", trigger)
                inst.type = resolved_type
                if not hasattr(inst, "description"):
                    inst.description = config.get("description", "")
                if not hasattr(inst, "example_arg"):
                    inst.example_arg = config.get("example_arg", "")
                if not hasattr(inst, "aliases"):
                    inst.aliases = config.get("aliases", [trigger])
                if not hasattr(inst, "timeout_sec"):
                    inst.timeout_sec = (
                        config.get("timeout_sec") or self.default_timeout_sec
                    )

                if hasattr(inst, "reload_config") and callable(inst.reload_config):
                    try:
                        inst.reload_config()
                    except Exception as e:
                        _safe_print(f"⚠️ 插件 {trigger} 初始 reload_config 失败: {e}")

                self.plugins[trigger] = inst
                p_type = getattr(inst, "type", "react")
                _safe_print(
                    f"   ✅ 加载插件 [{p_type}]: {getattr(inst, 'name', trigger)} (v{config.get('version', '1.0.0')})"
                )

                # 构建LLM命令映射
                llm_command = config.get("llm_command", trigger)
                if llm_command:
                    self.llm_command_map[llm_command] = trigger
                    _safe_print(f"   📝 LLM命令映射: {llm_command} -> {trigger}")

                # 检查插件是否被禁用
                if trigger in self.disabled_plugins:
                    _safe_print(f"   ⚠️ 插件已禁用: {trigger}")
                    continue

                self._map_plugin(trigger, inst)

            except json.JSONDecodeError as e:
                _safe_print(f"❌ 插件 {item_name} 的 config.json 格式错误: {e}")
            except Exception as e:
                self.load_errors.append({"plugin": item_name, "error": str(e)})
                _safe_print(f"❌ 插件加载失败 {item_name}: {e}")
                import traceback

                traceback.print_exc()

        self._log_plugin_security_summary()

    async def start_all_plugins(self, context: Optional[dict] = None):
        for name, plugin in self.plugins.items():
            if hasattr(plugin, "start") and asyncio.iscoroutinefunction(plugin.start):
                try:
                    if context is not None:
                        try:
                            params = inspect.signature(plugin.start).parameters
                        except Exception:
                            params = {}
                        if len(params) >= 1:
                            await plugin.start(context)
                        else:
                            await plugin.start()
                    else:
                        await plugin.start()
                except Exception as e:
                    _safe_print(f"❌ 启动插件 {name} 后台任务失败: {e}")

    async def stop_all_plugins(self):
        for name, plugin in self.plugins.items():
            stop = getattr(plugin, "stop", None)
            if not callable(stop):
                continue
            try:
                if asyncio.iscoroutinefunction(stop):
                    await stop()
                else:
                    stop()
            except Exception as e:
                _safe_print(f"⚠️ 停止插件 {name} 后台任务失败: {e}")

    # -------------------- Config Management --------------------
    def get_plugin_config(self, trigger: str) -> Optional[dict]:
        """获取插件配置"""
        return self.plugin_configs.get(trigger)

    def get_group_no_at_keywords(self) -> List[str]:
        keywords: List[str] = []
        for trigger, config in self.plugin_configs.items():
            access = self._normalize_access_control(config.get("access_control"))
            if not access.get("allow_group_without_at"):
                continue
            if not access.get("allow_remote_qq"):
                continue
            aliases: List[str] = []
            if str(config.get("type") or "react") == "direct":
                aliases.extend(self._normalize_alias_list(config.get("aliases") or []))
                if trigger not in aliases:
                    aliases.append(trigger)
            aliases.extend(
                self._normalize_alias_list(config.get("direct_command_aliases") or [])
            )
            for alias in dict.fromkeys(aliases):
                text = str(alias or "").strip()
                if text:
                    keywords.append(text)
        # 去重并按长度降序，避免短词抢占
        unique = list(dict.fromkeys(keywords))
        return sorted(unique, key=len, reverse=True)

    def save_plugin_config(self, trigger: str, config: dict) -> bool:
        """保存插件配置到文件"""
        if trigger not in self.plugins:
            return False

        config = dict(config or {})
        provided_secret_values = config.pop("_secret_values", {})
        config["access_control"] = self._normalize_access_control(
            config.get("access_control")
        )
        secret_settings = {}
        settings = config.get("settings", {})
        if isinstance(settings, dict):
            sanitized_settings = {}
            for key, value in settings.items():
                if self._is_secret_setting(key, value):
                    secret_value = ""
                    if isinstance(value, dict):
                        secret_value = str(
                            provided_secret_values.get(key, value.get("default") or "")
                        )
                        stored_value = dict(value)
                        stored_value["default"] = ""
                        sanitized_settings[key] = stored_value
                    else:
                        secret_value = str(provided_secret_values.get(key, value or ""))
                        sanitized_settings[key] = ""
                    secret_settings[key] = secret_value
                else:
                    sanitized_settings[key] = value
            config["settings"] = sanitized_settings

        # ✅ 关键修复：从 self.plugin_dirs 获取真实的文件夹名称
        # 如果找不到映射（理论上不可能），则回退到使用 trigger
        dir_name = self.plugin_dirs.get(trigger, trigger)
        plugin = self.plugins[trigger]
        resolved_type = self._resolve_plugin_type(
            trigger,
            config,
            plugin,
            source="save_plugin_config",
            plugin_dir=dir_name,
        )
        config["type"] = resolved_type

        config_path = os.path.join(self.plugin_dir, dir_name, "config.json")
        try:
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=4)

            for secret_key, secret_value in secret_settings.items():
                self.secret_store.set_secret(trigger, secret_key, secret_value)

            effective_config = self._apply_secret_overrides(trigger, config)

            # 更新内存中的配置
            self.plugin_configs[trigger] = effective_config

            # 更新插件实例的属性
            if hasattr(plugin, "name"):
                plugin.name = config.get("name", trigger)
            if hasattr(plugin, "type"):
                plugin.type = resolved_type
            if hasattr(plugin, "description"):
                plugin.description = config.get("description", "")
            if hasattr(plugin, "example_arg"):
                plugin.example_arg = config.get("example_arg", "")
            if hasattr(plugin, "aliases"):
                plugin.aliases = config.get("aliases", [trigger])
            plugin.direct_command_aliases = self._normalize_alias_list(
                effective_config.get(
                    "direct_command_aliases",
                    getattr(plugin, "direct_command_aliases", []),
                )
            )
            if hasattr(plugin, "timeout_sec"):
                plugin.timeout_sec = (
                    effective_config.get("timeout_sec") or self.default_timeout_sec
                )
            plugin.plugin_trigger = trigger
            plugin.access_control = self._normalize_access_control(
                effective_config.get("access_control")
            )
            plugin.settings = (
                effective_config.get("settings", {})
                if isinstance(effective_config.get("settings", {}), dict)
                else {}
            )
            plugin.tool_examples = effective_config.get("tool_examples", [])
            if not isinstance(plugin.tool_examples, list):
                plugin.tool_examples = [str(plugin.tool_examples)]

            # 调用插件的 reload_config 方法（如果存在）
            if hasattr(plugin, "reload_config") and callable(plugin.reload_config):
                try:
                    plugin.reload_config()
                    _safe_print(f"✅ 已调用插件 {trigger} 的 reload_config 方法")
                except Exception as e:
                    _safe_print(f"⚠️ 调用插件 {trigger} 的 reload_config 失败: {e}")

            self._rebuild_plugin_maps()

            return True
        except Exception as e:
            _safe_print(f"❌ 保存插件配置失败 {trigger}: {e}")
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
    def _unique_plugins_by_keys(
        self, keys: Iterable[str], *, allowed_types: Optional[set[str]] = None
    ) -> List[Any]:
        seen = set()
        out = []
        for k in keys:
            p = self.react_map.get(k)
            if not p:
                p = self.delegate_map.get(k)
            if not p:
                p = self.plugins.get(k)
            if not p:
                continue
            p_type = getattr(p, "type", "react")
            if allowed_types and p_type not in allowed_types:
                continue
            pid = id(p)
            if pid in seen:
                continue
            seen.add(pid)
            out.append(p)
        return out

    def _unique_react_plugins_by_keys(self, keys: Iterable[str]) -> List[Any]:
        return self._unique_plugins_by_keys(keys, allowed_types={"react"})

    def _unique_delegate_plugins_by_keys(self, keys: Iterable[str]) -> List[Any]:
        return self._unique_plugins_by_keys(keys, allowed_types={"delegate"})

    def get_tool_prompt_for_triggers(
        self, triggers: List[str], *, compact: bool = True, max_tools: int = 12
    ) -> str:
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
                    alias_str = ", ".join(
                        [a for a in aliases if a != getattr(p, "trigger", "")]
                    )
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
                + '只在需要工具时输出"工具调用行"，且必须独占一行：\n'
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
                alias_str = ", ".join(
                    [a for a in aliases if a != getattr(p, "trigger", "")]
                )
                alias_info = f" (别名: {alias_str})"
            tools.append(
                f"- {name}: [CMD: {llm_cmd} | {example_arg}] ({desc}){alias_info}"
            )

        return (
            "\n\n【可用工具能力】\n" + "\n".join(tools) + "\n\n【工具调用规则】\n"
            "1) 只在确实需要工具时使用。\n"
            "2) 工具调用必须单独成行，格式严格为：[CMD: 命令 | 参数]（参数多个用空格分隔）。\n"
            "3) 重要：必须使用上面列出的命令名称（CMD:后面的第一个单词）。\n"
            "4) 正常回复正文里不要出现 [CMD: 字样。\n"
        )

    def get_system_prompt_addition(self) -> str:
        return self.get_deferred_tool_prompt()

    def get_deferred_tool_prompt(self, max_tools: int = 18) -> str:
        rows = []
        seen = set()
        for trigger, plugin in self.plugins.items():
            p_type = str(getattr(plugin, "type", "react") or "react")
            if p_type not in {"react", "delegate", "direct"}:
                continue
            pid = id(plugin)
            if pid in seen:
                continue
            seen.add(pid)
            name = str(getattr(plugin, "name", trigger) or trigger).strip()
            desc = str(getattr(plugin, "description", "") or "").replace("\n", " ").strip()
            if len(desc) > 42:
                desc = desc[:42] + "…"
            rows.append(f"- {name}({trigger}): {desc or p_type}")
            if len(rows) >= max_tools:
                break
        if not rows:
            return ""
        return (
            "\n\n【可延迟发现的工具】\n"
            + "当前不直接暴露完整工具参数。若你判断必须用工具，但本轮没有列出具体命令，先单独输出：\n"
            + "[CMD: tool_search | 你需要的能力或关键词]\n"
            + "系统会返回匹配工具，再继续执行真实工具。不要在闲聊时使用。\n"
            + "可发现能力摘要：\n"
            + "\n".join(rows)
        )

    def should_use_deferred_tool_flow(self, user_text: str) -> bool:
        text = str(user_text or "").strip().lower()
        if not text:
            return False
        keywords = (
            "查", "搜", "搜索", "联网", "新闻", "金价", "天气", "汇率",
            "生成", "画", "生图", "图生图", "点歌", "播放", "语音",
            "打开", "读取", "总结", "链接", "下载", "翻译", "计算", "wiki",
            "邮件", "邮箱", "email", "mail",
        )
        return any(word in text for word in keywords)

    def search_tools(
        self, query: str, limit: int = 8, context: Optional[dict] = None
    ) -> List[Dict[str, Any]]:
        q = str(query or "").strip().lower()
        terms = [part for part in re.split(r"\s+", q) if part]
        scored = []
        for trigger, plugin in self.plugins.items():
            p_type = str(getattr(plugin, "type", "react") or "react")
            if p_type not in {"react", "delegate", "direct"}:
                continue
            allowed, _reason = self._is_plugin_allowed(plugin, context)
            if not allowed:
                continue
            aliases = getattr(plugin, "aliases", []) or []
            if not isinstance(aliases, list):
                aliases = [str(aliases)]
            llm_cmd = str(getattr(plugin, "llm_command", "") or trigger)
            haystack = " ".join(
                [
                    trigger,
                    llm_cmd,
                    str(getattr(plugin, "name", "") or ""),
                    str(getattr(plugin, "description", "") or ""),
                    " ".join(str(a) for a in aliases),
                ]
            ).lower()
            score = 0
            for term in terms or [q]:
                if term and term in haystack:
                    score += 3 if term in {trigger.lower(), llm_cmd.lower()} else 1
            if q and q in haystack:
                score += 2
            usage = self.deferred_tool_stats.get(trigger, {})
            score += min(3, int(usage.get("executed", 0) or 0))
            if score <= 0:
                continue
            scored.append((score, trigger, plugin, llm_cmd))
        scored.sort(key=lambda item: item[0], reverse=True)
        rows = []
        for _score, trigger, plugin, llm_cmd in scored[: max(1, int(limit or 8))]:
            rows.append(
                {
                    "trigger": trigger,
                    "command": llm_cmd,
                    "name": str(getattr(plugin, "name", trigger) or trigger),
                    "type": str(getattr(plugin, "type", "react") or "react"),
                    "description": str(getattr(plugin, "description", "") or ""),
                    "example_arg": str(getattr(plugin, "example_arg", "") or ""),
                    "examples": list(getattr(plugin, "tool_examples", []) or []),
                }
            )
        return rows

    def _record_deferred_tool_stat(self, trigger: str, event: str) -> None:
        key = str(trigger or "").strip()
        name = str(event or "").strip()
        if not key or not name:
            return
        row = self.deferred_tool_stats.setdefault(key, {})
        row[name] = int(row.get(name, 0) or 0) + 1

    def get_deferred_tool_stats(self) -> Dict[str, Dict[str, int]]:
        return {key: dict(value) for key, value in self.deferred_tool_stats.items()}

    def get_plugin_config_schema(self, trigger: str) -> Dict[str, Any]:
        key = str(trigger or "").strip()
        config = self.plugin_configs.get(key, {})
        if build_plugin_config_schema is None:
            return {"trigger": key, "fields": []}
        return build_plugin_config_schema(key, config)

    def get_delegate_prompt_for_triggers(
        self, triggers: List[str], *, compact: bool = True, max_tools: int = 8
    ) -> str:
        plugins = self._unique_delegate_plugins_by_keys(triggers)
        if not plugins:
            return ""

        if max_tools and len(plugins) > max_tools:
            plugins = plugins[:max_tools]

        lines = []
        for p in plugins:
            llm_cmd = getattr(p, "llm_command", "") or getattr(p, "trigger", "")
            desc = (getattr(p, "description", "") or "").replace("\n", " ").strip()
            if compact and len(desc) > 60:
                desc = desc[:60] + "…"
            lines.append(f"- {llm_cmd}: {desc or '委托副脑处理的复杂任务'}")

        return (
            "\n\n【可委托任务】\n"
            + "\n".join(lines)
            + "\n\n【委托格式】\n"
            + "当任务复杂、需要多步执行或需要调用外部工具时，可单独输出一行：\n"
            + "[CMD: 命令 | 需求说明]\n"
            + "这里的命令会交给副脑处理；只输出必要的任务要求，不要把思考过程写进去。\n"
        )

    def get_delegate_trigger_set(self) -> set[str]:
        triggers = set(self.delegate_map.keys())
        for trigger, plugin in self.plugins.items():
            if getattr(plugin, "type", "react") == "delegate":
                triggers.add(trigger)
        return triggers

    def is_delegate_trigger(self, trigger: str) -> bool:
        return str(trigger or "").strip() in self.get_delegate_trigger_set()

    # -------------------- Parse / Helpers --------------------
    def _find_cmd_end(self, raw: str, args_start: int) -> int:
        depth = 0
        quote = ""
        escaped = False
        for idx in range(args_start, len(raw)):
            char = raw[idx]
            if quote:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == quote:
                    quote = ""
                continue
            if char in {'"', "'"}:
                quote = char
                continue
            if char in "[{":
                depth += 1
                continue
            if char in "]}":
                if depth > 0:
                    depth -= 1
                    continue
                if char == "]":
                    return idx
        return -1

    def _iter_command_matches(self, text: str) -> List[Tuple[str, str, int, int]]:
        raw = text or ""
        matches: List[Tuple[str, str, int, int]] = []
        pos = 0
        marker = "[CMD:"
        while True:
            start = raw.find(marker, pos)
            if start < 0:
                break
            cursor = start + len(marker)
            while cursor < len(raw) and raw[cursor].isspace():
                cursor += 1
            trigger_match = re.match(r"[A-Za-z0-9_-]+", raw[cursor:])
            if not trigger_match:
                pos = start + len(marker)
                continue
            trigger = trigger_match.group(0)
            cursor += len(trigger)
            if cursor >= len(raw) or not raw[cursor].isspace():
                if cursor >= len(raw) or raw[cursor] not in {"|", "／", "/"}:
                    pos = start + len(marker)
                    continue
            while cursor < len(raw) and raw[cursor].isspace():
                cursor += 1
            if cursor >= len(raw):
                break
            if raw[cursor] in {"|", "／", "/"}:
                cursor += 1
                while cursor < len(raw) and raw[cursor].isspace():
                    cursor += 1
            end = self._find_cmd_end(raw, cursor)
            if end < 0:
                break
            matches.append((trigger.strip(), raw[cursor:end].strip(), start, end + 1))
            pos = end + 1
        return matches

    def extract_commands(self, text: str) -> List[Tuple[str, str]]:
        out = []
        for trigger, args, _start, _end in self._iter_command_matches(text or ""):
            out.append((trigger.strip(), (args or "").strip()))
        return out

    def contains_cmd(self, text: str) -> bool:
        return bool(self._iter_command_matches(text or ""))

    # -------------------- Execute --------------------
    async def _run_with_timeout(self, plugin, args: str, context: dict):
        timeout = getattr(plugin, "timeout_sec", None) or self.default_timeout_sec
        runtime_context = self._build_runtime_context(context)
        if getattr(plugin, "type", "react") == "delegate":
            runtime_context = self._build_delegate_runtime_context(context)
        task = asyncio.create_task(plugin.run(args, runtime_context))

        try:
            return await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
        except asyncio.TimeoutError:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            return f"⚠️ 工具超时（>{timeout}s）"

    def _build_runtime_context(self, context: dict) -> dict:
        runtime = dict(context or {})
        runtime.setdefault("model_gateway", self.model_gateway)
        return runtime

    def _build_delegate_runtime_context(self, context: dict) -> dict:
        runtime = self._build_runtime_context(context)
        runtime["delegate_mode"] = True
        runtime.setdefault("allow_read", True)
        runtime.setdefault("allow_write", False)
        runtime.setdefault("allow_exec", False)
        return runtime

    async def execute_direct_commands(
        self, user_text: str, context: dict
    ) -> Tuple[bool, Optional[str]]:
        """
        Direct 模式：默认只响应 /命令；插件显式允许时才可自然语言触发。
        """
        raw_text = str(user_text or "").strip()
        text, has_command_prefix = self._strip_direct_command_prefix(raw_text)
        if not text:
            return False, None

        raw_low = raw_text.lower()
        low = text.lower()

        # 按照关键词长度倒序排列，优先匹配长词（防止“看屏幕”被“看”先匹配截断）
        # 过滤掉极短的关键词（如1个字符），防止误触
        sorted_keys = sorted(self.direct_map.keys(), key=len, reverse=True)
        denied_message = None

        for key in sorted_keys:
            key_low = key.lower()
            normalized_key, key_has_command_prefix = self._strip_direct_command_prefix(key)
            normalized_key_low = normalized_key.lower()
            matched = key_low in raw_low or (
                bool(normalized_key_low) and normalized_key_low in low
            )
            if matched:
                args = raw_text if has_command_prefix and key_has_command_prefix else text
                plugin = self.direct_map[key]
                plugin_name = getattr(plugin, "name", key)
                allow_natural_language = bool(
                    getattr(plugin, "allow_natural_language_direct", False)
                )
                if not has_command_prefix and not allow_natural_language:
                    continue

                try:
                    should_handle = getattr(plugin, "should_handle_direct", None)
                    if callable(should_handle) and not bool(
                        should_handle(args, context, key)
                    ):
                        continue
                except Exception as e:
                    self._dbg(
                        f"🔌 [Direct] should_handle_direct 检查失败: {plugin_name} -> {e}"
                    )
                    continue

                allowed, reason = self._is_plugin_allowed(plugin, context)
                if not allowed:
                    denied_message = denied_message or self._get_access_denied_message(
                        plugin, context, reason
                    )
                    _safe_print(f"🔌 [Direct] 插件无权触发: {plugin_name} -> {reason}")
                    self._dbg(f"🔌 [Direct] 插件无权触发: {plugin_name} -> {reason}")
                    continue

                _safe_print(f"🔌 [Direct] 命中关键词 [{key}] -> 触发插件: {plugin_name}")
                self._dbg(f"🔌 [Direct] 命中关键词 [{key}] -> 触发插件: {plugin_name}")

                # 将用户的原始整句话作为参数传给插件
                # 这样 plugin.py 里的 if "camera" in args 逻辑依然有效
                try:
                    # 执行插件
                    res = await self._run_with_timeout(plugin, args, context)
                    _safe_print(f"🔌 [Direct] 执行成功: {plugin_name}")
                    self._dbg("🔌 [Direct] 执行成功")
                    return True, res
                except Exception as e:
                    _safe_print(f"🔌 [Direct] 执行失败: {plugin_name} -> {e}")
                    self._dbg(f"🔌 [Direct] 执行失败: {e}")
                    import traceback

                    traceback.print_exc()
                    return True, f"⚠️ 视觉模块异常: {e}"

        if denied_message and has_command_prefix:
            _safe_print(f"🔌 [Direct] 命令被拒绝: {denied_message}")
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

        self._map_plugin(trigger, plugin)

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

        self._unmap_plugin(trigger, plugin)

        return True

    def is_plugin_enabled(self, trigger: str) -> bool:
        """检查插件是否启用"""
        return trigger not in self.disabled_plugins

    def get_all_plugins_info(self) -> List[Dict[str, Any]]:
        """获取所有插件的信息（包括启用/禁用状态）"""
        info = []
        for trigger, plugin in self.plugins.items():
            config = self.plugin_configs.get(trigger, {})
            access_control = self._normalize_access_control(
                config.get("access_control")
            )
            info.append(
                {
                    "trigger": trigger,
                    "name": getattr(plugin, "name", trigger),
                    "type": getattr(plugin, "type", "react"),
                    "description": getattr(plugin, "description", ""),
                    "enabled": self.is_plugin_enabled(trigger),
                    "version": config.get("version", "1.0.0"),
                    "author": config.get("author", ""),
                    "access_control": access_control,
                    "access_summary": self._build_access_summary(access_control),
                }
            )
        return info

    async def execute_observe_commands(
        self, user_text: str, context: dict
    ) -> Tuple[bool, Any]:
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
                    self._dbg(
                        f"🔌 [Observe] 插件无权触发: {getattr(plugin, 'name', key)} -> {reason}"
                    )
                    continue
                self._dbg(
                    f"🔌 [Observe] 命中关键词 [{key}] -> 触发观察: {getattr(plugin, 'name', key)}"
                )

                try:
                    # 复用 run_with_timeout 逻辑
                    # 传入全句 args，方便插件做逻辑判断
                    res = await self._run_with_timeout(plugin, text, context)
                    return True, res
                except Exception as e:
                    self._dbg(f"🔌 [Observe] 执行失败: {e}")
                    return True, f"（数据获取失败: {e}）"

        return False, None

    async def execute_commands(
        self,
        text: str,
        context: dict,
        allow_tools: bool = True,
        allowed_types: Optional[set[str]] = None,
    ) -> Tuple[bool, str, List[str], List[str]]:
        """
        ReAct 工具执行：
        - 从 LLM 输出中解析 [CMD: trigger | args]
        - 返回：triggered, clean_text, tool_outputs, used_triggers
        """
        raw = text or ""

        self._dbg(f"\n{'=' * 60}")
        self._dbg("🔌 [ReAct] ========== 开始执行工具命令 ==========")
        self._dbg(f"🔌 [ReAct] LLM原始输出: {raw}")
        self._dbg(f"🔌 [ReAct] 允许工具: {allow_tools}")
        self._dbg(f"🔌 [ReAct] 上下文: {context}")

        parsed_matches = self._iter_command_matches(raw)
        matches = [(trigger, args) for trigger, args, _start, _end in parsed_matches]

        clean_parts = []
        last = 0
        for _trigger, _args, start, end in parsed_matches:
            clean_parts.append(raw[last:start])
            last = end
        clean_parts.append(raw[last:])
        clean_text = "".join(clean_parts).strip()

        self._dbg(f"🔌 [ReAct] 解析到 {len(matches)} 个命令: {matches}")
        self._dbg(f"🔌 [ReAct] 清理后的文本: {clean_text}")

        tool_outputs: List[str] = []
        used_triggers: List[str] = []
        triggered = False

        if not allow_tools:
            self._dbg("🔌 [ReAct] 工具被禁用，跳过执行")
            self._dbg("🔌 [ReAct] ========== 工具执行结束 ==========\n")
            return False, clean_text, [], []

        allowed_types = set(allowed_types or {"react"})
        self._dbg(
            f"🔌 [ReAct] 当前允许的插件类型: {sorted(allowed_types)} | react={list(self.react_map.keys())} | delegate={list(self.delegate_map.keys())}"
        )

        for idx, (llm_cmd, args) in enumerate(matches, 1):
            llm_cmd = (llm_cmd or "").strip()
            args = (args or "").strip()
            if not llm_cmd:
                self._dbg(f"🔌 [ReAct] 命令#{idx} trigger为空，跳过")
                continue

            self._dbg(f"\n🔌 [ReAct] ----- 处理命令#{idx}: {llm_cmd} | {args} -----")

            if llm_cmd == "tool_search":
                triggered = True
                matches_rows = [
                    row
                    for row in self.search_tools(args, context=context)
                    if str(row.get("type") or "react") in allowed_types
                ]
                if not matches_rows:
                    tool_outputs.append(f"【tool_search 结果】没有找到和“{args}”匹配的工具。")
                    used_triggers.append("tool_search")
                    self._record_deferred_tool_stat("tool_search", "empty")
                    continue
                lines = []
                for row in matches_rows:
                    example = row.get("example_arg") or "参数"
                    desc = str(row.get("description") or "").replace("\n", " ").strip()
                    if len(desc) > 80:
                        desc = desc[:80] + "…"
                    lines.append(
                        f"- {row.get('name')}：命令 [CMD: {row.get('command')} | {example}]；类型={row.get('type')}；{desc}"
                    )
                tool_outputs.append(
                    "【tool_search 结果】匹配到这些工具。若需要执行，请下一步只输出其中一个真实工具命令：\n"
                    + "\n".join(lines)
                )
                used_triggers.append("tool_search")
                self._record_deferred_tool_stat("tool_search", "matched")
                for row in matches_rows:
                    self._record_deferred_tool_stat(str(row.get("trigger") or ""), "suggested")
                continue

            # 首先尝试通过LLM命令映射找到实际的trigger
            trigger = self.llm_command_map.get(llm_cmd, llm_cmd)
            if trigger != llm_cmd:
                self._dbg(f"🔌 [ReAct] LLM命令映射: {llm_cmd} -> {trigger}")

            triggered = True
            plugin = (
                self.react_map.get(trigger)
                or self.delegate_map.get(trigger)
                or self.plugins.get(trigger)
            )

            if not plugin:
                self._dbg(f"🔌 [ReAct] 未找到插件: {trigger}")
                self._dbg(f"🔌 [ReAct] 可用的triggers: {list(self.plugins.keys())}")
                tool_outputs.append(
                    f"【{trigger} 结果】未找到该工具（可能未安装/trigger 写错）"
                )
                continue

            plugin_type = getattr(plugin, "type", "react")
            self._dbg(f"🔌 [ReAct] 插件类型: {plugin_type}")

            if plugin_type not in allowed_types:
                self._dbg(f"🔌 [ReAct] 插件类型 {plugin_type} 不在允许集合中，跳过")
                continue

            plugin_name = getattr(plugin, "name", trigger)
            allowed, reason = self._is_plugin_allowed(plugin, context)
            if not allowed:
                denied_message = self._get_access_denied_message(
                    plugin, context, reason
                )
                self._dbg(f"🔌 [ReAct] 插件无权触发: {plugin_name} -> {reason}")
                tool_outputs.append(f"【{trigger} 不可用】{denied_message}")
                continue

            used_triggers.append(trigger)
            self._record_deferred_tool_stat(trigger, "executed")
            self._dbg(f"🔌 [ReAct] 找到插件: {plugin_name}")
            self._dbg(f"🔌 [ReAct] 开始执行插件: {plugin_name}")

            try:
                runtime_context = context
                if plugin_type == "delegate":
                    runtime_context = self._build_delegate_runtime_context(context)
                    self._dbg(
                        "🔌 [ReAct] delegate 插件自动注入 delegate_mode=True"
                    )
                result = await self._run_with_timeout(plugin, args, runtime_context)
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
            self._dbg(
                f"🔌 [ReAct] 输出#{i}: {output[:100]}..."
                if len(output) > 100
                else f"🔌 [ReAct] 输出#{i}: {output}"
            )
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
            _safe_print(f"❌ 插件 {trigger} 不存在")
            return False

        try:
            # 1. 停止旧插件
            old_plugin = self.plugins[trigger]
            if hasattr(old_plugin, "stop") and callable(old_plugin.stop):
                try:
                    import asyncio

                    if asyncio.iscoroutinefunction(old_plugin.stop):
                        asyncio.run(old_plugin.stop())
                    else:
                        old_plugin.stop()
                except Exception as e:
                    _safe_print(f"⚠️ 停止旧插件失败: {e}")

            # 2. 获取插件目录
            dir_name = self.plugin_dirs.get(trigger, trigger)
            plugin_path = os.path.join(self.plugin_dir, dir_name)

            # 3. 重新加载配置
            config_path = os.path.join(plugin_path, "config.json")
            with open(config_path, "r", encoding="utf-8-sig") as f:
                config = json.load(f)
            config["access_control"] = self._normalize_access_control(
                config.get("access_control")
            )
            config = self._apply_secret_overrides(trigger, config)

            # 4. 重新加载代码
            module_path = os.path.join(plugin_path, "plugin.py")
            module = self._load_plugin_module(dir_name, module_path)

            # 5. 创建新实例
            inst = module.Plugin()

            # 6. 设置元数据
            llm_command = config.get("llm_command", trigger)
            inst.llm_command = llm_command
            inst.plugin_trigger = trigger
            inst.access_control = self._normalize_access_control(
                config.get("access_control")
            )
            inst.settings = (
                config.get("settings", {})
                if isinstance(config.get("settings", {}), dict)
                else {}
            )
            inst.tool_examples = config.get("tool_examples", [])
            if not isinstance(inst.tool_examples, list):
                inst.tool_examples = [str(inst.tool_examples)]
            inst.direct_command_aliases = self._normalize_alias_list(
                config.get(
                    "direct_command_aliases",
                    getattr(inst, "direct_command_aliases", []),
                )
            )

            resolved_type = self._resolve_plugin_type(
                trigger,
                config,
                inst,
                source="reload_plugin",
                plugin_dir=dir_name,
            )
            config["type"] = resolved_type

            inst.name = config.get("name", trigger)
            inst.type = resolved_type
            if not hasattr(inst, "description"):
                inst.description = config.get("description", "")
            if not hasattr(inst, "example_arg"):
                inst.example_arg = config.get("example_arg", "")
            if not hasattr(inst, "aliases"):
                inst.aliases = config.get("aliases", [trigger])
            if not hasattr(inst, "timeout_sec"):
                inst.timeout_sec = config.get("timeout_sec") or self.default_timeout_sec

            if hasattr(inst, "reload_config") and callable(inst.reload_config):
                try:
                    inst.reload_config()
                except Exception as e:
                    _safe_print(f"⚠️ 插件 {trigger} 热重载 reload_config 失败: {e}")

            # 7. 更新插件
            self.plugins[trigger] = inst
            self.plugin_configs[trigger] = config

            # 8. 重建映射
            self._rebuild_plugin_maps()

            _safe_print(f"✅ 插件 [{trigger}] 已热重载")
            return True

        except Exception as e:
            _safe_print(f"❌ 热重载插件 {trigger} 失败: {e}")
            import traceback

            traceback.print_exc()
            return False

    def _rebuild_plugin_maps(self):
        """重建所有插件映射"""
        self.react_map.clear()
        self.delegate_map.clear()
        self.direct_map.clear()
        self.observe_map.clear()
        self.llm_command_map.clear()

        for trigger, inst in self.plugins.items():
            # 跳过禁用的插件
            if trigger in self.disabled_plugins:
                continue

            # 构建 LLM 命令映射
            llm_command = getattr(inst, "llm_command", trigger)
            if llm_command:
                self.llm_command_map[llm_command] = trigger

            self._map_plugin(trigger, inst)

        _safe_print(
            f"✅ 插件映射已重建: react={len(self.react_map)}, delegate={len(self.delegate_map)}, direct={len(self.direct_map)}, observe={len(self.observe_map)}"
        )
