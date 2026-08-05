from integrations.chat_gateway.media_policy import (
    MediaBlockReason,
    check_http_url,
    check_local_path,
    is_blocked_ip,
    policy_for_source,
)
from integrations.chat_gateway.media_utils import load_image_base64


def test_private_ips_blocked():
    assert is_blocked_ip("127.0.0.1")
    assert is_blocked_ip("10.0.0.1")
    assert is_blocked_ip("192.168.1.1")
    assert is_blocked_ip("169.254.169.254")
    assert is_blocked_ip("::1")


def test_remote_policy_blocks_file_and_localhost():
    policy = policy_for_source("remote")
    ok, _, reason = check_http_url("http://127.0.0.1/x.png", policy)
    assert ok is False
    assert reason == MediaBlockReason.BLOCKED_PRIVATE_IP

    ok, _, reason = check_local_path("C:/Windows/win.ini", policy)
    assert ok is False
    assert reason == MediaBlockReason.BLOCKED_FILE


def test_load_image_remote_blocks_file_uri():
    result = load_image_base64(
        {"url": "file:///C:/Windows/win.ini"},
        source="remote",
    )
    assert result == ""


def test_load_image_remote_blocks_path_field():
    result = load_image_base64(
        {"path": "C:/Windows/win.ini"},
        source="remote",
    )
    assert result == ""


def test_load_image_accepts_data_url():
    # 1x1 png
    raw = (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQ"
        "AAAABJRU5ErkJggg=="
    )
    result = load_image_base64(
        {"url": f"data:image/png;base64,{raw}"},
        source="remote",
    )
    assert result == raw
