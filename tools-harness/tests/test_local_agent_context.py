import inspect


def test_local_agent_passes_chat_id_to_history_trimming():
    from agents import local_agent_graph

    source = inspect.getsource(local_agent_graph)
    assert source.count("trim_to_budget(raw_history, model, query, chat_id=chat_id)") >= 2
