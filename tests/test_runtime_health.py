from concurrent.futures import ThreadPoolExecutor

from services.runtime_health import RuntimeHealthCenter


def test_snapshot_sanitizes_details_and_marks_stale():
    health = RuntimeHealthCenter(clock=lambda: 100.0)
    health.report(
        "live2d_ws",
        "healthy",
        "已连接",
        details={
            "host": "127.0.0.1:10086",
            "api_key": "secret-value",
            "nested": {"authorization": "Bearer secret", "attempts": 2},
        },
        stale_after_seconds=10,
        updated_at=80.0,
    )

    snapshot = health.snapshot()

    component = snapshot["components"]["live2d_ws"]
    assert component["details"] == {
        "host": "127.0.0.1:10086",
        "api_key": "[REDACTED]",
        "nested": {"authorization": "[REDACTED]", "attempts": 2},
    }
    assert component["stale"] is True
    assert component["effective_state"] == "degraded"
    assert snapshot["overall"] == "degraded"


def test_report_clear_and_snapshot_are_thread_safe():
    health = RuntimeHealthCenter(clock=lambda: 200.0)

    def write(index: int):
        health.report(
            f"model:model-{index}",
            "cooldown" if index % 2 else "healthy",
            "模型状态",
            details={"attempt": index},
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(write, range(40)))

    snapshot = health.snapshot()
    assert len(snapshot["components"]) == 40
    assert snapshot["overall"] == "degraded"

    health.clear("model:model-1")
    assert "model:model-1" not in health.snapshot()["components"]


def test_offline_component_makes_overall_offline_but_disabled_does_not():
    health = RuntimeHealthCenter(clock=lambda: 300.0)
    health.report("asr", "disabled", "未启用")
    assert health.snapshot()["overall"] == "healthy"

    health.report("rust_activity", "offline", "等待活动源")
    assert health.snapshot()["overall"] == "offline"
