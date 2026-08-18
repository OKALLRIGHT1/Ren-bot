from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from modules.memory_core import MemoryCoreService
from modules.memory_core.repository import is_current
from modules.memory_sqlite import MemorySQLite
from services.gui_api.memory_service import MemoryGuiService
from services.memory_writeback import MemoryWritebackService, WritebackJob


def _store(tmp_path: Path) -> MemorySQLite:
    return MemorySQLite(str(tmp_path / "memory.sqlite"))


def _core(tmp_path: Path) -> MemoryCoreService:
    core = MemoryCoreService(_store(tmp_path))
    core.initialize()
    return core


def test_startup_unique_indexes_ignore_duplicate_episodes(tmp_path):
    core = _core(tmp_path)
    first = core.upsert_memory_record(
        kind="episode",
        key="2026-03-08 日记",
        content="同一天日记副本 a",
        subject_id="",
        source_type="diary",
        source_id="diary-a",
    )
    second = core.upsert_memory_record(
        kind="episode",
        key="2026-03-08 日记",
        content="同一天日记副本 b",
        subject_id="",
        source_type="diary",
        source_id="diary-b",
    )
    assert first and second and first != second
    again = MemoryCoreService(_store(tmp_path))
    result = again.initialize()
    assert result["schema_version"] == 1
    rows = again.list_memory_records(kinds=("episode",), status="active", limit=20)
    assert {row["id"] for row in rows} == {first, second}


def test_startup_archives_question_user_tasks_once(tmp_path):
    core = _core(tmp_path)
    question = core.upsert_memory_record(
        kind="other",
        key="user_task",
        content="你还记得我上次开会说了什么吗",
        subject_id="owner",
        session_id="desktop:q",
        source_type="test",
        source_id="q1",
    )
    real_task = core.upsert_memory_record(
        kind="other",
        key="user_task",
        content="记得帮我买咖啡",
        subject_id="owner",
        session_id="desktop:t",
        source_type="test",
        source_id="t1",
    )
    again = MemoryCoreService(_store(tmp_path))
    first = again.initialize()
    rows = again.list_memory_records(kinds=("other",), status="active", limit=20)
    assert {row["id"] for row in rows} == {real_task}
    archived = again.list_memory_records(kinds=("other",), status="archived", limit=20)
    assert {row["id"] for row in archived} == {question}
    assert first["repaired"] >= 1
    second = again.initialize()
    assert second["repaired"] == 0
    still_active = again.list_memory_records(kinds=("other",), status="active", limit=20)
    assert {row["id"] for row in still_active} == {real_task}
    still_archived = again.list_memory_records(kinds=("other",), status="archived", limit=20)
    assert {row["id"] for row in still_archived} == {question}


def test_is_current_treats_null_bounds_as_open():
    assert is_current({"status": "active", "valid_from": None, "valid_until": None})
    assert not is_current({"status": "superseded", "valid_from": None, "valid_until": None})


def test_is_current_respects_valid_until_and_future_from():
    now = datetime(2026, 8, 13, tzinfo=timezone.utc)
    past = (now - timedelta(days=1)).isoformat()
    future = (now + timedelta(days=1)).isoformat()
    assert not is_current(
        {"status": "active", "valid_from": None, "valid_until": past},
        now,
    )
    assert not is_current(
        {"status": "active", "valid_from": future, "valid_until": None},
        now,
    )


def test_persona_writeback_uses_empty_session_and_merges_channels(tmp_path):
    core = _core(tmp_path)
    first = core.upsert_memory_record(
        kind="fact",
        key="habit.meeting_weekday",
        content="用户固定周四开会",
        subject_id="owner",
        session_id="desktop:1",
        source_type="test",
        source_id="thu-desktop",
    )
    second = core.upsert_memory_record(
        kind="fact",
        key="habit.meeting_weekday",
        content="用户固定周四开会",
        subject_id="owner",
        session_id="qq:private:1",
        source_type="test",
        source_id="thu-qq",
    )
    rows = core.list_memory_records(subject_id="owner", status="active", limit=20)
    habits = [row for row in rows if row["key"] == "habit.meeting_weekday"]
    assert len(habits) == 1
    assert habits[0]["session_id"] == ""
    assert first
    assert second in {first, habits[0]["id"]}


def test_correction_leaves_only_current_fact_on_all_read_paths(tmp_path):
    core = _core(tmp_path)
    old_id = core.upsert_memory_record(
        kind="fact",
        key="habit.meeting_weekday",
        content="用户固定周四开会",
        subject_id="owner",
        source_type="test",
        source_id="thu",
        importance=0.9,
    )
    new_id = core.upsert_memory_record(
        kind="fact",
        key="habit.meeting_weekday",
        content="用户固定周三开会",
        subject_id="owner",
        source_type="test",
        source_id="wed",
        importance=0.95,
        supersede_keys=("habit.meeting_weekday",),
    )
    old = core.get_memory_record(old_id)
    new = core.get_memory_record(new_id)
    assert old is not None and old["status"] == "superseded"
    assert old.get("valid_until")
    assert new is not None and new["status"] == "active"
    assert new["session_id"] == ""

    profile = core.get_person_profile("owner")
    assert "周三" in profile.text
    assert "周四" not in profile.text

    character = core.get_character_profile("missing")
    assert character.text == ""

    recall = core.build_reply_context(
        "你还开会吗",
        session_id="desktop:1",
        person_id="owner",
        recent_messages=[],
        use_llm=False,
    )
    assert "周三" in (recall.memory_text + recall.profile_text)
    assert "周四" not in recall.memory_text

    current = core.list_current_memory_records(subject_id="owner", limit=50)
    assert [row["id"] for row in current if row["key"] == "habit.meeting_weekday"] == [new_id]


def test_expired_active_stays_in_list_records_but_not_current(tmp_path):
    core = _core(tmp_path)
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    record_id = core.upsert_memory_record(
        kind="fact",
        key="status.recent",
        content="这周很忙",
        subject_id="owner",
        source_type="test",
        source_id="busy",
        valid_until=past,
    )
    listed = core.list_memory_records(subject_id="owner", status="active", limit=20)
    assert any(row["id"] == record_id for row in listed)
    current = core.list_current_memory_records(subject_id="owner", limit=20)
    assert all(row["id"] != record_id for row in current)
    assert "很忙" not in core.get_person_profile("owner").text


def test_future_valid_from_does_not_create_or_supersede(tmp_path):
    core = _core(tmp_path)
    current_id = core.upsert_memory_record(
        kind="fact",
        key="habit.meeting_weekday",
        content="用户固定周三开会",
        subject_id="owner",
        source_type="test",
        source_id="now-habit",
    )
    future = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    created = core.upsert_memory_record(
        kind="fact",
        key="habit.meeting_weekday",
        content="下周一开始改远程",
        subject_id="owner",
        source_type="test",
        source_id="future-habit",
        valid_from=future,
    )
    assert created in {"", current_id}
    current = core.get_memory_record(current_id)
    assert current is not None
    assert current["status"] == "active"
    assert "周三" in current["content"]
    assert len(core.list_current_memory_records(subject_id="owner", kinds=("fact",), limit=20)) == 1


def test_source_id_payload_change_replaces_instead_of_short_circuit(tmp_path):
    core = _core(tmp_path)
    first = core.upsert_memory_record(
        kind="fact",
        key="habit.meeting_weekday",
        content="用户固定周四开会",
        subject_id="owner",
        source_type="person_fact_writeback",
        source_id="person_fact:owner:same",
    )
    second = core.upsert_memory_record(
        kind="fact",
        key="habit.meeting_weekday",
        content="用户固定周三开会",
        subject_id="owner",
        source_type="person_fact_writeback",
        source_id="person_fact:owner:same",
    )
    assert second
    assert second != first
    old = core.get_memory_record(first)
    new = core.get_memory_record(second)
    assert old is not None and old["status"] == "superseded"
    assert new is not None and "周三" in new["content"]


def test_writeback_extract_does_not_apply_valid_days(tmp_path):
    def fake_llm(messages, *_args, **_kwargs):
        return (
            '{"items":[{"kind":"fact","key":"habit.meeting_weekday",'
            '"content":"用户固定周四开会","confidence":0.95,'
            '"valid_days":90,"evidence_ids":["u1"]}]}'
        )

    core = MemoryCoreService(
        _store(tmp_path),
        llm_call=fake_llm,
        settings={"memory_writeback_inline": True, "memory_writeback_session_cooldown_sec": 0},
    )
    core.initialize()
    service = core.get_writeback_service()
    service.process_job(
        WritebackJob(
            session_id="desktop:1",
            person_id="owner",
            trigger_role="user",
            trigger_text="我周四开会",
            reason="explicit_user",
            meta={"_recent_messages": [{"id": "u1", "role": "user", "content": "我周四开会"}]},
        )
    )
    rows = core.list_memory_records(subject_id="owner", status="active", limit=20)
    habit = next(row for row in rows if row["key"] == "habit.meeting_weekday")
    assert habit["valid_until"] in {None, ""}
    assert habit["session_id"] == ""


def test_gui_default_view_uses_current_not_expired_active(tmp_path):
    core = _core(tmp_path)
    past = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    core.upsert_memory_record(
        kind="preference",
        key="likes.food",
        content="喜欢抹茶",
        subject_id="owner",
        source_type="test",
        source_id="tea",
    )
    core.upsert_memory_record(
        kind="fact",
        key="status.recent",
        content="已经过期的忙碌",
        subject_id="owner",
        source_type="test",
        source_id="expired-busy",
        valid_until=past,
    )
    service = MemoryGuiService(memory_core=core, brain=None)
    listed = service.list_core_records(status="active")
    texts = [row["content"] for row in listed["data"]["records"]]
    assert any("抹茶" in text for text in texts)
    assert all("过期的忙碌" not in text for text in texts)
