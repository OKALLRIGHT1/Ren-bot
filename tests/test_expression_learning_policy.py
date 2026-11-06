import json

from modules.memory_core.expression_learning_policy import (
    expression_source_allowed,
    normalize_expression_learning_policy,
    parse_id_list,
)
from modules.memory_core.service import MemoryCoreService
from modules.memory_sqlite import MemorySQLite


def test_parse_id_list_accepts_commas_and_extra_text():
    assert parse_id_list("123, 456\n群789") == ["123", "456", "789"]
    assert parse_id_list(["12", "12", "ab34"]) == ["12", "34"]


def test_source_rules():
    policy = normalize_expression_learning_policy(
        {
            "expression_learning_enabled": True,
            "expression_learning_local_enabled": False,
            "expression_learning_qq_accounts": "1001",
            "expression_learning_qq_groups": "2002",
            "expression_learning_intensity": "low",
        }
    )
    assert policy["batch"] == 20
    assert policy["enable_new"] is False
    assert expression_source_allowed({"source": "text_input"}, policy) is False
    policy["local"] = True
    assert expression_source_allowed({"source": "desktop"}, policy) is True
    assert (
        expression_source_allowed(
            {
                "source": "qq_gateway",
                "is_owner": True,
                "user_id": "1001",
                "message_type": "private",
            },
            policy,
        )
        is True
    )
    assert (
        expression_source_allowed(
            {
                "source": "qq_gateway",
                "is_owner": True,
                "user_id": "9999",
                "message_type": "private",
            },
            policy,
        )
        is False
    )
    assert (
        expression_source_allowed(
            {
                "source": "qq_gateway",
                "is_owner": False,
                "user_id": "1001",
                "group_id": "2002",
                "message_type": "group",
            },
            policy,
        )
        is False
    )
    assert (
        expression_source_allowed(
            {
                "source": "napcat_qq",
                "is_owner": True,
                "user_id": "1001",
                "group_id": "2002",
                "message_type": "group",
            },
            policy,
        )
        is True
    )


def test_learning_stays_off_until_enabled(tmp_path):
    calls = []

    def fake_llm(messages, *, task_type="summary", caller=""):
        calls.append(caller)
        return '{"items":[{"situation":"打招呼","style":"短句","examples":["早"],"quality":9,"source_ids":["1"]}]}'

    store = MemorySQLite(str(tmp_path / "memory.sqlite"))
    core = MemoryCoreService(store, llm_call=fake_llm, settings={"memory_writeback_enabled": False})
    core.initialize()
    core._expression_policy_loader = lambda: {
        "expression_learning_enabled": False,
        "expression_learning_local_enabled": True,
        "expression_learning_intensity": "high",
    }
    for _ in range(6):
        core.record_message(
            "user",
            "今天随便聊聊",
            session_id="local",
            character_name="铃",
            meta={"source": "text_input"},
        )
    assert "expression_learner_v2" not in calls


def test_low_intensity_local_learning_saves_disabled_pattern(tmp_path):
    def fake_llm(messages, *, task_type="summary", caller=""):
        if caller != "expression_learner_v2":
            return '{"items":[]}'
        ids = []
        for line in messages[-1]["content"].splitlines():
            if line.startswith("id="):
                ids.append(line.split()[0].split("=", 1)[1])
        source_id = ids[0] if ids else "1"
        return json.dumps(
            {
                "items": [
                    {
                        "situation": "打招呼",
                        "style": "短句",
                        "examples": ["早呀"],
                        "quality": 9,
                        "source_ids": [source_id],
                    }
                ]
            },
            ensure_ascii=False,
        )

    store = MemorySQLite(str(tmp_path / "memory.sqlite"))
    core = MemoryCoreService(
        store,
        llm_call=fake_llm,
        settings={
            "memory_writeback_enabled": False,
            "memory_core_profile_learning_enabled": False,
            "memory_core_expression_learning_enabled": False,
        },
    )
    core.initialize()
    core._expression_policy_loader = lambda: {
        "expression_learning_enabled": True,
        "expression_learning_local_enabled": True,
        "expression_learning_intensity": "low",
    }
    # Low intensity waits for 20 messages. Use the preset batch by sending 20.
    for index in range(20):
        core.record_message(
            "user" if index % 2 == 0 else "assistant",
            f"第{index}句随便说",
            session_id="local",
            character_name="铃",
            meta={"source": "text_input"},
        )
    rows = store.list_expression_patterns(limit=20)
    assert len(rows) == 1
    assert rows[0]["enabled"] is False
    assert rows[0]["style"] == "短句"


def test_other_group_speaker_gets_own_profile_not_owner(tmp_path):
    def fake_llm(messages, *, task_type="summary", caller=""):
        if caller != "memory_writeback_extract":
            return '{"items":[]}'
        text = messages[-1]["content"]
        evidence_id = "1"
        for line in text.splitlines():
            if line.startswith("id="):
                evidence_id = line.split()[0].split("=", 1)[1]
                break
        return json.dumps(
            {
                "items": [
                    {
                        "kind": "preference",
                        "key": "likes.cat",
                        "content": "喜欢猫咪",
                        "confidence": 0.9,
                        "evidence_ids": [evidence_id],
                    }
                ]
            },
            ensure_ascii=False,
        )

    store = MemorySQLite(str(tmp_path / "memory.sqlite"))
    core = MemoryCoreService(
        store,
        llm_call=fake_llm,
        settings={
            "memory_writeback_enabled": True,
            "memory_writeback_inline": True,
            "memory_writeback_session_cooldown_sec": 0,
            "memory_core_learning_batch_messages": 2,
        },
    )
    core.initialize()
    core._expression_policy_loader = lambda: {"expression_learning_enabled": True}
    core.record_message(
        "user",
        "我喜欢猫",
        session_id="group:2002",
        person_id="qq:3003",
        character_name="铃",
        meta={
            "source": "qq_gateway",
            "is_owner": False,
            "user_id": "3003",
            "sender_name": "小明",
            "group_id": "2002",
            "message_type": "group",
        },
    )

    people = {item["person_id"]: item for item in core.list_persons()}
    assert people["qq:3003"]["display_name"] == "小明"
    assert people["qq:3003"]["relationship"] == "群友"
    speaker_rows = core.repository.list_current_records(subject_id="qq:3003", limit=20)
    owner_rows = core.repository.list_current_records(subject_id="owner", limit=20)
    assert any(row.get("content") == "喜欢猫咪" for row in speaker_rows)
    assert all(row.get("content") != "喜欢猫咪" for row in owner_rows)
    assert store.list_expression_patterns(limit=20) == []


def test_qq_group_member_is_not_learned(tmp_path):
    calls = []

    def fake_llm(messages, *, task_type="summary", caller=""):
        calls.append(caller)
        return '{"items":[]}'

    store = MemorySQLite(str(tmp_path / "memory.sqlite"))
    core = MemoryCoreService(store, llm_call=fake_llm, settings={"memory_writeback_enabled": False})
    core.initialize()
    core._expression_policy_loader = lambda: {
        "expression_learning_enabled": True,
        "expression_learning_local_enabled": False,
        "expression_learning_intensity": "high",
        "expression_learning_qq_groups": ["2002"],
    }
    for _ in range(6):
        core.record_message(
            "user",
            "群里说一句",
            session_id="group:2002",
            character_name="铃",
            meta={
                "source": "qq_gateway",
                "is_owner": False,
                "user_id": "3003",
                "group_id": "2002",
                "message_type": "group",
            },
        )
    assert "expression_learner_v2" not in calls
