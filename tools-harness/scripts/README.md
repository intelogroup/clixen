# scripts/

## trace_candidates.py

Mines `store/trace_store.py` (sqlite) for recurring tool-call failure
patterns and prints ranked candidates. `--push` feeds new/escalated
patterns into forgememo (`forge save --type failure`) so they surface via
`get_active_failures`/`get_principles` like any other learned lesson.
Dedup state (pattern hash → last-pushed count) lives in
`data/trace_candidates_pushed.json` (gitignored, local only).

```
.venv/bin/python scripts/trace_candidates.py [--min-count N] [--limit RUNS] [--push]
```

Runs nightly at 3:30am via `com.clixen.trace_candidates.plist` (checked in
here, installed to `~/Library/LaunchAgents/`, same as every other
`com.clixen.*` launchd job — see repo root `CLAUDE.md`).

```
cp scripts/com.clixen.trace_candidates.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.clixen.trace_candidates.plist
```

Log: `tools-harness/trace_candidates.log`.
