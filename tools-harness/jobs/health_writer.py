#!/usr/bin/env python3
"""Writes health ping to health.db. Run every 5 min via launchd plist.

If this stops writing, the swarm knows it's broken and can alert before
running heavy tasks into a dead network state.
"""
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

DB = Path(__file__).parent / "health.db"


def main() -> None:
    try:
        conn = sqlite3.connect(str(DB))
        try:
            conn.execute("CREATE TABLE IF NOT EXISTS health (id INTEGER PRIMARY KEY, last_ping TEXT, last_error TEXT)")
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                "INSERT OR REPLACE INTO health (id, last_ping, last_error) VALUES (1, ?, NULL)",
                (datetime.now(timezone.utc).isoformat(),),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
