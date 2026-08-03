import re

from services.chat_support.reply_style_service import ReplyStyleService


def _make_service() -> ReplyStyleService:
    return ReplyStyleService(
        emo_set={"neutral", "think"},
        emo_tag_re=re.compile(r"<\s*emo\s*=\s*([a-zA-Z_]+)\s*>", re.IGNORECASE),
        cmd_re=re.compile(r"\[CMD:.*?\]", re.DOTALL),
    )


def test_strip_emo_tags_removes_orphan_closing_tag() -> None:
    service = _make_service()

    assert service.strip_emo_tags_anywhere("我查一下……上周的台风\n</emo>") == (
        "我查一下……上周的台风\n"
    )


def test_extract_emo_tag_cleans_orphan_closing_tag() -> None:
    service = _make_service()

    emotion, text = service.extract_emo_tag("我查一下……</emo>")

    assert emotion is None
    assert text == "我查一下……"


def test_extract_emo_tag_removes_matching_open_and_close_tags() -> None:
    service = _make_service()

    emotion, text = service.extract_emo_tag("<emo=think>我查一下……</emo>")

    assert emotion == "think"
    assert text == "我查一下……"
