from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from modules.memory_core import MemoryCoreService
from modules.memory_core.lifecycle import (
    MemoryLifecycleConfig,
    apply_reinforcement,
    decide_user_task,
    retention_strength,
)
from modules.memory_core.repository import USER_TASK_LIFECYCLE_KEY
from modules.memory_sqlite import MemorySQLite


def _store(tmp_path: Path) -> MemorySQLite:
    return MemorySQLite(str(tmp_path / "memory.sqlite"))


def _core(tmp_path: Path) -> MemoryCoreService:
    core = MemoryCoreService(_store(tmp_path))
    core.initialize()
    return core


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


def _task(core: MemoryCoreService, content: str, **kwargs) -> str:
    payload = {
        "kind": "other",
        "key": "user_task",
        "content": content,
        "subject_id": "owner",
        "session_id": "desktop:test",
        "source_type": "test",
        "source_id": content,
    }
    payload.update(kwargs)
    return core.upsert_memory_record(**payload)


def _age(core: MemoryCoreService, record_id: str, *, hours: float, anchor: float) -> None:
    del hours
    stamped = _iso(anchor)
    row = core.get_memory_record(record_id)
    metadata = dict(row.get("metadata") or {})
    life = dict(metadata.get("lifecycle") or {})
    if life:
        life["anchor_ts"] = anchor
        life["base_strength"] = 1.0
        life["frozen_at_ts"] = None
        life["state"] = "active"
        metadata["lifecycle"] = life
    conn = core.repository._connect()
    conn.execute(
        "UPDATE memory_records SET created_at=?, updated_at=?, last_confirmed_at=?, metadata_json=? WHERE id=?",
        (stamped, stamped, stamped, json.dumps(metadata, ensure_ascii=False), record_id),
    )
    conn.commit()


def test_retention_matches_maibot_half_life():
    assert retention_strength(1.0, 0, 24) == 1.0
    assert retention_strength(1.0, 24, 24) == 0.5
    assert retention_strength(1.0, 48, 24) == 0.25
    # 1.0 falls below 0.1 at 24 * log2(10) hours, about 3.3 days.
    assert retention_strength(1.0, 24 * 3.321928, 24) == pytest_approx(0.1)


def pytest_approx(value: float):
    from pytest import approx

    return approx(value, rel=1e-4)


def test_three_days_stays_active_and_four_and_a_half_archives():
    config = MemoryLifecycleConfig()
    anchor = 1_700_000_000.0
    still = decide_user_task(
        status="active",
        base_strength=1.0,
        anchor_ts=anchor,
        frozen_at_ts=None,
        valid_until_ts=None,
        protected_until_ts=None,
        manual_lock=False,
        now_ts=anchor + 72 * 3600,
        config=config,
    )
    assert still.status == "active"
    assert still.changed is False
    frozen = decide_user_task(
        status="active",
        base_strength=1.0,
        anchor_ts=anchor,
        frozen_at_ts=None,
        valid_until_ts=None,
        protected_until_ts=None,
        manual_lock=False,
        now_ts=anchor + 90 * 3600,
        config=config,
    )
    assert frozen.status == "frozen"
    archived = decide_user_task(
        status="active",
        base_strength=1.0,
        anchor_ts=anchor,
        frozen_at_ts=None,
        valid_until_ts=None,
        protected_until_ts=None,
        manual_lock=False,
        now_ts=anchor + 110 * 3600,
        config=config,
    )
    assert archived.status == "archived"
    assert archived.reason == "freeze_expired"


def test_explicit_end_skips_half_life_until_it_passes():
    config = MemoryLifecycleConfig()
    now = 1_800_000_000.0
    waiting = decide_user_task(
        status="active",
        base_strength=1.0,
        anchor_ts=now - 30 * 86400,
        frozen_at_ts=None,
        valid_until_ts=now + 3600,
        protected_until_ts=None,
        manual_lock=False,
        now_ts=now,
        config=config,
    )
    assert waiting.changed is False
    due = decide_user_task(
        status="active",
        base_strength=1.0,
        anchor_ts=now,
        frozen_at_ts=None,
        valid_until_ts=now - 1,
        protected_until_ts=None,
        manual_lock=False,
        now_ts=now,
        config=config,
    )
    assert due.status == "archived"
    assert due.reason == "valid_until"


def test_access_bump_does_not_reset_to_one_and_respects_hysteresis():
    config = MemoryLifecycleConfig()
    now = 1_800_000_000.0
    first = apply_reinforcement(
        mode="access",
        status="frozen",
        base_strength=0.09,
        anchor_ts=now,
        frozen_at_ts=now - 3600,
        last_access_ts=None,
        manual_lock=False,
        now_ts=now,
        config=config,
    )
    assert first is not None
    assert first.status == "frozen"
    assert first.base_strength == pytest_approx(0.14)
    cooled = apply_reinforcement(
        mode="access",
        status="frozen",
        base_strength=first.base_strength,
        anchor_ts=first.anchor_ts,
        frozen_at_ts=first.frozen_at_ts,
        last_access_ts=now,
        manual_lock=False,
        now_ts=now + 30 * 60,
        config=config,
    )
    assert cooled is None
    second = apply_reinforcement(
        mode="access",
        status="frozen",
        base_strength=first.base_strength,
        anchor_ts=first.anchor_ts,
        frozen_at_ts=first.frozen_at_ts,
        last_access_ts=now,
        manual_lock=False,
        now_ts=now + 61 * 60,
        config=config,
    )
    assert second is not None
    assert second.status == "active"
    assert second.base_strength < 0.2


def test_startup_archives_old_tasks_and_keeps_stable_facts(tmp_path):
    store = _store(tmp_path)
    core = MemoryCoreService(store)
    core.initialize()
    stale = _task(core, "把这个代码跑完再写一篇论文", session_id="s-stale", source_id="s-stale")
    fresh = _task(core, "明天把椅子搬回去", session_id="s-fresh", source_id="s-fresh")
    liked = core.upsert_memory_record(
        kind="fact",
        key="likes.music",
        content="喜欢迷星叫",
        subject_id="owner",
        session_id="",
        source_type="test",
        source_id="fact-1",
    )
    relation = core.upsert_memory_record(
        kind="relation",
        key="rel.group",
        content="箱宝同好会是小祥的群",
        subject_id="owner",
        session_id="",
        source_type="test",
        source_id="rel-1",
    )
    locked = _task(
        core,
        "记住这件一直要做的事",
        manual_lock=True,
        source_id="locked",
        session_id="s-locked",
    )
    anchor = datetime.now(timezone.utc).timestamp() - 120 * 3600
    for record_id in (stale, fresh, liked, relation, locked):
        _age(core, record_id, hours=120, anchor=anchor)
    # A later confirmation restarts only this task. Sibling tasks share key=user_task,
    # so this stays a direct clock edit instead of another upsert.
    fresh_row = core.get_memory_record(fresh)
    fresh_meta = dict(fresh_row.get("metadata") or {})
    fresh_life = dict(fresh_meta.get("lifecycle") or {})
    confirmed = datetime.now(timezone.utc).timestamp()
    fresh_life.update(
        {
            "state": "active",
            "base_strength": 1.0,
            "anchor_ts": confirmed,
            "frozen_at_ts": None,
        }
    )
    fresh_meta["lifecycle"] = fresh_life
    conn = core.repository._connect()
    conn.execute(
        "UPDATE memory_records SET last_confirmed_at=?, metadata_json=? WHERE id=?",
        (_iso(confirmed), json.dumps(fresh_meta, ensure_ascii=False), fresh),
    )
    conn.commit()
    core.repository.set_meta(
        USER_TASK_LIFECYCLE_KEY,
        _iso(datetime.now(timezone.utc).timestamp() - 7200),
    )
    again = MemoryCoreService(store)
    again.initialize()
    rows = {row["id"]: row for row in again.list_memory_records(status="", limit=20)}
    assert rows[stale]["status"] == "archived"
    assert rows[fresh]["status"] == "active"
    assert rows[liked]["status"] == "active"
    assert rows[relation]["status"] == "active"
    assert rows[locked]["status"] == "active"
    visible = again.list_current_memory_records(limit=20)
    assert stale not in {row["id"] for row in visible}
    assert fresh in {row["id"] for row in visible}


def test_repeat_mention_revives_archived_task(tmp_path):
    core = _core(tmp_path)
    record_id = _task(core, "下周我打算请一天假早点走")
    anchor = datetime.now(timezone.utc).timestamp() - 120 * 3600
    _age(core, record_id, hours=120, anchor=anchor)
    changed = core.repository.apply_user_task_lifecycle(
        now=datetime.now(timezone.utc).timestamp(),
        force=True,
    )
    assert changed == 1
    revived = _task(core, "下周我打算请一天假早点走", source_id="again")
    assert revived == record_id
    row = core.get_memory_record(record_id)
    assert row["status"] == "active"
    assert row["metadata"]["lifecycle"]["base_strength"] == 1.0
    same = core.list_memory_records(status="", limit=10)
    assert len([item for item in same if item["key"] == "user_task"]) == 1


def test_interval_skips_a_second_pass_until_it_is_due(tmp_path):
    core = _core(tmp_path)
    record_id = _task(core, "接着转头问骰子得了")
    now = datetime.now(timezone.utc).timestamp()
    _age(core, record_id, hours=120, anchor=now - 120 * 3600)
    assert core.repository.apply_user_task_lifecycle(now=now, force=True) == 1
    another = _task(core, "给你做一个网站")
    _age(core, another, hours=120, anchor=now - 120 * 3600)
    assert core.repository.apply_user_task_lifecycle(now=now + 600) == 0
    assert core.get_memory_record(another)["status"] == "active"
    assert core.repository.apply_user_task_lifecycle(now=now + 3700) == 1
    assert core.get_memory_record(another)["status"] == "archived"


def test_explicit_reinforcement_revives_and_weaken_freezes(tmp_path):
    core = _core(tmp_path)
    record_id = _task(core, "最近有点半月板损伤呢可能需要休养")
    now = datetime.now(timezone.utc).timestamp()
    _age(core, record_id, hours=90, anchor=now - 90 * 3600)
    assert core.repository.apply_user_task_lifecycle(now=now, force=True) == 1
    assert core.get_memory_record(record_id)["status"] == "frozen"
    assert core.repository.reinforce_user_task(record_id, mode="explicit", now=now) == "active"
    weakened = core.repository.reinforce_user_task(
        record_id,
        mode="weaken",
        now=now + 1,
    )
    assert weakened == "active"
    assert core.get_memory_record(record_id)["metadata"]["lifecycle"]["base_strength"] < 0.6
    # Drop it under the freeze line without waiting another half-life.
    core.repository.reinforce_user_task(record_id, mode="weaken", now=now + 2)
    assert core.repository.reinforce_user_task(record_id, mode="weaken", now=now + 3) == "frozen"


def test_locking_an_archived_task_puts_it_back_on_the_wall(tmp_path):
    core = _core(tmp_path)
    record_id = _task(core, "23号上完班我舅舅准备回东北了")
    now = datetime.now(timezone.utc).timestamp()
    _age(core, record_id, hours=120, anchor=now - 120 * 3600)
    assert core.repository.apply_user_task_lifecycle(now=now, force=True) == 1
    assert core.update_memory_record(record_id, manual_lock=True) is True
    row = core.get_memory_record(record_id)
    assert row["status"] == "active"
    assert row["manual_lock"] is True
    assert record_id in {
        item["id"] for item in core.list_current_memory_records(limit=10)
    }
