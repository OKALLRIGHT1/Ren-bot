"""Tests for the prompt snapshot observability system.

Covers: section splitting, usage extraction, JSONL writing (atomicity,
concurrency, failure silence), RequestContext counter/usage aggregation,
privacy guarantees, and toggle behavior.
"""

from __future__ import annotations

import json
import os
import threading
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from services.chat_support import snapshot as snap_mod
from services.chat_support.snapshot import (
    KNOWN_SECTION_HEADERS,
    RequestContext,
    extract_usage,
    make_request_id,
    split_sections,
    write_snapshot,
)


# ---------------------------------------------------------------------------
# Section splitting
# ---------------------------------------------------------------------------

class TestSplitSections:
    def test_known_headers_classified(self):
        text = (
            "【角色人格】\n你是一个角色\n\n"
            "【输出格式规范】\n规则一\n规则二\n\n"
            "【用户画像】\n姓名\n年龄\n城市"
        )
        result = split_sections(text)
        assert "【角色人格】" in result
        assert "【输出格式规范】" in result
        assert "【用户画像】" in result
        assert result["【角色人格】"]["char_count"] > 0
        assert result["【输出格式规范】"]["line_count"] == 3  # header + 2 lines

    def test_unclassified_no_header(self):
        text = "这是一段没有头部的文字\n第二行\n\n【角色人格】\n内容"
        result = split_sections(text)
        assert "__unclassified__" in result
        assert result["__unclassified__"]["char_count"] > 0
        assert "【角色人格】" in result

    def test_unclassified_unknown_header(self):
        text = "【未知区块】\n一些内容\n\n【角色人格】\n内容"
        result = split_sections(text)
        assert "【未知区块】" not in result
        assert "__unclassified__" in result
        assert "【角色人格】" in result

    def test_no_raw_content_in_result(self):
        text = "【角色人格】\n超级秘密信息不应该泄露\n\n"
        result = split_sections(text)
        serialized = json.dumps(result, ensure_ascii=False)
        assert "超级秘密信息" not in serialized
        # only char_count and line_count keys
        for v in result.values():
            assert set(v.keys()) == {"char_count", "line_count"}

    def test_long_header_does_not_crash(self):
        # header interior > 80 chars should not match the regex
        long_name = "x" * 100
        text = f"【{long_name}】\n内容"
        result = split_sections(text)
        assert "__unclassified__" in result
        assert f"【{long_name}】" not in result

    def test_empty_input(self):
        assert split_sections("") == {}
        assert split_sections(None) == {}

    def test_all_known_headers_present_in_whitelist(self):
        # spot-check a few critical headers are in the whitelist
        assert "【角色人格】" in KNOWN_SECTION_HEADERS
        assert "【本轮内心·勿照念】" in KNOWN_SECTION_HEADERS
        assert "【说法参考·可忽略】" in KNOWN_SECTION_HEADERS
        assert "【用户画像】" in KNOWN_SECTION_HEADERS

    def test_line_count_single_line(self):
        text = "【角色人格】\n一行"
        result = split_sections(text)
        assert result["【角色人格】"]["line_count"] == 2  # header + 1 content line

    def test_multiple_unclassified_chunks_merged(self):
        text = "块一\n\n块二\n\n【角色人格】\n内容"
        result = split_sections(text)
        assert "__unclassified__" in result
        # both blocks merged into one aggregate
        assert result["__unclassified__"]["char_count"] >= len("块一") + len("块二")


# ---------------------------------------------------------------------------
# Usage extraction
# ---------------------------------------------------------------------------

class TestExtractUsage:
    def test_openai_sync(self):
        resp = SimpleNamespace(
            usage=SimpleNamespace(
                prompt_tokens=100,
                completion_tokens=50,
                total_tokens=150,
            )
        )
        result = extract_usage(resp, "openai")
        assert result["prompt_tokens"] == 100
        assert result["completion_tokens"] == 50
        assert result["total_tokens"] == 150

    def test_openai_dict_usage(self):
        resp = {"usage": {"prompt_tokens": 200, "completion_tokens": 80, "total_tokens": 280}}
        result = extract_usage(resp, "openai")
        assert result["prompt_tokens"] == 200
        assert result["completion_tokens"] == 80

    def test_gemini_native(self):
        raw = {
            "usageMetadata": {
                "promptTokenCount": 300,
                "candidatesTokenCount": 120,
                "totalTokenCount": 420,
            }
        }
        result = extract_usage(None, "gemini_native", raw_json=raw)
        assert result["prompt_tokens"] == 300
        assert result["completion_tokens"] == 120
        assert result["total_tokens"] == 420

    def test_missing_usage_returns_none(self):
        resp = SimpleNamespace(usage=None)
        result = extract_usage(resp, "openai")
        assert result["prompt_tokens"] is None
        assert result["completion_tokens"] is None
        assert result["total_tokens"] is None

    def test_empty_response(self):
        result = extract_usage(None, "openai")
        assert result["prompt_tokens"] is None

    def test_exception_returns_none(self):
        # object that raises on attribute access
        class Bad:
            @property
            def usage(self):
                raise RuntimeError("boom")

        result = extract_usage(Bad(), "openai")
        assert result["prompt_tokens"] is None


# ---------------------------------------------------------------------------
# RequestContext
# ---------------------------------------------------------------------------

class TestRequestContext:
    def test_counter_increments(self):
        ctx = RequestContext(request_id="test-1")
        assert ctx.llm_call_count == 0
        assert ctx.next_call_seq() == 1
        assert ctx.next_call_seq() == 2
        assert ctx.next_call_seq() == 3
        assert ctx.llm_call_count == 3

    def test_usage_aggregation_empty(self):
        ctx = RequestContext(request_id="test-2")
        usage = ctx.total_usage
        assert usage["available"] is False
        assert usage["prompt_tokens"] is None

    def test_usage_aggregation(self):
        ctx = RequestContext(request_id="test-3")
        ctx.record_usage({"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150})
        ctx.record_usage({"prompt_tokens": 200, "completion_tokens": 80, "total_tokens": 280})
        usage = ctx.total_usage
        assert usage["available"] is True
        assert usage["prompt_tokens"] == 300
        assert usage["completion_tokens"] == 130
        assert usage["total_tokens"] == 430

    def test_request_id_format(self):
        rid = make_request_id()
        assert rid.startswith("p")
        assert "-" in rid


# ---------------------------------------------------------------------------
# JSONL writing
# ---------------------------------------------------------------------------

class TestWriteSnapshot:
    def test_basic_write(self, tmp_path):
        path = str(tmp_path / "snap.jsonl")
        snap_mod.set_snapshot_path(path)
        try:
            write_snapshot({"request_id": "t1", "status": "ok"})
            write_snapshot({"request_id": "t2", "status": "error"})
            with open(path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            assert len(lines) == 2
            r1 = json.loads(lines[0])
            assert r1["request_id"] == "t1"
            assert r1["status"] == "ok"
        finally:
            snap_mod.reset_snapshot_path()

    def test_concurrent_writes(self, tmp_path):
        path = str(tmp_path / "concurrent.jsonl")
        snap_mod.set_snapshot_path(path)
        try:
            def writer(start, count):
                for i in range(start, start + count):
                    write_snapshot({"request_id": f"c{i}", "seq": i})

            threads = []
            for t in range(10):
                threads.append(threading.Thread(target=writer, args=(t * 100, 10)))
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            with open(path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            assert len(lines) == 100
            ids = [json.loads(line)["request_id"] for line in lines]
            assert len(set(ids)) == 100  # all unique, no corruption
        finally:
            snap_mod.reset_snapshot_path()

    def test_failure_silent(self, tmp_path):
        # point to an impossible path
        path = str(tmp_path / "nonexistent_dir" / "deep" / "snap.jsonl")
        snap_mod.set_snapshot_path(path)
        try:
            # should not raise
            write_snapshot({"request_id": "fail-test"})
        finally:
            snap_mod.reset_snapshot_path()

    def test_no_content_fields(self, tmp_path):
        """Ensure snapshot dict has no privacy-sensitive keys."""
        path = str(tmp_path / "privacy.jsonl")
        snap_mod.set_snapshot_path(path)
        try:
            snapshot = {
                "request_id": "p1",
                "sections": {"【角色人格】": {"char_count": 230, "line_count": 4}},
                "usage": {"prompt_tokens": 100, "completion_tokens": 50,
                          "total_tokens": 150, "available": True},
            }
            write_snapshot(snapshot)
            with open(path, "r", encoding="utf-8") as f:
                data = json.loads(f.read())
            for forbidden in ("content", "tool_calls", "arguments", "image_url", "audio", "tokens"):
                assert forbidden not in data, f"privacy: '{forbidden}' should not be in snapshot"
        finally:
            snap_mod.reset_snapshot_path()


# ---------------------------------------------------------------------------
# Integration: snapshot disabled = no file
# ---------------------------------------------------------------------------

class TestSnapshotToggle:
    def test_write_final_snapshot_disabled(self, tmp_path):
        """When PROMPT_SNAPSHOT_ENABLED is False, no file should be written."""
        path = str(tmp_path / "toggle_off.jsonl")
        snap_mod.set_snapshot_path(path)
        try:
            from services.chat_support.natural_chat_pipeline import _write_final_snapshot

            req_ctx = RequestContext(request_id="toggle-test")
            with patch("services.chat_support.natural_chat_pipeline.PROMPT_SNAPSHOT_ENABLED", False):
                _write_final_snapshot(
                    req_ctx, 0.0, None, "ok",
                    {"triggered": False, "retried": False},
                    {"source": "test"}, "cmd",
                )
            assert not os.path.exists(path)
        finally:
            snap_mod.reset_snapshot_path()

    def test_write_final_snapshot_enabled(self, tmp_path):
        """When PROMPT_SNAPSHOT_ENABLED is True, file should be written."""
        path = str(tmp_path / "toggle_on.jsonl")
        snap_mod.set_snapshot_path(path)
        try:
            from services.chat_support.natural_chat_pipeline import _write_final_snapshot, AssembledChatPrompt

            req_ctx = RequestContext(request_id="toggle-on-test")
            req_ctx.next_call_seq()
            assembled = AssembledChatPrompt(
                snapshot_payload={
                    "sections": {"【角色人格】": {"char_count": 230, "line_count": 4}},
                    "system_total_chars": 230,
                    "system_total_lines": 4,
                    "history_count": 2,
                    "history_total_chars": 100,
                    "tools_count": 0,
                    "thought": {"enabled": False, "reason": "off"},
                    "expression_enabled": False,
                }
            )
            with patch("services.chat_support.natural_chat_pipeline.PROMPT_SNAPSHOT_ENABLED", True):
                _write_final_snapshot(
                    req_ctx, 1000.0, assembled, "ok",
                    {"triggered": False, "retried": False},
                    {"source": "qq"}, "cmd",
                )
            assert os.path.exists(path)
            with open(path, "r", encoding="utf-8") as f:
                data = json.loads(f.read())
            assert data["request_id"] == "toggle-on-test"
            assert data["source"] == "qq"
            assert data["status"] == "ok"
            assert data["llm_call_count"] == 1
            assert data["system_total_chars"] == 230
            assert data["thought"]["enabled"] is False
            assert data["expression_injected"] is False
            assert data["thought_injected"] is False
            # latency should be positive
            assert data["total_latency_ms"] >= 0
        finally:
            snap_mod.reset_snapshot_path()


# ---------------------------------------------------------------------------
# Integration: llm.py records request_id in metrics
# ---------------------------------------------------------------------------

class TestLLMMetricsRequestId:
    def test_metrics_include_request_id(self):
        from modules.llm import get_recent_llm_metrics

        # just verify the field exists in recent metric entries
        # (actual LLM calls are mocked elsewhere)
        metrics = get_recent_llm_metrics(limit=5)
        for m in metrics:
            assert "request_id" in m  # field always present, may be ""
