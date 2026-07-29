from modules.memory_sqlite import (
    format_active_tasks_for_prompt,
    format_notes_for_prompt,
    format_recent_episodes_for_prompt,
)


def build_system_prompt(
    *,
    character_manager,
    default_persona,
    system_rules_prompt,
    system_persona,
    current_user_text,
    tool_intent,
    session_id,
    graph,
    graph_expand_enabled,
    graph_expand_min_chars,
    extract_keywords,
    retrieve_memories,
    retrieve_knowledge,
    fetch_profile,
    profile_enabled,
    profile,
    sqlite_store,
    format_tool_history,
):
    time_header = ""
    if "【当前时间】" in system_persona:
        time_header = system_persona.split("\n")[0]

    active_char = character_manager.get_active_character()
    if active_char and active_char.get("prompt"):
        core_persona = active_char["prompt"]
    else:
        core_persona = default_persona

    final_system = f"{time_header}\n\n{core_persona}\n\n{system_rules_prompt}"

    tool_desc = ""
    if "【可用工具能力】" in system_persona:
        parts = system_persona.split("【可用工具能力】")
        if len(parts) > 1:
            tool_desc = "【可用工具能力】" + parts[1]
    elif "【工具】" in system_persona:
        parts = system_persona.split("【工具】")
        if len(parts) > 1:
            tool_desc = "【工具】" + parts[1]
    if tool_desc:
        final_system += "\n\n" + tool_desc

    raw_user = (current_user_text or "").strip()
    tool_mode = bool(tool_intent)
    recall_intent = False
    if hasattr(graph, "_is_recall_intent_query"):
        recall_intent = bool(graph._is_recall_intent_query(raw_user))
    else:
        recall_intent = False
    do_recall = (not tool_mode) and ((len(raw_user) >= 2) or recall_intent)

    search_text = raw_user
    if graph_expand_enabled and len(raw_user) >= graph_expand_min_chars:
        keywords = extract_keywords(raw_user)
        try:
            related = graph.get_related_keywords(keywords, depth=2, top_k=5)
            if related:
                search_text = raw_user + " " + " ".join(related)
        except Exception:
            pass

    session_key = str(session_id or "").strip()
    mem_items = (
        retrieve_memories(search_text, session_id=session_key) if do_recall else []
    )
    mem_text = ""
    if mem_items:
        mem_text = "\n".join(
            [graph._format_memory_item(m["meta"], m["doc"]) for m in mem_items]
        )

    know_items = [] if tool_mode else retrieve_knowledge(search_text, k=2)
    know_text = ""
    if know_items:
        know_text = "\n".join([f"· {k}" for k in know_items])

    profile_text = fetch_profile()
    if not profile_text and profile_enabled and profile:
        profile_text = profile.format_for_prompt()

    sqlite_notes_text = ""
    sqlite_tasks_text = ""
    sqlite_episodes_text = ""
    try:
        if sqlite_store:
            sqlite_tasks_text = format_active_tasks_for_prompt(sqlite_store, limit=6)
            sqlite_notes_text = format_notes_for_prompt(sqlite_store, max_items=24)
            sqlite_episodes_text = format_recent_episodes_for_prompt(
                sqlite_store, limit=3
            )
    except Exception:
        pass

    if profile_text:
        final_system += "\n\n【用户档案与自我认知】:\n" + profile_text
    if sqlite_tasks_text:
        final_system += "\n\n【当前待办/承诺】:\n" + sqlite_tasks_text
    if sqlite_notes_text:
        final_system += "\n\n【重要笔记 (Memory Items)】:\n" + sqlite_notes_text
    if sqlite_episodes_text:
        final_system += "\n\n【近期对话摘要 (Episodes)】:\n" + sqlite_episodes_text
    if know_text:
        final_system += "\n\n【相关知识库】:\n" + know_text
    if mem_text:
        final_system += (
            "\n\n【回忆片段】(仅供参考):\n"
            "当涉及用户既往事实时，优先相信 user 原话；assistant 推断若冲突则降级处理。\n"
            + mem_text
        )

    tool_ctx = format_tool_history(tool_intent)
    if tool_ctx:
        final_system += "\n\n【工具使用记录】:\n" + tool_ctx
    return final_system, recall_intent
