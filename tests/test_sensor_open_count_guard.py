"""Sensor-only open-count exaggeration guard (separate from natural-chat forbidden)."""

from services.chat_support.sensor_utils import (
    find_sensor_open_count_phrases,
    looks_like_sensor_template_reply,
    sanitize_sensor_open_count_reply,
    strip_sensor_open_count_phrases,
)


def test_find_open_count_digit_and_chinese():
    assert find_sensor_open_count_phrases("你今天打开了12次这个页面")
    assert find_sensor_open_count_phrases("又打开了十几次")
    assert find_sensor_open_count_phrases("切了3次")
    assert find_sensor_open_count_phrases("打开了好多次")
    assert find_sensor_open_count_phrases("打开过八次了吧")
    # 「遍」不在本护栏范围内（避免误伤口语）；次数用「次」
    assert not find_sensor_open_count_phrases("打开了好多遍")
    assert not find_sensor_open_count_phrases("坐很久了，该歇歇")
    assert not find_sensor_open_count_phrases("又回来了？")


def test_strip_keeps_rest_of_sentence():
    cleaned = strip_sensor_open_count_phrases("你今天打开了12次这个页面了，该歇歇")
    assert "打开了" not in cleaned or "12" not in cleaned
    assert "该歇歇" in cleaned
    assert "12" not in cleaned


def test_sanitize_drops_empty_after_strip():
    cleaned, hits = sanitize_sensor_open_count_reply("打开了12次")
    assert hits
    assert cleaned == ""


def test_sanitize_passthrough_ok_lines():
    text = "还在这页挂着啊，起来走走？"
    cleaned, hits = sanitize_sensor_open_count_reply(text)
    assert hits == []
    assert cleaned == text


def test_sanitize_partial_sentence():
    cleaned, hits = sanitize_sensor_open_count_reply(
        "Master 打开了十二次这个文档了，眼睛还好吗"
    )
    assert hits
    assert cleaned
    assert "十二" not in cleaned
    assert "打开了" not in cleaned
    assert "眼睛还好吗" in cleaned


def test_template_guard_keeps_natural_spoken_lines():
    assert not looks_like_sensor_template_reply("你又在抠这里？")
    assert not looks_like_sensor_template_reply("还真盯着这页不放。")
    assert not looks_like_sensor_template_reply("看起来有点困了。")
    assert not looks_like_sensor_template_reply(
        "还挂着这一页啊，眼睛不酸吗，起来走走。"
    )
    assert looks_like_sensor_template_reply("用户正在浏览当前窗口的主要内容。")
    assert looks_like_sensor_template_reply("屏幕上显示一份挺实用的文档。")
