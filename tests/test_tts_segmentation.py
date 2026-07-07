from modules.tts.stream_utils import StreamSentenceBuffer


def test_stream_sentence_buffer_flushes_at_soft_boundary_when_too_long():
    buffer = StreamSentenceBuffer(min_chars=1, max_chars=10)

    assert list(buffer.feed("第一段内容，")) == []
    assert list(buffer.feed("第二段内容，")) == ["第一段内容，"]
    assert list(buffer.close()) == ["第二段内容，"]


def test_stream_sentence_buffer_keeps_hard_sentence_boundaries():
    buffer = StreamSentenceBuffer(max_chars=10)

    assert list(buffer.feed("第一句。第二")) == ["第一句。"]
    assert list(buffer.close()) == ["第二"]
