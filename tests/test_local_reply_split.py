from services.chat_support.text_splitter import (
    split_assistant_display_parts,
    split_chat_text_parts,
)


def test_split_chat_text_parts_splits_natural_paragraphs_like_qq():
    text = (
        "嗯......虽然我很想帮你，但“发邮件”的能力好像暂时用不了了。\n\n"
        "主人可能还需要一些设置，才能让我帮你处理这件事情。\n"
        "你可以先告诉我收件人和正文，我帮你整理成草稿。"
    )

    parts = split_chat_text_parts(text)

    assert parts == [
        "嗯......虽然我很想帮你，但“发邮件”的能力好像暂时用不了了。",
        "主人可能还需要一些设置，才能让我帮你处理这件事情。",
        "你可以先告诉我收件人和正文，我帮你整理成草稿。",
    ]


def test_split_chat_text_parts_keeps_code_blocks_together():
    text = "可以这样：\n```python\nprint('hi')\n```\n然后再运行。"

    parts = split_chat_text_parts(text)

    assert parts == [text]


def test_tts_split_text_uses_chat_part_splitting():
    from modules.tts.router import _split_text

    text = (
        "第一段很短。\n\n"
        "第二段也不长。\n"
        "第三段应该单独冒泡。"
    )

    assert _split_text(text, 80) == [
        "第一段很短。",
        "第二段也不长。",
        "第三段应该单独冒泡。",
    ]


def test_silent_bubble_segments_use_chat_part_splitting():
    from core.application import split_local_bubble_text_parts

    text = "第一段。\n\n第二段。\n第三段。"

    assert split_local_bubble_text_parts(text) == ["第一段。", "第二段。", "第三段。"]


def test_finished_sentences_stay_separate_even_when_under_chunk_limit():
    text = (
        "嗯……Master酱说的“完美”，我会好好收在心里的。"
        "台风的话，上海这边……秋天偶尔会有它的尾巴扫过。"
        "但比起那个，我更担心你加班太晚，路上被风吹到。"
    )

    assert split_chat_text_parts(text, max_len=80) == [
        "嗯……Master酱说的“完美”，我会好好收在心里的。",
        "台风的话，上海这边……秋天偶尔会有它的尾巴扫过。",
        "但比起那个，我更担心你加班太晚，路上被风吹到。",
    ]
    assert split_assistant_display_parts(text) == split_chat_text_parts(text, max_len=80)
