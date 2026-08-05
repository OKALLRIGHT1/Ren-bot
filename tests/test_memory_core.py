from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from modules.memory_core import MemoryCoreService
from modules.memory_sqlite import MemorySQLite


def _store(tmp_path: Path) -> MemorySQLite:
    return MemorySQLite(str(tmp_path / "memory.sqlite"))


def test_memory_core_migrates_legacy_rows_once(tmp_path):
    store = _store(tmp_path)
    store.upsert_item(
        {
            "id": "legacy_pref",
            "type": "preference",
            "text": "用户喜欢简短直接的回答",
            "tags": ["role:user", "reply_style"],
            "confidence": 0.9,
            "allow_legacy_write": True,
        }
    )
    store.upsert_episode(
        {
            "id": "legacy_episode",
            "status": "active",
            "title": "项目会议",
            "summary": "上次项目会议持续了四十分钟",
        }
    )

    core = MemoryCoreService(store)
    first = core.initialize()
    second = core.initialize()

    assert first["schema_version"] >= 1
    assert second["schema_version"] == first["schema_version"]
    assert second["migrated"] == 0
    records = core.list_memory_records(limit=20)
    source_ids = {item["source_id"] for item in records}
    assert "legacy_pref" in source_ids
    assert "legacy_episode" in source_ids


def test_memory_core_repairs_legacy_character_profiles_without_owner_leak(tmp_path):
    store = _store(tmp_path)
    for item_id, role_id in (("old_name", "default_char"), ("new_name", "suzu")):
        store.upsert_item(
            {
                "id": item_id,
                "type": "agent_profile",
                "text": "五十铃怜",
                "tags": [f"role:{role_id}", "name"],
                "confidence": 0.9,
                "allow_legacy_write": True,
            }
        )
    for item_id, role_id in (("old_song", "default_char"), ("new_song", "suzu")):
        store.upsert_item(
            {
                "id": item_id,
                "type": "agent_profile",
                "text": "《迷星叫》",
                "tags": [f"role:{role_id}", "likes", "music"],
                "confidence": 0.9,
                "allow_legacy_write": True,
            }
        )
    store.upsert_item(
        {
            "id": "placeholder_trait",
            "type": "agent_profile",
            "text": "温柔 / 冷静 (初始性格)",
            "tags": ["role:char_test", "traits"],
            "allow_legacy_write": True,
        }
    )

    core = MemoryCoreService(
        store,
        character_catalog_getter=lambda: {
            "suzu": {"name": "五十铃怜", "aliases": []},
            "char_test": {"name": "测试角色", "aliases": []},
        },
    )
    first = core.initialize()
    second = core.initialize()

    assert first["repaired"] >= 3
    assert second["repaired"] == 0
    assert (tmp_path / "memory.pre-character-profile-repair-v1.bak.sqlite").exists()
    rows = core.list_memory_records(status="", limit=50)
    active_song_rows = [
        row
        for row in rows
        if row["status"] == "active" and row["content"] == "《迷星叫》"
    ]
    assert len(active_song_rows) == 1
    assert active_song_rows[0]["subject_id"] == "character:suzu"
    assert active_song_rows[0]["key"].startswith("likes.music.")
    assert any(
        row["status"] == "superseded" and row["content"] == "《迷星叫》"
        for row in rows
    )
    assert any(
        row["status"] == "archived" and row["source_id"] == "placeholder_trait"
        for row in rows
    )
    owner_profile = core.get_person_profile("owner", max_items=20)
    assert "迷星叫" not in owner_profile.text


def test_character_profile_is_separate_from_user_profile(tmp_path):
    core = MemoryCoreService(_store(tmp_path))
    core.initialize()
    core.upsert_memory_record(
        kind="preference",
        key="likes.music.song",
        content="喜欢《诗超绊》",
        subject_id="character:suzu",
        source_type="test",
        source_id="character-song",
    )
    core.upsert_memory_record(
        kind="preference",
        key="likes.music.user",
        content="用户喜欢古典音乐",
        subject_id="owner",
        source_type="test",
        source_id="owner-song",
    )

    assert "诗超绊" in core.get_character_profile("suzu").text
    assert "诗超绊" not in core.get_person_profile("owner").text


def test_memory_core_creates_one_time_backup_before_migration(tmp_path):
    store = _store(tmp_path)
    store.add_transcript("user", "迁移前的对话")

    core = MemoryCoreService(store)
    core.initialize()

    backup = tmp_path / "memory.pre-memory-core-v1.bak.sqlite"
    assert backup.exists()
    first_size = backup.stat().st_size

    core.initialize()

    assert backup.stat().st_size == first_size


def test_memory_core_settings_limit_recall_context(tmp_path):
    store = _store(tmp_path)

    def fake_llm(messages, *, task_type="summary", caller=""):
        if caller == "memory_impression":
            return "用户询问上次会议"
        if caller == "memory_selector":
            ids = []
            for line in messages[-1]["content"].splitlines():
                if line.startswith("id="):
                    ids.append(line.split("id=", 1)[1].split()[0])
            return '{"selected_ids":' + str(ids).replace("'", '"') + "}"
        return ""

    core = MemoryCoreService(
        store,
        llm_call=fake_llm,
        settings={
            "memory_core_candidate_limit": 2,
            "memory_core_final_limit": 1,
            "memory_core_context_max_chars": 80,
            "memory_core_profile_max_items": 1,
            "memory_core_impression_window": 2,
        },
    )
    core.initialize()
    for index in range(4):
        core.upsert_memory_record(
            kind="episode",
            content=f"上次会议记录 {index}：" + "发布计划" * 20,
            session_id="local:owner",
            subject_id="owner",
            source_type="test",
            source_id=f"meeting-{index}",
            confidence=0.95,
        )

    result = core.build_reply_context(
        "我上次会议说了什么",
        session_id="local:owner",
        person_id="owner",
        recent_messages=[
            {"role": "user", "content": f"旧消息 {index}"} for index in range(6)
        ],
    )

    assert len(result.selected_ids) == 1
    assert len(result.memory_text) <= 80
    assert result.diagnostics["candidate_count"] <= 2


def test_memory_core_learning_can_be_disabled(tmp_path):
    store = _store(tmp_path)
    calls = []

    def fake_llm(messages, *, task_type="summary", caller=""):
        calls.append(caller)
        return '{"items":[]}'

    core = MemoryCoreService(
        store,
        llm_call=fake_llm,
        settings={
            "memory_core_profile_learning_enabled": False,
            "memory_core_expression_learning_enabled": False,
            "memory_core_learning_batch_messages": 2,
        },
    )
    core.initialize()
    core.record_message(
        "user",
        "以后叫我 master",
        session_id="owner_shared",
        person_id="owner",
        character_name="高松灯",
        meta={"source": "text_input"},
    )
    core.record_message(
        "assistant",
        "嗯，知道了",
        session_id="owner_shared",
        person_id="owner",
        character_name="高松灯",
        meta={"source": "text_input"},
    )

    assert "profile_extract_v2" not in calls
    assert "expression_learner_v2" not in calls


def test_category_override_preserves_existing_metadata(tmp_path):
    core = MemoryCoreService(_store(tmp_path))
    core.initialize()
    record_id = core.upsert_memory_record(
        kind="preference",
        key="likes.general.0",
        content="绘画",
        subject_id="owner",
        source_type="test",
        source_id="category-record",
        metadata={"legacy_source": "profile.json", "evidence_marker": "keep"},
    )

    assert core.set_memory_category_override(record_id, "likes.art") is True
    row = core.get_memory_record(record_id)
    assert row["metadata"] == {
        "legacy_source": "profile.json",
        "evidence_marker": "keep",
        "category_override": "likes.art",
    }

    assert core.set_memory_category_override(record_id, "") is True
    row = core.get_memory_record(record_id)
    assert row["metadata"] == {
        "legacy_source": "profile.json",
        "evidence_marker": "keep",
    }


def test_category_override_rejects_unknown_category(tmp_path):
    core = MemoryCoreService(_store(tmp_path))
    core.initialize()
    record_id = core.upsert_memory_record(
        kind="fact",
        content="测试记忆",
        source_type="test",
        source_id="invalid-category",
    )

    with pytest.raises(ValueError, match="unknown memory category"):
        core.set_memory_category_override(record_id, "likes.unknown")


def test_activity_query_handles_present_and_missing_days(tmp_path):
    store = _store(tmp_path)
    core = MemoryCoreService(store)
    core.initialize()
    today = date.today().isoformat()

    assert "没有可靠的活动记录" in core.query_activity("我昨天学习了吗")

    store.save_daily_screen_stats(
        today,
        {
            "date": today,
            "durations": {"Visual Studio Code": 3600, "浏览器": 600},
        },
    )

    result = core.query_activity("我今天学习了多久")
    assert "Visual Studio Code 60 分钟" in result


def test_recall_selector_can_reject_unrelated_memories(tmp_path):
    store = _store(tmp_path)

    def fake_llm(messages, *, task_type="summary", caller=""):
        prompt = messages[-1]["content"]
        if caller == "memory_impression":
            return "用户正在询问上次项目会议讨论了什么"
        if caller == "memory_selector":
            selected = []
            for line in prompt.splitlines():
                if "发布计划" in line and "id=" in line:
                    selected.append(line.split("id=", 1)[1].split()[0])
            return '{"selected_ids":' + str(selected).replace("'", '"') + "}"
        return ""

    core = MemoryCoreService(store, llm_call=fake_llm)
    core.initialize()
    core.upsert_memory_record(
        kind="episode",
        content="上次项目会议讨论了发布计划",
        session_id="local:owner",
        source_type="test",
        source_id="meeting",
        confidence=0.95,
    )
    core.upsert_memory_record(
        kind="episode",
        content="上海前几天天气很热",
        session_id="local:owner",
        source_type="test",
        source_id="weather",
        confidence=0.95,
    )

    result = core.build_reply_context(
        "我上次项目会议讨论了什么",
        session_id="local:owner",
        person_id="owner",
        recent_messages=[{"role": "user", "content": "我上次项目会议讨论了什么"}],
    )

    assert "发布计划" in result.memory_text
    assert "天气" not in result.memory_text
    assert result.intent == "episode"


def test_episode_recall_searches_older_transcript_by_query(tmp_path):
    store = _store(tmp_path)
    exact_id = store.add_transcript(
        "assistant",
        "上次的会开了三个小时四十七分",
        session_id="owner_shared",
    )
    for index in range(260):
        store.add_transcript(
            "user",
            f"上次无关问题 {index}",
            session_id="owner_shared",
        )
        store.add_transcript(
            "assistant",
            f"无关的最近消息 {index}",
            session_id="owner_shared",
        )
    def fake_llm(messages, *, task_type="summary", caller=""):
        if caller == "memory_impression":
            return "用户询问上次开会持续了多久"
        if caller == "memory_selector":
            selected = []
            for line in messages[-1]["content"].splitlines():
                if "三个小时四十七分" in line and "id=" in line:
                    selected.append(line.split("id=", 1)[1].split()[0])
            return '{"selected_ids":' + str(selected).replace("'", '"') + "}"
        return ""

    core = MemoryCoreService(store, llm_call=fake_llm)
    core.initialize()
    result = core.build_reply_context(
        "还记得我上次开会开了多久吗",
        session_id="owner_shared",
        person_id="owner",
        recent_messages=[],
    )

    assert "三个小时四十七分" in result.memory_text
    assert result.selected_ids == (f"tr:{exact_id}",)


def test_episode_recall_includes_answer_after_matching_old_question(tmp_path):
    store = _store(tmp_path)
    store.add_transcript(
        "user",
        "还记得我上次开会开了多久吗",
        session_id="owner_shared",
    )
    answer_id = store.add_transcript(
        "assistant",
        "是三个小时四十七分，Master酱",
        session_id="owner_shared",
    )
    for index in range(260):
        store.add_transcript(
            "user",
            "还记得我上次开会开了多久吗",
            session_id="owner_shared",
        )
        store.add_transcript(
            "assistant",
            f"没找到可靠记录，我不记得 {index}",
            session_id="owner_shared",
        )
    store.add_transcript(
        "user",
        "还记得我上次开会是什么时候吗",
        session_id="owner_shared",
    )
    store.add_transcript(
        "assistant",
        "没找到可靠记录，我不想骗你说记得",
        session_id="owner_shared",
    )
    store.add_transcript(
        "user",
        "还记得我之前投了什么论文吗",
        session_id="owner_shared",
    )
    store.add_transcript(
        "assistant",
        "记得，是关于 HOMURA 模型的研究",
        session_id="owner_shared",
    )

    core = MemoryCoreService(store)
    core.initialize()
    result = core.build_reply_context(
        "还记得我上次开会开了多久吗",
        session_id="owner_shared",
        person_id="owner",
        recent_messages=[],
    )

    assert "三个小时四十七分" in result.memory_text
    assert result.selected_ids == (f"tr:{answer_id}",)


def test_plain_chat_does_not_recall_long_term_memory(tmp_path):
    store = _store(tmp_path)
    core = MemoryCoreService(store)
    core.initialize()
    core.upsert_memory_record(
        kind="episode",
        content="无关的历史事件",
        source_type="test",
        source_id="old",
    )

    result = core.build_reply_context(
        "今天心情怎么样",
        session_id="local:owner",
        person_id="owner",
        recent_messages=[],
    )

    assert result.intent == "none"
    assert result.memory_text == ""


def test_habit_weekday_query_uses_episode_and_requires_evidence(tmp_path):
    store = _store(tmp_path)
    core = MemoryCoreService(store)
    core.initialize()

    empty = core.build_reply_context(
        "我平常都是周几开会",
        session_id="local:owner",
        person_id="owner",
        recent_messages=[],
        use_llm=False,
    )
    assert empty.intent == "episode"
    assert empty.memory_text == ""

    core.upsert_memory_record(
        kind="fact",
        key="habit.meeting_weekday",
        content="用户固定周四开会",
        subject_id="owner",
        source_type="test",
        source_id="habit-thursday",
    )
    hit = core.build_reply_context(
        "我平常都是周几开会",
        session_id="local:owner",
        person_id="owner",
        recent_messages=[],
        use_llm=False,
    )
    assert hit.intent == "episode"
    assert "周四" in hit.memory_text


def test_format_active_tasks_skips_question_like_todos(tmp_path):
    from datetime import datetime, timezone

    from modules.memory_sqlite import format_active_tasks_for_prompt

    store = _store(tmp_path)
    now = datetime.now(timezone.utc).isoformat()
    store.upsert_item(
        {
            "type": "todo",
            "status": "active",
            "text": "还记得我上次开会是什么时候吗",
            "source": "task_agent",
            "updated_at": now,
        }
    )
    store.upsert_item(
        {
            "type": "todo",
            "status": "active",
            "text": "周四准备周会材料",
            "source": "task_agent",
            "updated_at": now,
        }
    )
    prompt = format_active_tasks_for_prompt(store, limit=6)
    assert "周四准备周会材料" in prompt
    assert "还记得我上次开会" not in prompt


def test_manual_profile_fact_is_not_overwritten(tmp_path):
    store = _store(tmp_path)
    core = MemoryCoreService(store)
    core.initialize()
    core.upsert_memory_record(
        kind="preference",
        key="preferred_address",
        content="称呼用户为 master",
        subject_id="owner",
        source_type="manual",
        source_id="address",
        manual_lock=True,
        confidence=1.0,
    )
    core.upsert_memory_record(
        kind="preference",
        key="preferred_address",
        content="称呼用户为主人",
        subject_id="owner",
        source_type="learned",
        source_id="address_guess",
        confidence=0.8,
    )

    profile = core.get_person_profile("owner")
    assert "master" in profile.text.lower()
    assert "主人" not in profile.text


def test_expression_selection_is_contextual(tmp_path):
    store = _store(tmp_path)

    def fake_llm(messages, *, task_type="summary", caller=""):
        prompt = messages[-1]["content"]
        if caller == "expression_selector":
            selected = []
            for line in prompt.splitlines():
                if "用户确认修复成功" in line and "id=" in line:
                    selected.append(line.split("id=", 1)[1].split()[0])
            return '{"selected_ids":' + str(selected).replace("'", '"') + "}"
        return ""

    core = MemoryCoreService(store, llm_call=fake_llm)
    core.initialize()
    core.upsert_expression_pattern(
        character_name="高松灯",
        scene="chat",
        situation="用户确认修复成功",
        style="短短地松一口气，不写总结",
        examples=["嗯，这样就好了"],
        source="test",
        quality_score=9.0,
    )
    core.upsert_expression_pattern(
        character_name="高松灯",
        scene="chat",
        situation="用户很难过",
        style="安静地陪伴",
        examples=["我在这里"],
        source="test",
        quality_score=9.0,
    )

    hints = core.select_expressions(
        user_text="已经修好了",
        character_name="高松灯",
        scene="chat",
        recent_messages=[],
    )

    assert len(hints) == 1
    assert "松一口气" in hints[0]
    assert "安静地陪伴" not in hints[0]


def test_expression_selection_uses_stable_character_id_after_rename(tmp_path):
    core = MemoryCoreService(_store(tmp_path))
    core.initialize()
    core.upsert_expression_pattern(
        character_id="char_tomori",
        character_name="旧角色名",
        scene="chat",
        situation="用户确认结果",
        style="保持简短",
        examples=["嗯"],
        source="test",
        quality_score=9.0,
    )

    hints = core.select_expressions(
        user_text="结果确认好了",
        character_id="char_tomori",
        character_name="新角色名",
        scene="chat",
    )

    assert any("保持简短" in hint for hint in hints)


def test_explicit_owner_profile_statement_is_learned(tmp_path):
    store = _store(tmp_path)

    def fake_llm(messages, *, task_type="summary", caller=""):
        if caller == "profile_extract_v2":
            prompt = messages[-1]["content"]
            evidence_id = next(
                line.split("id=", 1)[1].split()[0]
                for line in prompt.splitlines()
                if line.startswith("id=")
            )
            return (
                '{"items":[{"kind":"preference","key":"preferred_address",'
                '"content":"称呼用户为 master","confidence":0.98,'
                f'"valid_days":0,"evidence_ids":["{evidence_id}"]}}]}}'
            )
        return '{"items":[]}'

    core = MemoryCoreService(store, llm_call=fake_llm)
    core.initialize()
    core.record_message(
        "user",
        "以后叫我 master",
        session_id="owner_shared",
        person_id="owner",
        character_name="高松灯",
        meta={"source": "text_input"},
    )

    assert "master" in core.get_person_profile("owner").text.lower()


def test_meeting_weekday_correction_triggers_profile_learning(tmp_path):
    store = _store(tmp_path)
    calls = []

    def fake_llm(messages, *, task_type="summary", caller=""):
        calls.append(caller)
        if caller == "profile_extract_v2":
            prompt = messages[-1]["content"]
            evidence_id = next(
                line.split("id=", 1)[1].split()[0]
                for line in prompt.splitlines()
                if line.startswith("id=") and "其实是周四" in line
            )
            return (
                '{"items":[{"kind":"fact","key":"habit.meeting_weekday",'
                '"content":"用户固定周四开会","confidence":0.95,'
                f'"valid_days":0,"evidence_ids":["{evidence_id}"]}}]}}'
            )
        return '{"items":[]}'

    core = MemoryCoreService(store, llm_call=fake_llm)
    core.initialize()
    assert MemoryCoreService._has_explicit_profile_signal("其实是周四哦")
    core.record_message(
        "user",
        "我平常都是周几开会",
        session_id="owner_shared",
        person_id="owner",
        meta={"source": "text_input"},
    )
    core.record_message(
        "assistant",
        "查了下记事 好像周三和周五比较多",
        session_id="owner_shared",
        person_id="owner",
        meta={"source": "text_input"},
    )
    core.record_message(
        "user",
        "其实是周四哦",
        session_id="owner_shared",
        person_id="owner",
        meta={"source": "text_input"},
    )

    assert "profile_extract_v2" in calls
    hit = core.build_reply_context(
        "我平常都是周几开会",
        session_id="owner_shared",
        person_id="owner",
        recent_messages=[],
        use_llm=False,
    )
    assert "周四" in hit.memory_text


def test_other_qq_users_are_not_automatic_learning_sources(tmp_path):
    store = _store(tmp_path)
    calls = []

    def fake_llm(messages, *, task_type="summary", caller=""):
        calls.append(caller)
        return '{"items":[]}'

    core = MemoryCoreService(store, llm_call=fake_llm)
    core.initialize()
    core.record_message(
        "user",
        "我喜欢把所有回复写得很长",
        session_id="private:42",
        person_id="qq:42",
        character_name="高松灯",
        meta={"source": "qq_gateway", "is_owner": False, "user_id": "42"},
    )

    assert "profile_extract_v2" not in calls
    assert core.get_person_profile("qq:42").text == ""


def test_other_person_scope_cannot_recall_owner_or_global_memories(tmp_path):
    store = _store(tmp_path)
    core = MemoryCoreService(store)
    core.initialize()
    core.upsert_memory_record(
        kind="episode",
        content="上次项目会议讨论了 owner 的发布计划",
        subject_id="owner",
        source_type="test",
        source_id="owner-meeting",
        confidence=1.0,
    )
    core.upsert_memory_record(
        kind="episode",
        content="上次项目会议记录了全局旧数据",
        subject_id="",
        source_type="test",
        source_id="global-meeting",
        confidence=1.0,
    )
    core.upsert_memory_record(
        kind="episode",
        content="上次项目会议讨论了 QQ 用户自己的事项",
        subject_id="qq:42",
        session_id="private:42",
        source_type="test",
        source_id="qq-meeting",
        confidence=1.0,
    )

    result = core.build_reply_context(
        "还记得我上次项目会议吗",
        session_id="private:42",
        person_id="qq:42",
        recent_messages=[],
    )

    assert "QQ 用户自己的事项" in result.memory_text
    assert "owner 的发布计划" not in result.memory_text
    assert "全局旧数据" not in result.memory_text


def test_memory_record_mutations_enqueue_rebuildable_vector_jobs(tmp_path):
    core = MemoryCoreService(_store(tmp_path))
    core.initialize()

    record_id = core.upsert_memory_record(
        kind="fact",
        key="meeting.duration",
        content="上次会议持续四十分钟",
        subject_id="owner",
        source_type="test",
        source_id="vector-job",
    )
    jobs = core.list_vector_jobs(status="pending")
    assert [(item["record_id"], item["operation"]) for item in jobs] == [
        (record_id, "upsert")
    ]

    core.mark_vector_job_indexed(
        record_id,
        model="bge-m3",
        dimension=1024,
        content_hash="hash-v1",
    )
    assert core.vector_job_stats() == {
        "pending": 0,
        "processing": 0,
        "indexed": 1,
        "failed": 0,
    }

    assert core.update_memory_record(record_id, content="会议持续四十五分钟")
    assert core.list_vector_jobs(status="pending")[0]["operation"] == "upsert"

    assert core.update_memory_record(record_id, status="archived")
    assert core.list_vector_jobs(status="pending")[0]["operation"] == "delete"

    core.mark_vector_job_failed(record_id, "embedding offline")
    assert core.vector_job_stats()["failed"] == 1

    assert core.delete_memory_record(record_id)
    delete_job = core.list_vector_jobs(status="pending")[0]
    assert delete_job["record_id"] == record_id
    assert delete_job["operation"] == "delete"


def test_memory_core_merges_vector_only_candidates_by_record_id(tmp_path):
    store = _store(tmp_path)
    record_id = ""

    def vector_search(text, *, person_id, session_id, limit):
        assert person_id == "owner"
        assert session_id == "owner_shared"
        assert limit >= 1
        return [
            {"id": record_id, "vector_score": 0.92},
            {"id": record_id, "vector_score": 0.88},
        ]

    core = MemoryCoreService(store, vector_search=vector_search)
    core.initialize()
    record_id = core.upsert_memory_record(
        kind="episode",
        content="午后的发布会议最终持续四十分钟",
        subject_id="owner",
        session_id="owner_shared",
        source_type="test",
        source_id="vector-only-recall",
        confidence=1.0,
    )

    result = core.build_reply_context(
        "还记得咱们上次讨论时长吗",
        session_id="owner_shared",
        person_id="owner",
        recent_messages=[],
    )

    assert "发布会议最终持续四十分钟" in result.memory_text
    assert result.selected_ids == (record_id,)
    assert result.diagnostics["vector_candidate_count"] == 1
    assert result.diagnostics["vector_status"] == "ok"


def test_memory_core_vector_failure_preserves_lexical_recall(tmp_path):
    def vector_search(*_args, **_kwargs):
        raise RuntimeError("embedding offline")

    core = MemoryCoreService(_store(tmp_path), vector_search=vector_search)
    core.initialize()
    record_id = core.upsert_memory_record(
        kind="episode",
        content="项目会议持续四十分钟",
        subject_id="owner",
        source_type="test",
        source_id="lexical-fallback",
        confidence=1.0,
    )

    result = core.build_reply_context(
        "还记得项目会议持续四十分钟吗",
        person_id="owner",
        recent_messages=[],
    )

    assert result.selected_ids == (record_id,)
    assert result.diagnostics["vector_status"] == "unavailable"
    assert result.diagnostics["vector_error"] == "embedding offline"


def test_memory_core_discards_foreign_person_vector_candidates(tmp_path):
    foreign_id = ""

    def vector_search(*_args, **_kwargs):
        return [{"id": foreign_id, "vector_score": 0.99}]

    core = MemoryCoreService(_store(tmp_path), vector_search=vector_search)
    core.initialize()
    foreign_id = core.upsert_memory_record(
        kind="episode",
        content="owner 的私有会议记录",
        subject_id="owner",
        source_type="test",
        source_id="foreign-vector",
        confidence=1.0,
    )

    result = core.build_reply_context(
        "还记得上次会议吗",
        session_id="private:42",
        person_id="qq:42",
        recent_messages=[],
    )

    assert result.memory_text == ""
    assert foreign_id not in result.selected_ids


def test_memory_core_rebuild_vector_jobs_reflects_active_records(tmp_path):
    core = MemoryCoreService(_store(tmp_path))
    core.initialize()
    active_id = core.upsert_memory_record(
        kind="fact",
        content="仍然有效的记忆",
        subject_id="owner",
        source_type="test",
        source_id="rebuild-active",
    )
    archived_id = core.upsert_memory_record(
        kind="fact",
        content="已经归档的记忆",
        subject_id="owner",
        source_type="test",
        source_id="rebuild-archived",
    )
    core.mark_vector_job_indexed(
        active_id, model="bge-m3", dimension=1024, content_hash="active"
    )
    core.update_memory_record(archived_id, status="archived")

    queued = core.rebuild_vector_jobs()
    jobs = {item["record_id"]: item for item in core.list_vector_jobs(status="pending")}

    assert queued == 2
    assert jobs[active_id]["operation"] == "upsert"
    assert jobs[archived_id]["operation"] == "delete"


def test_memory_core_notifies_background_vector_worker_after_mutations(tmp_path):
    notifications = []
    core = MemoryCoreService(
        _store(tmp_path),
        vector_job_notifier=lambda: notifications.append("queued"),
    )
    core.initialize()

    record_id = core.upsert_memory_record(
        kind="fact",
        content="需要后台索引",
        subject_id="owner",
        source_type="test",
        source_id="notify-vector",
    )
    assert core.update_memory_record(record_id, content="已经更新")
    assert core.delete_memory_record(record_id)

    assert notifications == ["queued", "queued", "queued"]


def test_expression_selection_is_scoped_by_person(tmp_path):
    store = _store(tmp_path)
    core = MemoryCoreService(store)
    core.initialize()
    core.upsert_expression_pattern(
        character_name="高松灯",
        scene="chat",
        situation="用户确认结果",
        style="owner 专属表达",
        examples=["owner"],
        source="learned_v2",
        quality_score=9.0,
        person_id="owner",
    )
    core.upsert_expression_pattern(
        character_name="高松灯",
        scene="chat",
        situation="用户确认结果",
        style="QQ 用户自己的表达",
        examples=["qq"],
        source="learned_v2",
        quality_score=9.0,
        person_id="qq:42",
    )

    hints = core.select_expressions(
        user_text="结果确认好了",
        character_name="高松灯",
        person_id="qq:42",
    )

    assert any("QQ 用户自己的表达" in hint for hint in hints)
    assert all("owner 专属表达" not in hint for hint in hints)


def test_expression_feedback_updates_quality(tmp_path):
    store = _store(tmp_path)
    selected_id = ""

    def fake_llm(messages, *, task_type="summary", caller=""):
        if caller == "expression_selector":
            return '{"selected_ids":["' + selected_id + '"]}'
        return ""

    core = MemoryCoreService(store, llm_call=fake_llm)
    core.initialize()
    selected_id = core.upsert_expression_pattern(
        character_name="高松灯",
        scene="chat",
        situation="用户确认结果",
        style="简短回应",
        examples=["嗯"],
        source="test",
        quality_score=8.0,
    )
    hints = core.select_expressions(
        user_text="这样可以了",
        character_name="高松灯",
        session_id="owner_shared",
    )
    assert hints
    core.record_reply(
        session_id="owner_shared",
        character_name="高松灯",
        text="嗯",
    )
    core.observe_followup(session_id="owner_shared", text="不对，理解错了")

    row = store._connect().execute(
        "SELECT quality_score,negative_count FROM expression_patterns WHERE id=?",
        (selected_id,),
    ).fetchone()
    assert int(row["negative_count"]) == 1
    assert float(row["quality_score"]) < 8.0
