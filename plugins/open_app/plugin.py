import subprocess
import os
from core.logger import get_logger
from plugins.plugin_utils import handle_plugin_errors

logger = get_logger()


class Plugin:
    def __init__(self):
        """初始化插件，从配置读取应用列表"""
        self.app_map = {}
        self._load_app_config()
    
    def _load_app_config(self):
        """从配置加载应用列表"""
        try:
            # 尝试从配置文件读取
            config_path = os.path.join(os.path.dirname(__file__), "config.json")
            if os.path.exists(config_path):
                import json
                with open(config_path, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                
                settings = config.get("settings", {})
                app_list = settings.get("app_list", {}).get("default", [])
                
                # 解析应用列表
                for item in app_list:
                    if "|" in item:
                        parts = item.split("|", 1)
                        if len(parts) == 2:
                            display_name, path = parts
                            display_name = display_name.strip()
                            path = path.strip()
                            
                            # 支持多个路径（用||分隔）
                            if "||" in path:
                                paths = [p.strip() for p in path.split("||")]
                                self.app_map[display_name] = paths
                            else:
                                self.app_map[display_name] = path
                
                logger.info(f"从配置加载了 {len(self.app_map)} 个应用")
        except Exception as e:
            logger.error(f"加载应用配置失败: {e}")
            # 如果配置加载失败，使用默认值
            self.app_map = {
                "计算器": "calc.exe",
                "记事本": "notepad.exe",
                "画图": "mspaint.exe",
                "任务管理器": "taskmgr.exe",
                "命令提示符": "cmd.exe"
            }
    
    def reload_config(self):
        """重新加载配置（当GUI修改配置后调用）"""
        logger.info("重新加载应用配置...")
        self._load_app_config()

    @handle_plugin_errors("快速启动")
    async def run(self, args, ctx):
        app_name = args.strip().lower()
        target_path = None

        logger.info(f"尝试启动应用: {app_name}")

        # 1. 精确/模糊查找 Key
        for key, paths in self.app_map.items():
            if key in app_name:  # 比如用户说 "打开网易云音乐"，匹配到 "网易云"
                if isinstance(paths, list):
                    # 如果是列表，尝试找到第一个存在的路径
                    for p in paths:
                        if os.path.exists(p):
                            target_path = p
                            logger.debug(f"找到应用路径: {p}")
                            break
                else:
                    # 如果是系统命令或单路径
                    if paths.endswith(".exe") and not os.path.sep in paths:
                        target_path = paths  # 系统命令直接用
                    elif os.path.exists(paths):
                        target_path = paths

                if target_path: 
                    break

        if target_path:
            try:
                # 使用 Popen 不阻塞主线程
                subprocess.Popen(target_path)
                logger.info(f"成功启动应用: {app_name}")
                return f"✅ 已为你启动 {app_name}。"
            except (FileNotFoundError, PermissionError) as e:
                logger.error(f"启动应用失败(文件/权限): {app_name}, 错误: {e}")
                return f"❌ 启动失败，文件不存在或无权限: {e}"
            except Exception as e:
                logger.error(f"启动应用异常: {app_name}, 错误: {e}")
                return f"❌ 启动失败: {e}"
        else:
            logger.warning(f"未找到应用: {args}")
            return f"⚠️ 找不到应用 '{args}'。\n💡 提示：你可以在插件的'自定义配置'中添加应用。\n格式：显示名|路径，例如：\n浏览器|C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"
