"""Tests for the 「修正：原词→改成词」 chat-message learning trigger."""

import tempfile
import os

from services.chat_service import _parse_correction_pair


def test_parse_full_width_colon_and_arrow():
    assert _parse_correction_pair("修正：沈迪→沈頔") == ("沈迪", "沈頔")


def test_parse_half_width_colon_and_arrow():
    assert _parse_correction_pair("修正:沈迪->沈頔") == ("沈迪", "沈頔")


def test_parse_fat_arrow():
    assert _parse_correction_pair("修正：A=>B") == ("A", "B")


def test_parse_with_surrounding_spaces():
    assert _parse_correction_pair("  修正： 沈迪 → 沈頔  ") == ("沈迪", "沈頔")


def test_parse_rejects_non_correction_text():
    assert _parse_correction_pair("今天天气怎么样") is None


def test_parse_rejects_empty():
    assert _parse_correction_pair("") is None
    assert _parse_correction_pair(None) is None  # type: ignore[arg-type]


def test_parse_rejects_same_src_dst():
    assert _parse_correction_pair("修正：沈迪→沈迪") is None


def test_parse_rejects_missing_dst():
    assert _parse_correction_pair("修正：沈迪→") is None


def test_learn_via_corrector_after_chat_parse(tmp_path):
    """End-to-end: parsed pair feeds into a real AsrCorrector.learn()."""
    from modules.asr.corrector import AsrCorrector

    corrector = AsrCorrector(
        os.path.join(str(tmp_path), "rules.json"),
        os.path.join(str(tmp_path), "learned.json"),
        promote_threshold=2,
    )
    src, dst = _parse_correction_pair("修正：沈迪→沈頔")
    entry = corrector.learn(src, dst)
    assert entry["count"] == 1
    assert entry["promoted"] is False

    # Second time promotes into the rule table.
    entry2 = corrector.learn(src, dst)
    assert entry2["promoted"] is True
    rules = corrector.list_rules()
    assert any(r["pattern"] == "沈迪" and r["replacement"] == "沈頔" for r in rules)
