from __future__ import annotations

import json
import time
from pathlib import Path

from modules.memory_core import MemoryCoreService
from modules.memory_sqlite import MemorySQLite
from services.memory_writeback import MemoryWritebackService, WritebackJob


def _store(tmp_path: Path) -> MemorySQLite:
    return MemorySQLite(str(tmp_path / "memory.sqlite"))


def _core(tmp_path: Path, llm_call=None, **settings) -> MemoryCoreService:
    base = {
        "memory_core_enabled": True,
        "memory_writeback_enabled": True,
        "memory_writeback_inline": True,
        "memory_writeback_chat_summary_enabled": True,
        "memory_writeback_person_fact_enabled": True,
        "memory_writeback_summary_message_threshold": 6,
        "memory_writeback_session_cooldown_sec": 0,
        "memory_writeback_min_confidence": 0.7,
        "memory_core_profile_learning_enabled": False,
        "memory_core_expression_learning_enabled": False,
    }
    base.update(settings)
    core = MemoryCoreService(_store(tmp_path), llm_call=llm_call, settings=base)
    core.initialize()
    return core


def test_writeback_extracts_thursday_correction_from_user_evidence(tmp_path):
    calls = []

    def fake_llm(messages, *, task_type="default", caller=""):
        calls.append({"task_type": task_type, "caller": caller})
        prompt = messages[-1]["content"]
        evidence_id = "10705"
        for line in prompt.splitlines():
            if "其实是周四" in line and line.startswith("id="):
                evidence_id = line.split("id=", 1)[1].split()[0]
                break
        return json.dumps(
            {
                "items": [
                    {
                        "kind": "fact",
                        "key": "habit.meeting_weekday",
                        "content": "用户固定周四开会",
                        "confidence": 0.95,
                        "valid_days": 0,
                        "evidence_ids": [evidence_id],
                        "is_correction": True,
                        "category_override": "habits",
                        "reason": "user corrected weekday",
                    }
                ]
            },
            ensure_ascii=False,
        )

    core = _core(tmp_path, llm_call=fake_llm)
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

    assert any(item["caller"] == "memory_writeback_extract" for item in calls)
    rows = [
        row
        for row in core.list_memory_records(subject_id="owner", limit=50)
        if "周四" in str(row.get("content") or "")
    ]
    assert rows
    assert any(str(row.get("key") or "") == "habit.meeting_weekday" for row in rows)

    hit = core.build_reply_context(
        "我平常都是周几开会",
        session_id="owner_shared",
        person_id="owner",
        recent_messages=[],
        use_llm=False,
    )
    assert "周四" in hit.memory_text


def test_writeback_empty_items_means_do_not_store(tmp_path):
    def fake_llm(messages, *, task_type="default", caller=""):
        return '{"items":[]}'

    core = _core(tmp_path, llm_call=fake_llm)
    core.record_message(
        "user",
        "今天天气真好",
        session_id="s1",
        person_id="owner",
        meta={"source": "text_input"},
    )
    core.record_message(
        "assistant",
        "是啊，出门走走吧",
        session_id="s1",
        person_id="owner",
        meta={"source": "text_input"},
    )
    facts = [
        row
        for row in core.list_memory_records(subject_id="owner", limit=50)
        if str(row.get("source_type") or "") == "person_fact_writeback"
    ]
    assert facts == []


def test_writeback_rejects_facts_without_user_evidence_ids(tmp_path):
    def fake_llm(messages, *, task_type="default", caller=""):
        return json.dumps(
            {
                "items": [
                    {
                        "kind": "fact",
                        "key": "habit.fake",
                        "content": "用户喜欢编造",
                        "confidence": 0.99,
                        "evidence_ids": ["not-in-context"],
                    }
                ]
            }
        )

    core = _core(tmp_path, llm_call=fake_llm)
    service = core.get_writeback_service()
    assert service is not None
    result = service.process_job(
        WritebackJob(
            session_id="s1",
            person_id="owner",
            trigger_role="assistant",
            trigger_text="好的我记下了",
            reason="assistant_reply",
            meta={
                "_recent_messages": [
                    {"id": "1", "role": "user", "content": "随便聊聊"},
                    {"id": "2", "role": "assistant", "content": "好的我记下了"},
                ]
            },
        )
    )
    assert result.facts_written == 0


def test_writeback_correction_supersedes_same_key(tmp_path):
    responses = iter(
        [
            json.dumps(
                {
                    "items": [
                        {
                            "kind": "fact",
                            "key": "habit.meeting_weekday",
                            "content": "用户周三开会",
                            "confidence": 0.9,
                            "evidence_ids": ["u1"],
                        }
                    ]
                }
            ),
            json.dumps(
                {
                    "items": [
                        {
                            "kind": "fact",
                            "key": "habit.meeting_weekday",
                            "content": "用户固定周四开会",
                            "confidence": 0.96,
                            "evidence_ids": ["u2"],
                            "is_correction": True,
                        }
                    ]
                }
            ),
        ]
    )

    def fake_llm(messages, *, task_type="default", caller=""):
        return next(responses)

    core = _core(tmp_path, llm_call=fake_llm)
    service = core.get_writeback_service()
    service.process_job(
        WritebackJob(
            session_id="s1",
            person_id="owner",
            trigger_role="user",
            trigger_text="我周三开会",
            reason="explicit_user",
            meta={
                "_recent_messages": [
                    {"id": "u1", "role": "user", "content": "我周三开会"},
                ]
            },
        )
    )
    service.process_job(
        WritebackJob(
            session_id="s1",
            person_id="owner",
            trigger_role="user",
            trigger_text="其实是周四哦",
            reason="explicit_user",
            meta={
                "_recent_messages": [
                    {"id": "u2", "role": "user", "content": "其实是周四哦"},
                ]
            },
        )
    )
    active = [
        row
        for row in core.list_memory_records(subject_id="owner", status="active", limit=20)
        if str(row.get("key") or "") == "habit.meeting_weekday"
    ]
    assert len(active) == 1
    assert "周四" in active[0]["content"]


def test_writeback_skips_untrusted_qq_non_owner(tmp_path):
    calls = []

    def fake_llm(messages, *, task_type="default", caller=""):
        calls.append(caller)
        return '{"items":[]}'

    core = _core(tmp_path, llm_call=fake_llm)
    core.record_message(
        "user",
        "我喜欢把回复写得很长",
        session_id="private:42",
        person_id="qq:42",
        meta={"source": "qq_gateway", "is_owner": False, "user_id": "42"},
    )
    assert "memory_writeback_extract" not in calls


def test_writeback_chat_summary_window(tmp_path):
    def fake_llm(messages, *, task_type="default", caller=""):
        if caller == "memory_writeback_summary":
            return "用户讨论了项目进度，并约定周四开会，其余为闲聊。"
        return '{"items":[]}'

    core = _core(
        tmp_path,
        llm_call=fake_llm,
        memory_writeback_summary_message_threshold=4,
        memory_writeback_person_fact_enabled=False,
    )
    for index in range(4):
        role = "user" if index % 2 == 0 else "assistant"
        core.record_message(
            role,
            f"消息内容编号 {index} 足够长一点",
            session_id="sum-session",
            person_id="owner",
            meta={"source": "text_input"},
        )
    summaries = [
        row
        for row in core.list_memory_records(subject_id="owner", kinds=("summary",), limit=20)
        if str(row.get("source_type") or "") == "chat_summary_writeback"
    ]
    assert summaries
    assert "周四" in summaries[0]["content"] or "开会" in summaries[0]["content"]


def test_has_writeback_signal_rules():
    assert MemoryWritebackService._has_writeback_signal("其实是周四哦")
    assert MemoryWritebackService._has_writeback_signal("我平常周四开会")
    assert MemoryWritebackService._has_writeback_signal("以后叫我 master")
    assert MemoryWritebackService._has_writeback_signal("帮我记一下我过敏芒果")
    assert not MemoryWritebackService._has_writeback_signal("我平常都是周几开会")
    assert not MemoryWritebackService._has_writeback_signal("哈哈")


def test_writeback_runs_when_profile_learning_disabled(tmp_path):
    """Regression: writeback must not depend on profile/expression learning flags."""

    def fake_llm(messages, *, task_type="default", caller=""):
        prompt = messages[-1]["content"]
        evidence_id = "1"
        for line in prompt.splitlines():
            if "其实是周四" in line and line.startswith("id="):
                evidence_id = line.split("id=", 1)[1].split()[0]
                break
        return json.dumps(
            {
                "items": [
                    {
                        "kind": "fact",
                        "key": "habit.meeting_weekday",
                        "content": "用户固定周四开会",
                        "confidence": 0.94,
                        "evidence_ids": [evidence_id],
                        "is_correction": True,
                        "category_override": "habits",
                    }
                ]
            },
            ensure_ascii=False,
        )

    core = _core(
        tmp_path,
        llm_call=fake_llm,
        memory_core_profile_learning_enabled=False,
        memory_core_expression_learning_enabled=False,
    )
    core.record_message(
        "user",
        "其实是周四哦",
        session_id="owner_shared",
        person_id="owner",
        meta={"source": "text_input"},
    )
    rows = [
        row
        for row in core.list_memory_records(subject_id="owner", limit=20)
        if str(row.get("key") or "") == "habit.meeting_weekday"
    ]
    assert rows
    assert "周四" in rows[0]["content"]


def test_writeback_supersede_keys_archives_old_key(tmp_path):
    responses = iter(
        [
            json.dumps(
                {
                    "items": [
                        {
                            "kind": "preference",
                            "key": "preferred_address",
                            "content": "用户希望被叫小G",
                            "confidence": 0.9,
                            "evidence_ids": ["u1"],
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "items": [
                        {
                            "kind": "preference",
                            "key": "preferred_name",
                            "content": "用户希望被叫 master",
                            "confidence": 0.95,
                            "evidence_ids": ["u2"],
                            "is_correction": True,
                            "supersede_keys": ["preferred_address"],
                        }
                    ]
                },
                ensure_ascii=False,
            ),
        ]
    )

    def fake_llm(messages, *, task_type="default", caller=""):
        return next(responses)

    core = _core(tmp_path, llm_call=fake_llm)
    service = core.get_writeback_service()
    service.process_job(
        WritebackJob(
            session_id="s1",
            person_id="owner",
            trigger_role="user",
            trigger_text="叫我小G",
            reason="explicit_user",
            meta={"_recent_messages": [{"id": "u1", "role": "user", "content": "叫我小G"}]},
        )
    )
    service.process_job(
        WritebackJob(
            session_id="s1",
            person_id="owner",
            trigger_role="user",
            trigger_text="以后叫我 master",
            reason="explicit_user",
            meta={
                "_recent_messages": [
                    {"id": "u2", "role": "user", "content": "以后叫我 master"}
                ]
            },
        )
    )
    old = [
        row
        for row in core.list_memory_records(
            subject_id="owner", status="superseded", limit=20
        )
        if str(row.get("key") or "") == "preferred_address"
    ]
    active = [
        row
        for row in core.list_memory_records(subject_id="owner", status="active", limit=20)
        if str(row.get("key") or "") == "preferred_name"
    ]
    assert old
    assert active
    assert "master" in active[0]["content"]


def test_writeback_async_queue_processes_and_flush(tmp_path):
    calls = []

    def fake_llm(messages, *, task_type="default", caller=""):
        calls.append(caller)
        prompt = messages[-1]["content"]
        evidence_id = "1"
        for line in prompt.splitlines():
            if "我喜欢抹茶" in line and line.startswith("id="):
                evidence_id = line.split("id=", 1)[1].split()[0]
                break
        return json.dumps(
            {
                "items": [
                    {
                        "kind": "preference",
                        "key": "likes.food",
                        "content": "用户喜欢抹茶",
                        "confidence": 0.93,
                        "evidence_ids": [evidence_id],
                        "category_override": "likes.food",
                    }
                ]
            },
            ensure_ascii=False,
        )

    core = _core(
        tmp_path,
        llm_call=fake_llm,
        memory_writeback_inline=False,
        memory_writeback_session_cooldown_sec=0,
    )
    core.record_message(
        "user",
        "我喜欢抹茶",
        session_id="async-s",
        person_id="owner",
        meta={"source": "text_input"},
    )
    service = core.get_writeback_service()
    assert service is not None
    assert service.flush(timeout=3.0)
    # give worker a moment to finish process_job after queue mark
    deadline = time.time() + 3.0
    rows = []
    while time.time() < deadline:
        rows = [
            row
            for row in core.list_memory_records(subject_id="owner", limit=20)
            if "抹茶" in str(row.get("content") or "")
        ]
        if rows:
            break
        time.sleep(0.05)
    core.stop_writeback(timeout=1.0)
    assert any(c == "memory_writeback_extract" for c in calls)
    assert rows


def test_writeback_rejects_assistant_only_evidence(tmp_path):
    def fake_llm(messages, *, task_type="default", caller=""):
        return json.dumps(
            {
                "items": [
                    {
                        "kind": "fact",
                        "key": "habit.fake",
                        "content": "用户周三开会",
                        "confidence": 0.99,
                        "evidence_ids": ["a1"],
                    }
                ]
            },
            ensure_ascii=False,
        )

    core = _core(tmp_path, llm_call=fake_llm)
    service = core.get_writeback_service()
    result = service.process_job(
        WritebackJob(
            session_id="s1",
            person_id="owner",
            trigger_role="assistant",
            trigger_text="好的我记下你周三开会",
            reason="assistant_reply",
            meta={
                "_recent_messages": [
                    {"id": "u1", "role": "user", "content": "今天天气不错"},
                    {
                        "id": "a1",
                        "role": "assistant",
                        "content": "好的我记下你周三开会",
                    },
                ]
            },
        )
    )
    assert result.facts_written == 0
    facts = [
        row
        for row in core.list_memory_records(subject_id="owner", limit=20)
        if str(row.get("source_type") or "") == "person_fact_writeback"
    ]
    assert facts == []
