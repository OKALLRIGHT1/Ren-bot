from integrations.gui_ws import GuiWebSocketServer


class _Request:
    def __init__(self, *, token: str, path: str = "/gui"):
        self.headers = {"X-GUI-Token": token}
        self.path = path


class _ModernConnection:
    def __init__(self, *, token: str, path: str = "/gui"):
        self.request = _Request(token=token, path=path)


def test_extract_token_supports_modern_websockets_request_headers():
    server = GuiWebSocketServer(access_token="secret")
    connection = _ModernConnection(token="secret")

    assert server._extract_token(connection, "/gui") == "secret"


def test_connection_path_supports_modern_websockets_request_object():
    server = GuiWebSocketServer(access_token="secret")
    connection = _ModernConnection(token="secret", path="/other")

    assert server._connection_path(connection, None) == "/other"
