import json
import socket
from pathlib import Path

import pytest

from plugins.qq_watermark.plugin import (
    COMMAND,
    Plugin,
    _resolve_target_url,
    classify_natural,
    hint_text,
)
from plugins.qq_watermark.xdownload import (
    XDownloadError,
    interpret_parse_payload,
    normalize_base_url,
    user_message_for_error,
    validate_download_url,
    validate_source_url,
)


ROOT = Path(__file__).resolve().parents[1]


async def _instant_sleep(_delay):
    return None


def _public_dns(monkeypatch):
    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.1.1.1", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)


def test_normalize_base_url_strips_v1():
    assert normalize_base_url("https://dl.example.com") == "https://dl.example.com/"
    assert normalize_base_url("https://dl.example.com/v1/") == "https://dl.example.com/"


def test_source_url_allows_supported_public_host(monkeypatch):
    _public_dns(monkeypatch)
    assert validate_source_url("https://v.douyin.com/abc/").startswith("https://v.douyin.com/")
    assert validate_source_url("https://xhslink.cn/o/257vyAKztY4").startswith("https://xhslink.cn/")


def test_source_url_allows_clash_fake_ip_for_supported_host(monkeypatch):
    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.18.0.87", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    assert validate_source_url("https://xhslink.cn/o/1EOJHCXoIlX").startswith("https://xhslink.cn/")
    with pytest.raises(XDownloadError) as blocked:
        validate_source_url("https://example.com/watch")
    assert blocked.value.code == "blocked_host"


def test_source_url_rejects_private_and_unknown_host(monkeypatch):
    _public_dns(monkeypatch)
    with pytest.raises(XDownloadError) as private:
        validate_source_url("http://127.0.0.1/secret")
    assert private.value.code == "blocked_host"
    with pytest.raises(XDownloadError):
        validate_source_url("https://example.com/watch")
    with pytest.raises(XDownloadError):
        validate_source_url("file:///tmp/a.mp4")


def test_download_url_rejects_metadata_redirect_target(monkeypatch):
    _public_dns(monkeypatch)
    with pytest.raises(XDownloadError):
        validate_download_url(
            "http://169.254.169.254/latest/meta-data",
            base_url="https://dl.example.com/",
            source_url="https://v.douyin.com/abc/",
        )


def test_error_messages_hide_internals():
    text = user_message_for_error(XDownloadError("auth", "Bearer super-secret", status=401))
    assert "密钥" not in text or "联系管理员" in text
    assert "super-secret" not in text
    assert "超时" in user_message_for_error(XDownloadError("timeout", "timeout"))
    assert "连不上" in user_message_for_error(XDownloadError("network", "network"))
    assert "下载超时" in user_message_for_error(XDownloadError("download_timeout", "timeout"))
    assert "媒体下载失败" in user_message_for_error(XDownloadError("download_network", "network"))
    assert "平台解析失败" in user_message_for_error(XDownloadError("upstream", "stack", status=502))


@pytest.mark.asyncio
async def test_transient_retry_recovers_then_gives_up(monkeypatch):
    from plugins.qq_watermark import xdownload

    monkeypatch.setattr(xdownload.asyncio, "sleep", _instant_sleep)
    hits = {"n": 0}

    async def flaky():
        hits["n"] += 1
        if hits["n"] < 2:
            raise XDownloadError("download_network", "ClientConnectorError: reset")
        return "ok"

    assert await xdownload.with_transient_retry(flaky) == "ok"
    assert hits["n"] == 2

    hits["n"] = 0

    async def always_down():
        hits["n"] += 1
        raise XDownloadError("network", "ClientConnectorError: reset")

    with pytest.raises(XDownloadError) as failed:
        await xdownload.with_transient_retry(always_down)
    assert failed.value.code == "network"
    assert hits["n"] == 2


@pytest.mark.asyncio
async def test_transient_retry_skips_auth_errors(monkeypatch):
    from plugins.qq_watermark import xdownload

    monkeypatch.setattr(xdownload.asyncio, "sleep", _instant_sleep)
    hits = {"n": 0}

    async def unauthorized():
        hits["n"] += 1
        raise XDownloadError("auth", "unauthorized", status=401)

    with pytest.raises(XDownloadError) as failed:
        await xdownload.with_transient_retry(unauthorized)
    assert failed.value.code == "auth"
    assert hits["n"] == 1


@pytest.mark.asyncio
async def test_parse_session_uses_happy_eyeballs():
    from plugins.qq_watermark.xdownload import XDownloadClient

    client = XDownloadClient(base_url="https://dl.example.com/", api_key="x")
    timeout = __import__("aiohttp").ClientTimeout(total=5)
    session = client._session(timeout)
    try:
        delay = getattr(session.connector, "_happy_eyeballs_delay", None)
        if delay is None:
            delay = getattr(session.connector, "happy_eyeballs_delay", None)
        assert delay is not None
        force_close = getattr(session.connector, "_force_close", None)
        if force_close is None:
            force_close = getattr(session.connector, "force_close", None)
        assert force_close is True
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_execute_reparses_after_truncated_download(monkeypatch, tmp_path):
    from types import SimpleNamespace

    plugin = Plugin()
    plugin._settings["api_key"] = "x"
    monkeypatch.setattr("plugins.qq_watermark.plugin.asyncio.sleep", _instant_sleep)
    dirs = {"n": 0}

    def fake_work_dir():
        dirs["n"] += 1
        path = tmp_path / str(dirs["n"])
        path.mkdir()
        return path

    monkeypatch.setattr("plugins.qq_watermark.plugin._work_dir", fake_work_dir)
    monkeypatch.setattr("plugins.qq_watermark.plugin._cleanup_dir", lambda path: None)
    hits = {"parse": 0, "deliver": 0}

    async def fake_parse(_url):
        hits["parse"] += 1
        return SimpleNamespace(media_type="image", title="t")

    async def fake_deliver(_client, _parsed, _url, _context, _work):
        hits["deliver"] += 1
        if hits["deliver"] == 1:
            raise XDownloadError(
                "download_network",
                "ClientPayloadError: Response payload is not completed",
            )
        return ""

    plugin._client = lambda: SimpleNamespace(parse=fake_parse)
    plugin._deliver = fake_deliver
    result = await plugin._execute("https://xhslink.cn/o/abc", {"source": "qq_gateway"})
    assert result == ""
    assert hits["parse"] == 2
    assert hits["deliver"] == 2


@pytest.mark.asyncio
async def test_execute_does_not_reparse_auth_errors(monkeypatch, tmp_path):
    from types import SimpleNamespace

    plugin = Plugin()
    plugin._settings["api_key"] = "x"
    monkeypatch.setattr("plugins.qq_watermark.plugin.asyncio.sleep", _instant_sleep)
    monkeypatch.setattr("plugins.qq_watermark.plugin._work_dir", lambda: tmp_path)
    monkeypatch.setattr("plugins.qq_watermark.plugin._cleanup_dir", lambda path: None)
    hits = {"n": 0}

    async def fake_parse(_url):
        hits["n"] += 1
        raise XDownloadError("auth", "unauthorized", status=401)

    plugin._client = lambda: SimpleNamespace(parse=fake_parse)
    result = await plugin._execute("https://xhslink.cn/o/abc", {"source": "qq_gateway"})
    assert "密钥" in result or "鉴权" in result
    assert hits["n"] == 1


def test_interpret_video_and_gallery():
    video = interpret_parse_payload(
        {"data": {"type": "video", "title": "片段", "links": {"files": [{"download": "https://dl.example/a.mp4"}]}}}
    )
    assert video.media_type == "video"
    assert video.files[0].url.endswith(".mp4")
    images = interpret_parse_payload(
        {
            "data": {
                "type": "image",
                "title": "猫耳",
                "cover": "https://sns-img.xhscdn.com/cover.jpg",
                "webpage_url": "https://www.xiaohongshu.com/explore/abc",
                "links": {
                    "files": [{"download": "https://dl.example/1.jpg", "type": "image"}],
                    "gallery_zip": "https://dl.example/all.zip",
                },
            }
        }
    )
    assert images.gallery_zip.endswith(".zip")
    assert images.title == "猫耳"
    assert images.cover.endswith("cover.jpg")
    assert "xiaohongshu.com" in images.source_url
    with pytest.raises(XDownloadError):
        interpret_parse_payload({"data": {"type": "video"}})


@pytest.mark.asyncio
async def test_image_record_is_images_with_title(tmp_path):
    plugin = Plugin()
    captured = {}

    class _Port:
        async def send_forward(self, nodes, context, **kwargs):
            captured["nodes"] = nodes
            captured["kwargs"] = kwargs
            return {"ok": True}

    first = tmp_path / "a.jpg"
    second = tmp_path / "b.jpg"
    first.write_bytes(b"a")
    second.write_bytes(b"b")
    ok = await plugin._send_image_record(
        [first, second],
        {"channel_meta": {"self_id": "10000", "self_name": "灯"}},
        _Port(),
        "七宗罪这一块",
        source_url="https://xhslink.cn/o/459q0Zqdv2C",
        cover_url="https://sns-img.xhscdn.com/cover.jpg",
    )
    assert ok is True
    assert captured["kwargs"]["title"] == "七宗罪这一块"
    assert len(captured["nodes"]) == 3
    header = captured["nodes"][0]["data"]["content"][0]
    assert header["type"] == "text"
    assert header["data"]["text"] == "七宗罪这一块"
    assert all(node["data"]["content"][0]["type"] == "image" for node in captured["nodes"][1:])


@pytest.mark.asyncio
async def test_napcat_forward_uses_multimsg_card_fields(tmp_path, monkeypatch):
    from integrations.chat_gateway.napcat import NapCatOneBotAdapter

    captured = {}

    async def fake_send_action(self, session_id, action, payload, **kwargs):
        captured["action"] = action
        captured["payload"] = payload
        return {"ok": True, "message_id": 1}

    monkeypatch.setattr(NapCatOneBotAdapter, "_send_action", fake_send_action)
    adapter = NapCatOneBotAdapter()
    first = tmp_path / "a.jpg"
    first.write_bytes(b"a")
    result = await adapter.send_forward(
        "group:860870195",
        [
            {
                "type": "node",
                "data": {
                    "name": "灯",
                    "uin": "10000",
                    "content": [{"type": "text", "data": {"text": "七宗罪这一块"}}],
                },
            },
            {
                "type": "node",
                "data": {
                    "name": "灯",
                    "uin": "10000",
                    "content": [{"type": "image", "data": {"file": first.resolve().as_uri()}}],
                },
            },
        ],
        title="七宗罪这一块",
        source="qq_gateway",
    )
    assert result["ok"] is True
    payload = captured["payload"]
    assert captured["action"] == "send_group_forward_msg"
    assert payload["prompt"] == "[聊天记录]"
    assert payload["source"] == "七宗罪这一块"
    assert payload["news"][0] == {"text": "灯: 七宗罪这一块"}
    assert payload["news"][1] == {"text": "灯: [图片]"}
    assert payload["summary"].startswith("查看")


def test_natural_classification_requires_explicit_verb_and_url():
    url = "https://v.douyin.com/abc/"
    assert classify_natural(f"帮我去水印下载 {url}") == "explicit"
    assert classify_natural(f"这个能解析吗 {url}") == "hint"
    assert classify_natural(url) == "none"
    assert classify_natural("看看这个视频") == "none"
    assert "/去水印" in hint_text()


def test_url_can_come_from_quoted_card_when_text_has_none():
    ctx = {
        "channel_meta": {
            "qq_card_links": ["https://xhslink.cn/o/257vyAKztY4"],
            "reply": {"text": ""},
        }
    }
    assert _resolve_target_url("帮我把这个去水印", ctx).startswith("https://xhslink.cn/")
    assert classify_natural("帮我把这个去水印", ctx) == "explicit"
    assert _resolve_target_url("帮我去水印", {"channel_meta": {}}) == ""
    plugin = Plugin()
    match = plugin._match_natural("提取这个链接", ctx)
    assert match is not None and "xhslink.cn" in match.args["url"]


def test_command_match_and_empty_usage():
    plugin = Plugin()
    match = plugin._match_command(f"{COMMAND} https://v.douyin.com/abc/", {})
    assert match is not None and match.args["url"].startswith("https://")
    assert plugin._match_command("看看 https://v.douyin.com/abc/", {}) is None
    assert plugin._match_natural("只有链接 https://v.douyin.com/abc/", {}) is None
    explicit = plugin._match_natural("帮我把这个去水印下载 https://v.douyin.com/abc/", {})
    assert explicit is not None and explicit.args["confirm"] is True


def test_config_secret_is_not_committed():
    config = json.loads((ROOT / "plugins" / "qq_watermark" / "config.json").read_text(encoding="utf-8"))
    secret = config["settings"]["api_key"]
    assert secret["type"] == "secret"
    assert secret["default"] == ""
    assert config["settings"]["base_url"]["default"].rstrip("/") == "https://dl.tomori.de5.net"
