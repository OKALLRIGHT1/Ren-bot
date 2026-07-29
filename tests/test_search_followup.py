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


def test_search_acknowledgement_is_topic_aware_and_not_fixed() -> None:
    acknowledgement = search_flow_service.build_search_acknowledgement(
        "查一下宝可梦风波的最新信息"
    )

    assert "宝可梦风波" in acknowledgement
    assert acknowledgement != "好，我查一下"
    assert len(acknowledgement) <= 36


def test_search_acknowledgement_avoids_duplicate_possessive_particle() -> None:
    acknowledgement = search_flow_service.build_search_acknowledgement(
        "搜索一下北京今天的天气"
    )

    assert "天气的最新情况" not in acknowledgement
