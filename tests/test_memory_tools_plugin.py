from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from modules.memory_core import MemoryCoreService
from modules.memory_sqlite import MemorySQLite
from modules.tool_router import ToolRouter
from plugins.memory_tools.plugin import Plugin


def test_memory_capabilities_use_shared_intent_rules():
    plugin = Plugin()
    capabilities = {item.id: item for item in plugin.get_capabilities()}

    assert capabilities["memory.query"].match("还记得我上次说了什么吗", {}) is not None
    assert capabilities["memory.person_profile"].match("你对我的印象是什么", {}) is not None
    assert capabilities["activity.query"].match("我昨天学习了多久", {}) is not None
    assert capabilities["memory.query"].match("今天天气怎么样", {}) is None
    assert MemoryCoreService.detect_intent("我一般什么时候开会") == "episode"
    assert MemoryCoreService.detect_intent("我平常都是周几开会") == "episode"
    assert MemoryCoreService.detect_intent("我通常哪天开会") == "episode"
    assert MemoryCoreService.detect_intent("上次开会多久") == "episode"
    assert MemoryCoreService.detect_intent("今天我学习了吗") == "activity"
    assert MemoryCoreService.detect_intent("我昨天学习了多久") == "activity"


def test_memory_plugin_queries_current_person_scope(tmp_path):
    store = MemorySQLite(str(tmp_path / "memory.sqlite"))
    core = MemoryCoreService(store)
    core.initialize()
    core.upsert_memory_record(
        kind="preference",
        key="reply_style",
        subject_id="qq:42",
        content="喜欢简短回复",
        source_type="test",
        source_id="profile",
    )
    brain = SimpleNamespace(memory_core=core, short_term_memory=[])

    result = asyncio.run(
        Plugin().run(
            "profile ||| 你对我的印象是什么",
            {
                "brain": brain,
                "memory_person_id": "qq:42",
                "memory_session_id": "private:42",
            },
        )
    )

    assert "简短回复" in result


def test_explicit_memory_query_skips_llm_and_prefers_answer_evidence(tmp_path):
    store = MemorySQLite(str(tmp_path / "memory.sqlite"))
    llm_calls = []

    def fake_llm(messages, *, task_type="summary", caller=""):
        llm_calls.append(caller)
        return ""

    core = MemoryCoreService(store, llm_call=fake_llm)
    core.initialize()
    question_id = core.upsert_memory_record(
        kind="other",
        key="user_task",
        content="还记得我上次开会开了多久吗",
        subject_id="owner",
        session_id="owner_shared",
        source_type="test",
        source_id="old-question",
    )
    core.upsert_memory_record(
        kind="episode",
        content="上次开会持续了三个小时四十七分钟",
        subject_id="owner",
        session_id="owner_shared",
        source_type="test",
        source_id="meeting-answer",
    )
    core.vector_search = lambda *args, **kwargs: [
        {"id": question_id, "vector_score": 1.0}
    ]
    brain = SimpleNamespace(memory_core=core, short_term_memory=[])

    result = asyncio.run(
        Plugin().run(
            "还记得我上次开会开了多久吗",
            {
                "brain": brain,
                "memory_person_id": "owner",
                "memory_session_id": "owner_shared",
            },
        )
    )

    assert "三个小时四十七分钟" in result
    assert "还记得我上次开会" not in result
    assert llm_calls == []


def test_tool_router_uses_memory_capability_for_explicit_recall():
    route = ToolRouter(
        react_map={},
        direct_map={},
        delegate_map={"memory_tools": Plugin()},
    ).route("还记得我上次说了什么吗")

    assert route.need_tools is True
    assert route.tool_triggers == ["memory_tools"]
    assert route.reason == "capability:memory.query"


def test_tool_router_does_not_treat_weather_as_memory():
    route = ToolRouter(
        react_map={},
        direct_map={},
        delegate_map={"memory_tools": Plugin()},
    ).route("上海今天天气怎么样")

    assert route.tool_triggers == []


def test_memory_tools_remote_access_is_owner_only():
    with open("plugins/memory_tools/config.json", "r", encoding="utf-8") as handle:
        config = json.load(handle)

    assert config["access_control"]["allow_qq_owner"] is True
    assert config["access_control"]["allow_qq_others"] is False
