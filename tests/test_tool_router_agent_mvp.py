from modules.tool_router import ToolRouter
from plugins.app_control.plugin import Plugin as AppControlPlugin
from plugins.code_agent.plugin import Plugin as CodeAgentPlugin
from plugins.mcp_tools.plugin import Plugin as McpToolsPlugin
from plugins.moegirl_wiki.plugin import Plugin as MoegirlPlugin
from plugins.qq_music.plugin import Plugin as QQMusicPlugin
from plugins.user_files.plugin import Plugin as UserFilesPlugin
from plugins.vision.plugin import Plugin as VisionPlugin
from plugins.web_reader.plugin import Plugin as WebReaderPlugin
from plugins.workspace_ops.plugin import Plugin as WorkspaceOpsPlugin


class Plugin:
    def __init__(self, name, aliases=None):
        self.name = name
        self.aliases = aliases or []
        self.type = "direct"


def _router():
    code_agent = Plugin("代码代理", ["codex", "claude code", "cc"])
    user_files = Plugin("用户文件助手", ["用户文件", "文件助手", "下载目录"])
    direct = {"code_agent": code_agent, "user_files": user_files}
    return ToolRouter(react_map={}, direct_map=direct, delegate_map={})


def _router_with_direct_aliases():
    code_agent = Plugin("代码代理", ["codex", "claude code", "cc"])
    user_files = Plugin("用户文件助手", ["用户文件", "文件助手", "下载目录"])
    direct = {
        "code_agent": code_agent,
        "codex": code_agent,
        "cc": code_agent,
        "user_files": user_files,
        "下载目录": user_files,
    }
    return ToolRouter(react_map={}, direct_map=direct, delegate_map={})


def test_router_init_does_not_print(capsys):
    _router()

    captured = capsys.readouterr()
    assert captured.out == ""


def test_router_prefers_code_agent_for_obvious_code_delegation():
    route = _router().route("让 Codex 分析这个项目为什么启动失败")

    assert route.need_tools is True
    assert route.tool_triggers == ["code_agent"]
    assert route.reason == "code_agent_preferred"


def test_router_does_not_route_casual_codex_mentions_to_code_agent():
    route = _router().route("我喜欢 codex 这个名字")

    assert route.need_tools is False


def test_router_does_not_route_casual_direct_alias_mentions():
    route = _router_with_direct_aliases().route("我喜欢 codex 这个名字")

    assert route.need_tools is False


def test_router_handles_codex_call_phrasing():
    route = _router_with_direct_aliases().route("调用 Codex 看看这个项目")

    assert route.need_tools is True
    assert route.tool_triggers == ["code_agent"]
    assert route.reason == "code_agent_preferred"


def test_router_routes_explicit_codex_drawing_to_code_agent():
    route = _router_with_direct_aliases().route("让 Codex 帮我画一张丰川祥子的图")

    assert route.need_tools is True
    assert route.tool_triggers == ["code_agent"]
    assert route.reason == "code_agent_preferred"


def test_router_prefers_user_files_when_codex_reads_user_file():
    route = _router_with_direct_aliases().route("让 Codex 看看下载目录里的 a.txt")

    assert route.need_tools is True
    assert route.tool_triggers == ["user_files"]
    assert route.reason == "user_files_preferred"


def test_router_prefers_user_files_for_obvious_user_file_request():
    route = _router().route("帮我看看下载目录里的 a.txt")

    assert route.need_tools is True
    assert route.tool_triggers == ["user_files"]
    assert route.reason == "user_files_preferred"


def test_router_does_not_route_casual_download_mentions_to_user_files():
    route = _router().route("我今天下载了一个游戏")

    assert route.need_tools is False


def test_router_uses_code_agent_capability_when_available():
    route = ToolRouter(
        react_map={},
        direct_map={"code_agent": CodeAgentPlugin()},
        delegate_map={},
    ).route("让 Codex 分析这个项目为什么启动失败")

    assert route.need_tools is True
    assert route.tool_triggers == ["code_agent"]
    assert route.reason == "capability:code_agent.codex_task"


def test_router_uses_code_agent_capability_for_explicit_drawing_request():
    route = ToolRouter(
        react_map={},
        direct_map={"code_agent": CodeAgentPlugin()},
        delegate_map={},
    ).route("让 Codex 画一张丰川祥子的图")

    assert route.need_tools is True
    assert route.tool_triggers == ["code_agent"]
    assert route.reason == "capability:code_agent.codex_task"


def test_router_uses_user_files_capability_when_available():
    route = ToolRouter(
        react_map={},
        direct_map={"user_files": UserFilesPlugin()},
        delegate_map={},
    ).route("帮我看看下载目录里的 a.txt")

    assert route.need_tools is True
    assert route.tool_triggers == ["user_files"]
    assert route.reason == "capability:user_files.read"


def test_router_uses_web_reader_capability_when_available():
    route = ToolRouter(
        react_map={},
        direct_map={},
        delegate_map={"web_reader": WebReaderPlugin()},
    ).route("帮我解析链接 https://example.com/article")

    assert route.need_tools is True
    assert route.tool_triggers == ["web_reader"]
    assert route.reason == "capability:web_reader.read_url"


def test_router_does_not_route_casual_url_mention_to_web_reader():
    route = ToolRouter(
        react_map={},
        direct_map={},
        delegate_map={"web_reader": WebReaderPlugin()},
    ).route("这个链接是 https://example.com/article")

    assert route.need_tools is False


def test_router_uses_moegirl_capability_when_available():
    route = ToolRouter(
        react_map={},
        direct_map={},
        delegate_map={"moegirl_wiki": MoegirlPlugin()},
    ).route("查萌百 高松灯")

    assert route.need_tools is True
    assert route.tool_triggers == ["moegirl_wiki"]
    assert route.reason == "capability:moegirl.lookup"


def test_router_uses_workspace_read_capability_when_available():
    route = ToolRouter(
        react_map={},
        direct_map={},
        delegate_map={"workspace_ops": WorkspaceOpsPlugin()},
    ).route("帮我看看 README.md")

    assert route.need_tools is True
    assert route.tool_triggers == ["workspace_ops"]
    assert route.reason == "capability:workspace.read"


def test_router_uses_mcp_domain_capability_when_available():
    route = ToolRouter(
        react_map={},
        direct_map={},
        delegate_map={"mcp_tools": McpToolsPlugin()},
    ).route("查一下麦当劳优惠券")

    assert route.need_tools is True
    assert route.tool_triggers == ["mcp_tools"]
    assert route.reason == "capability:mcp_tools.domain_call"


def test_router_does_not_route_explicit_web_search_to_mcp_domain():
    route = ToolRouter(
        react_map={},
        direct_map={},
        delegate_map={"mcp_tools": McpToolsPlugin()},
    ).route("上网搜索麦当劳优惠券")

    assert route.need_tools is False


def test_router_wraps_legacy_qq_music_direct_capability():
    route = ToolRouter(
        react_map={},
        direct_map={"qq_music": QQMusicPlugin()},
        delegate_map={},
    ).route("点歌 春日影")

    assert route.need_tools is True
    assert route.tool_triggers == ["qq_music"]
    assert route.reason == "capability:qq_music.direct"


def test_router_wraps_slash_command_capability():
    route = ToolRouter(
        react_map={},
        direct_map={"app_control": AppControlPlugin()},
        delegate_map={},
    ).route("/重启")

    assert route.need_tools is True
    assert route.tool_triggers == ["app_control"]
    assert route.reason == "capability:app_control.command"


def test_router_uses_vision_screen_capability_when_available():
    route = ToolRouter(
        react_map={},
        direct_map={"vision": VisionPlugin()},
        delegate_map={},
    ).route("帮我看看屏幕")

    assert route.need_tools is True
    assert route.tool_triggers == ["vision"]
    assert route.reason == "capability:vision.screen"
