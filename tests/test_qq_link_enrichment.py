import pytest

from services.chat_support.qq_link_enrichment import QqLinkEnrichmentService


@pytest.mark.asyncio
async def test_link_enrichment_disabled_returns_input_unchanged():
    service = QqLinkEnrichmentService(enabled=False)
    text, images = await service.enrich(
        "看看 https://www.bilibili.com/video/BV123",
        [{"url": "https://example.test/image.png"}],
    )

    assert text == "看看 https://www.bilibili.com/video/BV123"
    assert images == [{"url": "https://example.test/image.png"}]


@pytest.mark.asyncio
async def test_link_enrichment_appends_parser_result():
    class FakeResult:
        platform = "Bilibili"
        title = "视频标题"
        text = "视频简介"
        url = "https://www.bilibili.com/video/BV1xx411c7mD"
        image_urls = ["https://example.test/cover.jpg"]

    class FakeParser:
        platform = "Bilibili"

        def find(self, text, max_links):
            return ["https://www.bilibili.com/video/BV1xx411c7mD"]

        async def parse(self, url):
            return FakeResult()

    service = QqLinkEnrichmentService(enabled=True, parsers=[FakeParser()])
    text, images = await service.enrich(
        "看看 https://www.bilibili.com/video/BV1xx411c7mD",
        [],
    )

    assert "[链接解析]" in text
    assert "平台: Bilibili" in text
    assert "标题: 视频标题" in text
    assert images == [{"url": "https://example.test/cover.jpg"}]


@pytest.mark.asyncio
async def test_bilibili_parser_returns_url_when_network_fails(monkeypatch):
    from services.chat_support.lite_link_parser.parsers.bilibili import (
        BilibiliLinkParser,
    )

    async def failing_fetch(_url):
        raise RuntimeError("offline")

    parser = BilibiliLinkParser(fetch_json=failing_fetch)
    matches = parser.find("看 https://www.bilibili.com/video/BV1xx411c7mD", 3)
    result = await parser.parse(matches[0])

    assert matches == ["https://www.bilibili.com/video/BV1xx411c7mD"]
    assert result.platform == "Bilibili"
    assert result.url == "https://www.bilibili.com/video/BV1xx411c7mD"
    assert result.title == ""
