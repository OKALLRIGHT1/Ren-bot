import pytest

from services.agent_adapters import NoopAgentAdapter


@pytest.mark.asyncio
async def test_noop_adapter_declines_handling():
    adapter = NoopAgentAdapter()
    result = await adapter.handle("查邮件", {"source": "text_input"}, tools=[])

    assert result["handled"] is False
    assert result["reply"] is None
