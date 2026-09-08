from datetime import datetime

from services.chat_service import ChatService
from services.chat_support import search_flow_service, text_utils


class _GatewayContext:
    @staticmethod
    def conversation_session_key(_ctx):
        return "qq:owner"


def _make_chat_service() -> ChatService:
    service = object.__new__(ChatService)
    service.gateway_context_service = _GatewayContext()
    service._last_search_topic_by_session = {}
    service._load_recent_user_topic_from_store = lambda _ctx, current_text="": ""
    service._looks_structured_reply = lambda _text: False
    service.brain = None
    return service


def test_polite_search_only_message_is_generic_followup() -> None:
    assert text_utils.is_generic_search_followup_request("你可以搜索一下") is True


def test_polite_search_followup_keeps_and_resolves_previous_topic() -> None:
    service = _make_chat_service()
    ctx = {"source": "napcat_qq", "user_id": "owner"}
    topic = "你知道上周登陆中国的台风叫什么吗"

    service._remember_search_topic(topic, ctx)
    service._remember_search_topic("你可以搜索一下", ctx)

    assert service._last_search_topic_by_session["qq:owner"] == topic
    assert service._resolve_followup_search_query("你可以搜索一下", ctx) == topic


def test_search_with_explicit_topic_is_not_generic_followup() -> None:
    assert text_utils.is_generic_search_followup_request("搜索一下天气接口怎么配置") is False


def test_direct_fact_question_is_searchworthy_without_guessing() -> None:
    assert text_utils.is_direct_fact_search_question("上周登陆中国的台风叫什么") is True
    assert text_utils.is_direct_fact_search_question("上海今天天气怎么样") is False
    assert text_utils.is_direct_fact_search_question("上海最近会有什么台风吗") is True
    assert text_utils.is_direct_fact_search_question("最近会不会下雨") is False
    assert text_utils.is_direct_fact_search_question("现在多少度") is False
    assert text_utils.is_direct_fact_search_question("你今天怎么样") is False
    assert text_utils.is_direct_fact_search_question("晚饭吃什么") is False
    assert text_utils.is_direct_fact_search_question("你最近会不会来") is False
    assert text_utils.is_direct_fact_search_question("最近怎么样") is False


def test_weather_plugin_questions_are_not_searchworthy() -> None:
    assert text_utils.is_weather_plugin_question("现在多少度") is True
    assert text_utils.is_weather_plugin_question("外面温度怎么样") is True
    assert text_utils.is_weather_plugin_question("上海今天天气怎么样") is True
    assert text_utils.is_weather_plugin_question("最近会不会下雨") is True
    assert text_utils.is_weather_plugin_question("上海最近会有什么台风吗") is False
    assert text_utils.is_searchworthy_question("现在多少度") is False
    assert text_utils.is_searchworthy_question("上海今天天气怎么样") is False
    assert text_utils.is_searchworthy_question("最近会不会下雨") is False


def test_stamp_search_query_pins_today_to_system_year() -> None:
    now = datetime(2026, 9, 1, 12, 0)
    assert (
        text_utils.stamp_search_query_with_current_year("今天有什么新闻", now=now)
        == "2026年 今天有什么新闻"
    )
    assert (
        text_utils.stamp_search_query_with_current_year("2026年今天有什么新闻", now=now)
        == "2026年今天有什么新闻"
    )
    assert (
        text_utils.stamp_search_query_with_current_year("上周台风叫什么", now=now)
        == "上周台风叫什么"
    )


def test_search_plugin_schema_defers_weather_and_stamps_today() -> None:
    from plugins.search.plugin import Plugin as SearchPlugin

    plugin = SearchPlugin()
    description = plugin.get_tool_schema()["description"]
    assert "天气" in description
    assert "info_gateway" in description
    resolved = plugin._resolve_search_query("今天有什么新闻", {})
    year = str(datetime.now().year)
    assert resolved.startswith(f"{year}年 ")
    assert "今天有什么新闻" in resolved
    grok_prompt = plugin._build_grok_system_prompt({}, "今天有什么新闻")
    assert datetime.now().strftime("%Y-%m-%d") in grok_prompt
    assert "不要把去年" in grok_prompt


def test_strip_think_tags_removes_visible_cot() -> None:
    assert text_utils.strip_think_tags("<think>去年的温度</think>今天20度") == "今天20度"
    cleaned = text_utils.strip_internal_tags(
        "<think>不要说出来</think>\n<emo=think>今天大概二十度"
    )
    assert "<think>" not in cleaned
    assert "不要说出来" not in cleaned
    assert "今天大概二十度" in cleaned


def test_forced_search_runs_for_fact_question_even_if_first_reply_is_confident() -> None:
    query = search_flow_service.choose_forced_search_query(
        user_text="上周登陆中国的台风叫什么",
        first_reply="印象里好像有一个，不过我先按记得的说吧。",
        followup_query="",
        triggered=False,
        tool_results=[],
        delegate_triggers=[],
        looks_like_uncertain_answer=text_utils.looks_like_uncertain_answer,
        is_searchworthy_question=text_utils.is_searchworthy_question,
    )

    assert query == "上周登陆中国的台风叫什么"


def test_forced_search_still_runs_when_casual_question_sounds_uncertain() -> None:
    query = search_flow_service.choose_forced_search_query(
        user_text="你今天怎么样",
        first_reply="我不太确定该怎么接。",
        followup_query="",
        triggered=False,
        tool_results=[],
        delegate_triggers=[],
        looks_like_uncertain_answer=text_utils.looks_like_uncertain_answer,
        is_searchworthy_question=text_utils.is_searchworthy_question,
    )

    assert query == "你今天怎么样"


def test_forced_search_skips_confident_casual_chat() -> None:
    query = search_flow_service.choose_forced_search_query(
        user_text="晚饭吃什么",
        first_reply="随便炒个菜就行。",
        followup_query="",
        triggered=False,
        tool_results=[],
        delegate_triggers=[],
        looks_like_uncertain_answer=text_utils.looks_like_uncertain_answer,
        is_searchworthy_question=text_utils.is_searchworthy_question,
    )

    assert query == ""


def test_forced_search_skips_weather_plugin_questions() -> None:
    query = search_flow_service.choose_forced_search_query(
        user_text="现在多少度",
        first_reply="我不太确定。",
        followup_query="",
        triggered=False,
        tool_results=[],
        delegate_triggers=[],
        looks_like_uncertain_answer=text_utils.looks_like_uncertain_answer,
        is_searchworthy_question=text_utils.is_searchworthy_question,
    )

    assert query == ""


def test_forced_search_runs_for_place_time_world_question() -> None:
    query = search_flow_service.choose_forced_search_query(
        user_text="上海最近会有什么台风吗",
        first_reply="秋天偶尔会有它的尾巴扫过。",
        followup_query="",
        triggered=False,
        tool_results=[],
        delegate_triggers=[],
        looks_like_uncertain_answer=text_utils.looks_like_uncertain_answer,
        is_searchworthy_question=text_utils.is_searchworthy_question,
    )

    assert query == "上海最近会有什么台风吗"
