import threading


def test_session_summary_writes_are_serialized(monkeypatch):
    from tools import memory_tools

    active = 0
    peak = 0
    guard = threading.Lock()

    class Table:
        def delete(self, _query):
            nonlocal active, peak
            with guard:
                active += 1
                peak = max(peak, active)
            # A real Lance transaction yields here under load.
            import time
            time.sleep(0.005)
            with guard:
                active -= 1

    class KB:
        table = Table()

        def store(self, **_kwargs):
            return None

    monkeypatch.setattr(memory_tools, "_kb", lambda: KB())
    threads = [threading.Thread(target=memory_tools.save_session_summary, args=(f"chat-{i}", "summary")) for i in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert peak == 1
