from modules import llm_probe


class FakeResp:
    def __init__(self, status_code):
        self.status_code = status_code


def test_probe_missing_url():
    result = llm_probe.probe_model_endpoint({})
    assert result["ok"] is False
    assert "URL" in result["message"]


def test_probe_chat_200(monkeypatch):
    monkeypatch.setattr(
        llm_probe.requests,
        "post",
        lambda *a, **k: FakeResp(200),
    )
    result = llm_probe.probe_model_endpoint(
        {
            "base_url": "https://api.example.com/v1",
            "api_key": "sk-test",
            "model": "demo",
        }
    )
    assert result["ok"] is True
    assert result["status"] == 200
    assert result["latency_ms"] >= 0


def test_probe_401_maps_message(monkeypatch):
    monkeypatch.setattr(
        llm_probe.requests,
        "post",
        lambda *a, **k: FakeResp(401),
    )
    result = llm_probe.probe_model_endpoint(
        {
            "base_url": "https://api.example.com/v1",
            "api_key": "bad",
            "model": "demo",
        }
    )
    assert result["ok"] is False
    assert result["status"] == 401
    assert "Key" in result["message"]


def test_probe_timeout(monkeypatch):
    def boom(*_a, **_k):
        raise llm_probe.requests.Timeout()

    monkeypatch.setattr(llm_probe.requests, "post", boom)
    result = llm_probe.probe_model_endpoint(
        {
            "base_url": "https://api.example.com/v1",
            "api_key": "sk",
            "model": "demo",
        }
    )
    assert result["ok"] is False
    assert "超时" in result["message"]
