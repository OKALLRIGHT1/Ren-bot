"""Forbidden phrase guard tests."""

from __future__ import annotations

from services.chat_support.forbidden_phrase_guard import (
    build_retry_constraint,
    find_forbidden_phrases,
    should_retry_after_forbidden,
    strip_forbidden_spans,
)
from services.chat_support.natural_chat_pipeline import evaluate_forbidden_reply


def test_find_hard_switch_label():
    hits = find_forbidden_phrases("这切换得好硬啊")
    assert any("切换得好硬" in h for h in hits)


def test_find_multiple():
    hits = find_forbidden_phrases("节奏很怪，落差好大")
    assert len(hits) >= 2


def test_clean_text_no_hit():
    assert find_forbidden_phrases("今天开会啊……") == []


def test_retry_policy():
    assert should_retry_after_forbidden(hits=["切换得好硬"], retries_done=0, max_retries=1)
    assert not should_retry_after_forbidden(
        hits=["切换得好硬"], retries_done=1, max_retries=1
    )
    assert not should_retry_after_forbidden(hits=[], retries_done=0, max_retries=1)


def test_strip_spans():
    cleaned = strip_forbidden_spans("嗯，切换得好硬，这样啊")
    assert "切换得好硬" not in cleaned
    assert "嗯" in cleaned or "这样" in cleaned


def test_evaluate_retry_then_strip():
    first = evaluate_forbidden_reply(
        "切换得好硬呢", retries_done=0, max_retries=1
    )
    assert first["should_retry"] is True
    assert first["retry_constraint"]
    second = evaluate_forbidden_reply(
        "切换得好硬呢", retries_done=1, max_retries=1
    )
    assert second["should_retry"] is False
    assert "切换得好硬" not in second["stripped"]


def test_retry_constraint_mentions_hit():
    text = build_retry_constraint(["落差好大"])
    assert "落差好大" in text
