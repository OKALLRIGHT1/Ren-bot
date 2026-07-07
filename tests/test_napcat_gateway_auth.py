from types import SimpleNamespace

from integrations.chat_gateway.server import NapCatWebhookServer


class DummyGateway:
    pass


def _server():
    return NapCatWebhookServer(
        gateway=DummyGateway(),
        loop=None,
        access_token="runtime-token",
    )


def _request(remote="", headers=None, query=None):
    return SimpleNamespace(
        remote=remote,
        headers=headers or {},
        query=query or {},
    )


def test_http_request_still_requires_token():
    server = _server()

    assert server._request_authorized(_request(remote="127.0.0.1")) is False


def test_ws_request_allows_loopback_without_token_for_legacy_napcat():
    server = _server()

    assert server._ws_request_authorized(_request(remote="127.0.0.1")) is True
    assert server._ws_request_authorized(_request(remote="::1")) is True


def test_ws_request_rejects_non_loopback_without_token():
    server = _server()

    assert server._ws_request_authorized(_request(remote="192.168.1.20")) is False


def test_ws_request_accepts_token_from_non_loopback():
    server = _server()

    assert (
        server._ws_request_authorized(
            _request(remote="192.168.1.20", headers={"Authorization": "Bearer runtime-token"})
        )
        is True
    )
