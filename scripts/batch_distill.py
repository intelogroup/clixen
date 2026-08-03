#!/usr/bin/env python3
import os
import sqlite3
import subprocess
import sys
import time
import argparse

DB_PATH = os.path.expanduser("~/.forge/forge.db")

def parse_args():
    parser = argparse.ArgumentParser(description="Batch distill undistilled sessions using the new distill-agent.")
    parser.add_argument("--limit", type=int, default=10, help="Maximum number of sessions with >= 3 events to process (default: 10).")
    parser.add_argument("--all", action="store_true", help="Process all available completed undistilled sessions.")
    parser.add_argument("--min-events", type=int, default=0, help="Minimum number of undistilled events required for a session to be processed.")
    parser.add_argument("--include-unknown", action="store_true", help="Include the 'unknown' session in distillation.")
    parser.add_argument("--dry-run", action="store_true", help="Print the sessions that would be processed without executing them.")
    return parser.parse_args()

def main():
    args = parse_args()

    if not os.path.exists(DB_PATH):
        print(f"\033[91mError: Database not found at {DB_PATH}\033[0m")
        sys.exit(1)

    print(f"\033[94mConnecting to database: {DB_PATH}\033[0m")
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Query undistilled sessions
    query = """
        SELECT session_id, count(*) as count, MIN(project_id) as project_id
        FROM events
        WHERE distilled = 0
    """
    if not args.include_unknown:
        query += " AND session_id != 'unknown'"
    
    query += " GROUP BY session_id ORDER BY count DESC;"

    cursor.execute(query)
    all_sessions = cursor.fetchall()

    if not all_sessions:
        print("\033[92mAll sessions are already distilled! Backlog is clean.\033[0m")
        conn.close()
        return

    # Separate sessions into small (< 3 events) and larger (>= 3 events)
    small_sessions = []
    large_sessions = []

    for session_id, count, project_id in all_sessions:
        if count < 3:
            small_sessions.append((session_id, count, project_id))
        else:
            large_sessions.append((session_id, count, project_id))

    print(f"\033[93mFound {len(all_sessions)} undistilled sessions in total:\033[0m")
    print(f"  - {len(large_sessions)} sessions with >= 3 events (require LLM synthesis)")
    print(f"  - {len(small_sessions)} sessions with < 3 events (thin/trivial, fast path marked distilled instantly)")

    # Filter by min-events
    if args.min_events > 0:
        large_sessions = [s for s in large_sessions if s[1] >= args.min_events]
        small_sessions = [s for s in small_sessions if s[1] >= args.min_events]
        print(f"\033[94mAfter filtering with --min-events={args.min_events}:\033[0m")
        print(f"  - {len(large_sessions)} large sessions to process")
        print(f"  - {len(small_sessions)} small sessions to process")

    # Determine limit
    sessions_to_process = []
    
    # 1. We always process all small sessions because they are instant and cost 0 tokens
    sessions_to_process.extend(small_sessions)

    # 2. Limit the large sessions
    if args.all:
        sessions_to_process.extend(large_sessions)
        print(f"\033[95mProcessing ALL {len(large_sessions)} large sessions and {len(small_sessions)} small sessions...\033[0m")
    else:
        limited_large = large_sessions[:args.limit]
        sessions_to_process.extend(limited_large)
        print(f"\033[95mProcessing top {len(limited_large)} large sessions (limit: {args.limit}) and {len(small_sessions)} small sessions...\033[0m")

    if args.dry_run:
        print("\033[96m=== DRY RUN ===\033[0m")
        for session_id, count, project_id in sessions_to_process:
            kind = "SMALL (fast-path)" if count < 3 else "LARGE"
            print(f"Would process: session_id={session_id:<40} events={count:<5} project={project_id:<15} [{kind}]")
        conn.close()
        return

    # Execute distillation
    total_processed = 0
    total_events_distilled = 0
    start_time = time.time()

    print("\033[94mStarting distillation batch execution...\033[0m")
    print("-" * 80)

    for idx, (session_id, count, project_id) in enumerate(sessions_to_process, 1):
        is_small = count < 3
        kind_str = "\033[90m[SMALL]\033[0m" if is_small else "\033[93m[LARGE]\033[0m"
        
        print(f"[{idx}/{len(sessions_to_process)}] {kind_str} Distilling session {session_id} ({count} events, project: {project_id})...")
        
        cmd = [
            "forgememo", "distill-agent",
            "--session-id", session_id,
            "--project-id", project_id or "",
            "--checkpoint-kind", "daemon",
            "--checkpoint-key", f"{session_id}:daemon"
        ]

        session_start = time.time()
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, check=True)
            duration = time.time() - session_start
            
            # Print stderr output if any (logs are printed to stderr in Go)
            stderr_out = res.stderr.strip()
            if stderr_out:
                for line in stderr_out.split("\n"):
                    print(f"  \033[90m{line}\033[0m")
                    
            print(f"  \033[92mSuccess! Completed in {duration:.2f}s\033[0m")
            total_processed += 1
            total_events_distilled += count
            
        except subprocess.CalledProcessError as e:
            duration = time.time() - session_start
            print(f"  \033[91mFailed after {duration:.2f}s!\033[0m")
            if e.stderr:
                print(f"  \033[91mError: {e.stderr.strip()}\033[0m")
            if e.stdout:
                print(f"  \033[90mOutput: {e.stdout.strip()}\033[0m")
            # Continue to next session instead of crashing
            continue

        # Add a tiny sleep to avoid overwhelming any limits or systems
        if not is_small:
            time.sleep(0.5)

    duration = time.time() - start_time
    print("-" * 80)
    print(f"\033[92mBatch distillation complete!\033[0m")
    print(f"  - Sessions processed: {total_processed}/{len(sessions_to_process)}")
    print(f"  - Events distilled:  {total_events_distilled}")
    print(f"  - Total duration:    {duration:.2f}s")
    
    # Check updated database state
    cursor.execute("SELECT count(*), distilled FROM events GROUP BY distilled;")
    counts = cursor.fetchall()
    print("\033[94mUpdated database status:\033[0m")
    for cnt, dist in counts:
        status_str = "Distilled" if dist == 1 else "Undistilled"
        print(f"  - {status_str}: {cnt} events")

    conn.close()

if __name__ == "__main__":
    main()
