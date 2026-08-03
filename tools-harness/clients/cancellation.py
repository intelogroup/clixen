import threading

class QueryAbortedException(Exception):
    """Raised when a query is aborted by the client/UI."""
    pass

def check_aborted():
    t = threading.current_thread()
    event = getattr(t, "abort_event", None)
    if event and event.is_set():
        raise QueryAbortedException("Query aborted by client")
