from __future__ import annotations

from modules import ollama_service


def test_host_port_normalizes_bind_all_and_env_style(monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "0.0.0.0:11434")
    host, port = ollama_service._host_port_from_settings({})
    assert host == "127.0.0.1"
    assert port == 11434


def test_status_reports_not_running_when_port_closed(monkeypatch):
    monkeypatch.setattr(ollama_service, "is_ollama_running", lambda *a, **k: False)
    monkeypatch.setattr(ollama_service, "resolve_ollama_executable", lambda: "C:/fake/ollama.exe")
    status = ollama_service.ollama_status({"ollama_autostart_enabled": True})
    assert status["ok"] is True
    assert status["enabled"] is True
    assert status["running"] is False


def test_ensure_skips_when_disabled(monkeypatch):
    monkeypatch.setattr(ollama_service, "is_ollama_running", lambda *a, **k: False)
    called = {"start": False}

    def _start(**kwargs):
        called["start"] = True
        return {"ok": True, "running": True, "started": True}

    monkeypatch.setattr(ollama_service, "start_ollama_service", _start)
    result = ollama_service.ensure_ollama_service({"ollama_autostart_enabled": False})
    assert result["running"] is False
    assert called["start"] is False


def test_ensure_starts_when_enabled(monkeypatch):
    monkeypatch.setattr(ollama_service, "is_ollama_running", lambda *a, **k: False)

    def _start(**kwargs):
        return {
            "ok": True,
            "started": True,
            "running": True,
            "host": "127.0.0.1",
            "port": 11434,
            "message": "已启动 Ollama 服务",
        }

    monkeypatch.setattr(ollama_service, "start_ollama_service", _start)
    result = ollama_service.ensure_ollama_service({"ollama_autostart_enabled": True})
    assert result["ok"] is True
    assert result["running"] is True
    assert result["started"] is True
