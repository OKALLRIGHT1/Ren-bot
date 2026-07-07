import asyncio
from pathlib import Path
from types import SimpleNamespace

from core.application import Live2DApplication


class DummyLogger:
    def debug(self, message):
        return None


def _make_app(plugin_manager):
    app = Live2DApplication.__new__(Live2DApplication)
    app.plugin_manager = plugin_manager
    app.chat_gateway = None
    app.logger = DummyLogger()
    return app


def test_sedentary_meme_selector_prefers_qq_select_only_interface(tmp_path):
    qq_image = tmp_path / "qq.png"
    fallback_image = tmp_path / "fallback.png"
    qq_image.write_bytes(b"png")
    fallback_image.write_bytes(b"png")
    calls = []

    class QqMemePlugin:
        async def select_qq_meme_image_path(self, **kwargs):
            calls.append(("qq", kwargs["ctx"]["reason"]))
            return {"image_path": str(qq_image)}

    class MemePackPlugin:
        async def select_meme_image_path(self, **kwargs):
            calls.append(("meme_pack", kwargs["ctx"]["reason"]))
            return {"image_path": str(fallback_image)}

    app = _make_app(
        SimpleNamespace(
            plugins={"qq_meme": QqMemePlugin(), "meme_pack": MemePackPlugin()}
        )
    )

    selected = asyncio.run(app._select_sedentary_meme_image_path_async("电脑", 60))

    assert selected == str(qq_image.resolve())
    assert calls == [("qq", "sedentary")]


def test_sedentary_meme_selector_falls_back_to_meme_pack(tmp_path):
    fallback_image = tmp_path / "fallback.png"
    fallback_image.write_bytes(b"png")

    class QqMemePlugin:
        async def select_qq_meme_image_path(self, **kwargs):
            return {}

    class MemePackPlugin:
        async def select_meme_image_path(self, **kwargs):
            return {"image_path": str(fallback_image)}

    app = _make_app(
        SimpleNamespace(
            plugins={"qq_meme": QqMemePlugin(), "meme_pack": MemePackPlugin()}
        )
    )

    selected = asyncio.run(app._select_sedentary_meme_image_path_async("电脑", 60))

    assert selected == str(fallback_image.resolve())


def test_sedentary_meme_selector_ignores_remote_or_missing_paths(tmp_path):
    fallback_image = tmp_path / "fallback.png"
    fallback_image.write_bytes(b"png")

    class QqMemePlugin:
        async def select_qq_meme_image_path(self, **kwargs):
            return {"image_path": "https://example.com/remote.png"}

    class MemePackPlugin:
        async def select_meme_image_path(self, **kwargs):
            return {"image_path": str(fallback_image)}

    app = _make_app(
        SimpleNamespace(
            plugins={"qq_meme": QqMemePlugin(), "meme_pack": MemePackPlugin()}
        )
    )

    selected = asyncio.run(app._select_sedentary_meme_image_path_async("电脑", 60))

    assert selected == str(fallback_image.resolve())


def test_sedentary_meme_selector_accepts_local_file_uri(tmp_path):
    qq_image = tmp_path / "qq.png"
    qq_image.write_bytes(b"png")

    class QqMemePlugin:
        async def select_qq_meme_image_path(self, **kwargs):
            return {"image_path": qq_image.as_uri()}

    app = _make_app(SimpleNamespace(plugins={"qq_meme": QqMemePlugin()}))

    selected = asyncio.run(app._select_sedentary_meme_image_path_async("电脑", 60))

    assert selected == str(qq_image.resolve())
