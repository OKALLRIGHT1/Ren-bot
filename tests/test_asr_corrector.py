"""Tests for the ASR post-recognition correction layer."""

import json

from modules.asr.corrector import AsrCorrector, CorrectionRules, LearnedPairs


# ── CorrectionRules ────────────────────────────────────────────────────


def test_rules_apply_simple_replacement(tmp_path):
    path = str(tmp_path / "rules.json")
    rules = CorrectionRules(path)
    rules.add("沈迪", "沈頔")
    result, applied = rules.apply("我今天去了沈迪")
    assert result == "我今天去了沈頔"
    assert len(applied) == 1


def test_rules_longest_pattern_wins(tmp_path):
    """A short rule must not eat the prefix of a longer one."""
    path = str(tmp_path / "rules.json")
    rules = CorrectionRules(path)
    rules.add("迪", "的")
    rules.add("沈迪", "沈頔")
    result, _ = rules.apply("沈迪来了")
    assert result == "沈頔来了"


def test_rules_disabled_not_applied(tmp_path):
    path = str(tmp_path / "rules.json")
    rules = CorrectionRules(path)
    rule = rules.add("测试", "替换")
    rules.set_enabled(rule["id"], False)
    result, applied = rules.apply("这是测试")
    assert result == "这是测试"
    assert applied == []


def test_rules_remove(tmp_path):
    path = str(tmp_path / "rules.json")
    rules = CorrectionRules(path)
    rule = rules.add("A", "B")
    assert rules.remove(rule["id"]) is True
    result, applied = rules.apply("A")
    assert result == "A"
    assert applied == []


def test_rules_persist_and_reload(tmp_path):
    path = str(tmp_path / "rules.json")
    rules = CorrectionRules(path)
    rules.add("旧词", "新词")
    # Fresh instance reads the same file.
    rules2 = CorrectionRules(path)
    result, applied = rules2.apply("旧词在这里")
    assert result == "新词在这里"
    assert len(applied) == 1


def test_rules_missing_file_reads_empty(tmp_path):
    path = str(tmp_path / "does_not_exist.json")
    rules = CorrectionRules(path)
    assert rules.list() == []
    result, applied = rules.apply("任意文本")
    assert result == "任意文本"
    assert applied == []


def test_rules_reject_invalid(tmp_path):
    import pytest

    path = str(tmp_path / "rules.json")
    rules = CorrectionRules(path)
    with pytest.raises(ValueError):
        rules.add("", "B")
    with pytest.raises(ValueError):
        rules.add("A", "A")


# ── LearnedPairs ───────────────────────────────────────────────────────


def test_learned_records_count(tmp_path):
    path = str(tmp_path / "learned.json")
    learned = LearnedPairs(path)
    entry = learned.record("迪", "頔")
    assert entry["count"] == 1
    assert entry["promoted"] is False


def test_learned_idempotent_same_pair_increments(tmp_path):
    path = str(tmp_path / "learned.json")
    learned = LearnedPairs(path, promote_threshold=3)
    learned.record("迪", "頔")
    learned.record("迪", "頔")
    entries = learned.list()
    assert len(entries) == 1
    assert entries[0]["count"] == 2


def test_learned_new_dst_resets(tmp_path):
    path = str(tmp_path / "learned.json")
    learned = LearnedPairs(path, promote_threshold=3)
    learned.record("迪", "頔")
    entry = learned.record("迪", "迪的")  # dst changed
    assert entry["count"] == 1


# ── AsrCorrector (integration of rules + learned) ──────────────────────


def test_corrector_applies_rules(tmp_path):
    rules_path = str(tmp_path / "rules.json")
    learned_path = str(tmp_path / "learned.json")
    corrector = AsrCorrector(rules_path, learned_path)
    corrector.add_rule("五十铃", "五十鈴")
    text, applied = corrector.correct("五十铃你好")
    assert text == "五十鈴你好"
    assert len(applied) == 1


def test_corrector_learn_promotes_at_threshold(tmp_path):
    rules_path = str(tmp_path / "rules.json")
    learned_path = str(tmp_path / "learned.json")
    corrector = AsrCorrector(rules_path, learned_path, promote_threshold=2)

    corrector.learn("沈迪", "沈頔")
    rules = corrector.list_rules()
    # First learn buffers but does not yet promote.
    assert rules == []

    corrector.learn("沈迪", "沈頔")
    # Second learn crosses threshold → promoted into the rule table.
    rules = corrector.list_rules()
    assert any(r["pattern"] == "沈迪" and r["replacement"] == "沈頔" for r in rules)

    # Now correction applies the promoted rule.
    text, applied = corrector.correct("沈迪来了")
    assert text == "沈頔来了"
    assert len(applied) == 1


def test_corrector_learn_below_threshold_no_rule(tmp_path):
    rules_path = str(tmp_path / "rules.json")
    learned_path = str(tmp_path / "learned.json")
    corrector = AsrCorrector(rules_path, learned_path, promote_threshold=3)
    corrector.learn("A", "B")
    assert corrector.list_rules() == []


def test_corrector_reload(tmp_path):
    rules_path = str(tmp_path / "rules.json")
    learned_path = str(tmp_path / "learned.json")
    corrector = AsrCorrector(rules_path, learned_path)
    corrector.add_rule("X", "Y")

    # Simulate an external edit to the rules file.
    with open(rules_path, "w", encoding="utf-8") as handle:
        json.dump({"rules": [{"id": "ext1", "pattern": "外", "replacement": "内", "enabled": True}]}, handle)

    corrector.reload()
    text, applied = corrector.correct("外边")
    assert text == "内边"
    assert applied == ["ext1"]


def test_corrector_no_correction_returns_empty_applied(tmp_path):
    rules_path = str(tmp_path / "rules.json")
    learned_path = str(tmp_path / "learned.json")
    corrector = AsrCorrector(rules_path, learned_path)
    text, applied = corrector.correct("没有任何规则匹配的文本")
    assert text == "没有任何规则匹配的文本"
    assert applied == []
