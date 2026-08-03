# Filesystem Intent Routing (3 tiers)

`harness.py:run()` resolves `intent="filesystem"` through 3 dispatch tiers:

1. **Regex fast path** — `parse_local_fs_action()` matches clear commands ("delete file X", "create folder Y") → direct tool execution, no LLM
2. **Specialist dispatch** — `agents/specialists/dispatch.py` classifies query into 10 specialists (transport/form/video/audio/scraper/data/write/research/read/path), runs the matching specialist, returns result. Added to harness.py at line ~997.
3. **LangGraph ReAct agent** — `agents/local_agent_graph.py` with full tool-calling loop (fallback for ambiguous requests)

Specialist priority chain: `form > video > audio > transport > scraper > data > write > research > read > path`

## Transport Specialist

Fast path (no LLM): regex classification + direct connector calls. Covers Uber price estimates, Uber trip history, and bus/subway/transit queries.

Patterns:
- Accept: `uber`, `lyft`, `taxi`, `bus`, `subway`, `mbta`, `transit`, `directions from/to/between`, ride pricing queries
- Negate: financial context (`stock`, `share`, `ipo`, `revenue`, etc.) and non-transport "transfer" queries

Fast path routing:
- Uber estimate query with location → `estimate_uber_ride(destination, pickup)`
- Uber trip history → `get_my_uber_trips()`
- Bus/transit/directions → `bus_eta(origin, destination)`

Fallback: LLM tool loop with 3 tool schemas (same as transit intent) for ambiguous queries.

Files: `agents/specialists/transport_specialist.py`, patterns in `agents/specialists/dispatch.py`
