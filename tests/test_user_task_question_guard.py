from services.chat_support.followup_service import FollowupService


def _service():
    return FollowupService(
        memory_store_getter=lambda: None,
        add_memory_safe=lambda *args, **kwargs: None,
    )


def test_question_recall_is_not_extracted_as_user_task():
    service = _service()
    assert service.looks_like_question_or_recall("你还记得我上次开会说了什么吗")
    assert service.extract_task_candidates("你还记得我上次开会说了什么吗") == []
    assert service.extract_task_candidates("记得帮我买咖啡") == ["帮我买咖啡"]
