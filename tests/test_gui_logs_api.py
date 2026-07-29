from __future__ import annotations

from pathlib import Path

from services.gui_api.logs_service import LogsGuiService


def test_list_and_tail_logs(tmp_path: Path):
    console = tmp_path / "console.log"
    agent = tmp_path / "agent.log"
    console.write_text("line1\nline2\nline3\n", encoding="utf-8")
    agent.write_text("agent-a\nagent-b\n", encoding="utf-8")
    service = LogsGuiService(log_dir=tmp_path)
    listed = service.list_logs()
    assert listed["ok"] is True
    names = {item["name"] for item in listed["data"]["logs"]}
    assert "console.log" in names
    assert "agent.log" in names
    tail = service.tail("console.log", max_bytes=1000)
    assert tail["ok"] is True
    assert "line3" in tail["data"]["text"]
    assert tail["data"]["truncated"] is False


def test_rejects_path_escape(tmp_path: Path):
    service = LogsGuiService(log_dir=tmp_path)
    result = service.tail("../secret.log")
    assert result["ok"] is False
