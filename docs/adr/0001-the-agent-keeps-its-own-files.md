# 0001 — The agent has its own directory, and nothing else

Status: accepted (2026-10-25). Code: `tools-harness/tools/workspace.py`.

## Context

The orb is a visual-actions agent: it drives the user's screen so they can watch it
work. It had no file capability at all, and the two writers that existed
(`write_file`, `bash_exec`) were gated by `path_policy`, whose write roots were the
repo, the whole dev-projects parent directory and `~/Downloads`. That is a sane
shape for a dev harness and the wrong one for a shipped desktop app: the agent's
files belong to the agent, and a user's folders belong to the user.

Two failure modes motivated a separate boundary rather than widening `path_policy`:

1. Writing into `~/Documents` or `~/Desktop` is both an iCloud-sync decision and an
   all-or-nothing TCC grant — the user cannot consent to "this app writes one file".
2. `DEV_ROOT` was computed as `Path(__file__).parents[3]`, which means "whatever
   directory the Python package happens to sit in". Inside an `.app` that is not the
   developer's projects folder, so the grant silently changes meaning on packaging.

## Decision

`tools/workspace.py` is the only module that decides where the agent's files live:

```
$CLIXEN_WORKSPACE                     the app sets <vault>/workspace
$CLIXEN_DATA_DIR/../workspace         the sidecar's env is <vault>/data
~/Library/Application Support/Clixen/workspace     dev, no app running
```

Four tools (`workspace_list` / `_read` / `_write` / `_delete`), workspace-relative
paths only: absolute paths, `~`, `..` and NUL bytes are rejected outright, and
containment is re-checked **after** `resolve()` so a symlink pointing out is caught.
Writes are atomic (temp + rename) and snapshot what they overwrite into `.trash`;
`delete` moves to `.trash` instead of unlinking.

`DEV_ROOT` and a hardcoded `~/.gemini/antigravity` are removed from `path_policy`.
Writes outside workspace/app-data/tmp/Downloads require `CLIXEN_EXTRA_WRITE_ROOTS`.

**An unmounted vault is an error, not a fallback.** Writing to `<mountpoint>/...`
while the sparse image is detached succeeds against the bare directory underneath
and the file disappears on the next mount. The root needs a `.clixen-vault` marker
or a real mount point, otherwise the call refuses and says so.

## Consequences

- `write_file` / `bash_exec` are not granted to the orb, so "save this" cannot
  become "write into the user's home".
- The 1 MB write cap and the flat namespace are deliberate ceilings for v1; nested
  projects are folders, not new roots.
- Anything that genuinely needs repo access must be granted explicitly, per task.
