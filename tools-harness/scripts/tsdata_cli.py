#!/usr/bin/env python3
"""tsdata CLI — harvest and inspect the local time-series lake.

  python3 scripts/tsdata_cli.py sources
  python3 scripts/tsdata_cli.py probe
  python3 scripts/tsdata_cli.py harvest --source openmeteo_era5
  python3 scripts/tsdata_cli.py harvest --all
  python3 scripts/tsdata_cli.py catalog
  python3 scripts/tsdata_cli.py panel --domain climate
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tsdata.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
