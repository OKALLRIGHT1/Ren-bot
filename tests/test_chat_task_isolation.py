import asyncio
from types import SimpleNamespace

import pytest
import pytest_asyncio

from core.application import Live2DApplication


def qq(session, owner=False):
    return {"source": "qq_gateway", "channel_meta": {
        "session_id": session, "is_owner": owner,
        "message_type": "group" if session.startswith("group:") else "private",
    }}


@pytest_asyncio.fixture
async def app():
    app = Live2DApplication.__new__(Live2DApplication)
    app.loop = asyncio.get_running_loop()
    app.started = {}
    app.cancelled = []
    app.finished_cleanup = []
    app.logger = SimpleNamespace(error=lambda *args: None)
    app.event_bus = SimpleNamespace(emit=None)

    async def cleanup():
        await asyncio.sleep(0)
        app.finished_cleanup.append("desktop")

    async def process(text, ctx):
        app.started.setdefault(text, asyncio.Event()).set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            app.cancelled.append(text)
            raise

    app.chat_service = SimpleNamespace(process=process)
    app._cleanup_cancelled_gui_chat = cleanup
    yield app
    await app._cancel_all_chat_tasks()
    await asyncio.sleep(0)


async def send(app, text, ctx=None):
    event = app.started.setdefault(text, asyncio.Event())
    app.on_gui_send(text, ctx)
    await asyncio.wait_for(event.wait(), 1)


@pytest.mark.asyncio
async def test_qq_sessions_do_not_cancel_each_other_or_desktop(app):
    await send(app, "desktop")
    await send(app, "private", qq("private:42"))
    await send(app, "group", qq("group:7", owner=True))
    assert app.cancelled == []
    assert len(app._chat_tasks) == 3


@pytest.mark.asyncio
async def test_owner_qq_and_desktop_share_task_scope(app):
    await send(app, "desktop")
    await send(app, "owner", qq("private:1", owner=True))
    assert app.cancelled == ["desktop"]
    assert app.finished_cleanup == ["desktop"]
    assert list(app._chat_tasks) == ["owner_shared"]
    await send(app, "desktop-again")
    assert app.cancelled == ["desktop", "owner"]
    # Cancelling remote output must not reset desktop speech/UI.
    assert app.finished_cleanup == ["desktop"]


@pytest.mark.asyncio
async def test_replacing_same_qq_turn_leaves_other_session_running(app):
    await send(app, "A", qq("private:42"))
    await send(app, "B", qq("group:7"))
    await send(app, "A2", qq("private:42"))
    assert app.cancelled == ["A"]
    assert app.finished_cleanup == []
    assert len(app._chat_tasks) == 2


@pytest.mark.asyncio
async def test_desktop_stop_does_not_cancel_other_qq_sessions(app):
    await send(app, "owner", qq("private:1", owner=True))
    await send(app, "other", qq("private:42"))
    task = app._gui_chat_task
    app.cancel_gui_chat()
    await asyncio.gather(task, return_exceptions=True)
    await asyncio.sleep(0)
    assert app.cancelled == ["owner"]
    assert app._gui_chat_task is None
    assert len(app._chat_tasks) == 1


@pytest.mark.asyncio
async def test_shutdown_cancels_all_sessions_and_clears_registry(app):
    await send(app, "desktop")
    await send(app, "other", qq("private:42"))
    await app._cancel_all_chat_tasks()
    await asyncio.sleep(0)
    assert set(app.cancelled) == {"desktop", "other"}
    assert app._chat_tasks == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("queued", [False, True])
async def test_rapid_replacement_waits_for_original_cleanup(app, queued):
    cleanup_started = asyncio.Event()
    release_cleanup = asyncio.Event()

    async def cleanup():
        cleanup_started.set()
        await release_cleanup.wait()
        app.finished_cleanup.append("desktop")

    app._cleanup_cancelled_gui_chat = cleanup
    await send(app, "first")
    app.on_gui_send("second")
    if queued:
        app.on_gui_send("third")
    await asyncio.wait_for(cleanup_started.wait(), 1)
    if not queued:
        app.on_gui_send("third")
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert "second" not in app.started
    assert "third" not in app.started
    third_started = app.started.setdefault("third", asyncio.Event())
    release_cleanup.set()
    await asyncio.wait_for(third_started.wait(), 1)
    assert app.finished_cleanup == ["desktop"]
    assert list(app._chat_tasks) == ["owner_shared"]


@pytest.mark.asyncio
async def test_hidden_command_does_not_replace_owner_turn(app):
    await send(app, "desktop")
    current = app._gui_chat_task
    app.on_gui_send("[CMD: toolbar]")
    await asyncio.sleep(0)
    assert app._gui_chat_task is current
    assert app.cancelled == []


def test_owner_group_and_incomplete_metadata_never_join_desktop():
    app = Live2DApplication.__new__(Live2DApplication)
    assert app._chat_task_key(qq("group:7", owner=True)) != "owner_shared"
    assert app._chat_task_key({"source": "qq_gateway", "channel_meta": {"group_id": "7", "is_owner": True}}) != "owner_shared"
    assert app._chat_task_key({"source": "qq_gateway"}) != app._chat_task_key({"source": "qq_gateway"})
