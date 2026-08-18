from services.chat_service import ChatService


def test_question_recall_is_not_extracted_as_user_task():
    service = ChatService.__new__(ChatService)
    assert service._looks_like_question_or_recall("你还记得我上次开会说了什么吗")
    assert service._extract_task_candidates("你还记得我上次开会说了什么吗") == []
    assert service._extract_task_candidates("记得帮我买咖啡") == ["帮我买咖啡"]
