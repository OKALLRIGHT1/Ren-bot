from services.chat_support.text_utils import looks_like_leaked_tool_call


def test_leaked_cmd_and_function_call_are_detected():
    assert looks_like_leaked_tool_call('[CMD: llm_monitor | summary ||| 50]')
    assert looks_like_leaked_tool_call('llm_monitor("summary ||| 50")')
    assert looks_like_leaked_tool_call('llm_monitor("summary ||| 50")')
    assert not looks_like_leaked_tool_call("我最喜欢仙子伊布")
    assert not looks_like_leaked_tool_call("")
