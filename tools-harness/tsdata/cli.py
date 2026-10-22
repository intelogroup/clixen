"""tsdata CLI — harvest and inspect the local time-series lake.

Usage:

  python3 scripts/tsdata_cli.py sources              # list registered sources
  python3 scripts/tsdata_cli.py probe                # check connectivity
  python3 scripts/tsdata_cli.py harvest --source openmeteo_era5
  python3 scripts/tsdata_cli.py harvest --all
  python3 scripts/tsdata_cli.py catalog              # what we have
  python3 scripts/tsdata_cli.py panel --domain climate --hours 1000
  python3 scripts/tsdata_cli.py stats
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tsdata.lake import DEFAULT_ROOT, Lake  # noqa: E402
from tsdata.sources import get_source, sources_by_domain  # noqa: E402


def _add_common(p: argparse.ArgumentParser) -> None:
    # Absolute by default: a relative "tools-harness/data/tsdata" resolves
    # against the *current working directory*, so running the CLI from
    # tools-harness/ silently created a second, nested lake.
    p.add_argument("--root", default=str(DEFAULT_ROOT),
                   help=f"lake root directory (default: {DEFAULT_ROOT})")


def cmd_sources(args: argparse.Namespace) -> int:
    by_domain = sources_by_domain()
    for d, ss in by_domain.items():
        print(f"  {d:12s} {len(ss)}")
        for s in ss:
            print(f"      {s.key:22s} {s.name:42s} {s.freq:8s} {s.domain}")
    print(f"\nTotal: {sum(len(v) for v in by_domain.values())} sources")


def cmd_probe(args: argparse.Namespace) -> int:
    with Lake(root=args.root) as lake:
        # a cheap, cheap fetch to prove the plumbing works end to end
        src = get_source("usgs_quakes")
        series = list(src.harvest(lake.fetcher))
        print(f"probe {src.key}: {len(series)} series ready to harvest")
    print("catalog:", lake.stats())
    return 0


def _report(r: dict) -> None:
    """One line per source, plus the covered window when there is one."""
    print(f"  {r['source']:24s} {r['status']:8s} "
          f"series={r.get('n_series', 0):6d} obs={r.get('n_obs', 0):9d} "
          f"{r.get('error', '')}", flush=True)
    if r.get("first_ts"):
        print(f"    window {r['first_ts']} -> {r['last_ts']}  ({r.get('seconds')}s)",
              flush=True)


def cmd_harvest(args: argparse.Namespace) -> int:
    sources = args.source or ["all"]
    with Lake(root=args.root) as lake:
        if sources == ["all"]:
            results = lake.harvest_all(force=args.force)
            for r in results:
                _report(r)
            return 0
        results = []
        for key in sources:
            r = lake.harvest(key, force=args.force)
            results.append(r)
            _report(r)
        return 0


def cmd_catalog(args: argparse.Namespace) -> int:
    with Lake(root=args.root) as lake:
        ds = lake.datasets()
        by = {}
        for d in ds:
            by.setdefault(d["domain"], []).append(d)
        for domain, rows in sorted(by.items()):
            print(f"  {domain:12s} {len(rows):4d} datasets")
            for d in rows[:8]:
                print(f"      {d['dataset_id']:24s} {d['title'][:40]:42s} {d['n_series']:4d}s {d['n_obs']:9d}")
            if len(rows) > 8:
                print(f"      ... and {len(rows)-8} more")
        print(f"\nTotal: {len(ds)} datasets")
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    with Lake(root=args.root) as lake:
        print(lake.stats())
    return 0


def _fmt_age(age: float) -> str:
    if age == float("inf"):
        return "    --"
    if age < 1:
        return f"{age * 24:6.1f}h"
    return f"{age:6.1f}d"


def cmd_eye(args: argparse.Namespace) -> int:
    """The god's-eye view: every feed fused onto one panel."""
    from tsdata.eye import Eye

    with Lake(root=args.root) as lake:
        eye = Eye(lake)
        st = eye.status()
        t = st["totals"]

        print("\n  GOD'S EYE — feed status")
        print(f"  {'DOMAIN':12s} {'FEEDS':>6s} {'LIVE':>5s} {'STALE':>6s} {'EMPTY':>6s} "
              f"{'SERIES':>7s} {'OBS':>10s}")
        for d in st["domains"]:
            print(f"  {d['domain']:12s} {d['feeds']:6d} {d['live']:5d} {d['stale']:6d} "
                  f"{d['empty']:6d} {d['series']:7d} {d['obs']:10d}")
        print(f"  {'TOTAL':12s} {t['feeds']:6d} {t['live']:5d} {t['stale']:6d} "
              f"{t['empty']:6d} {t['series']:7d} {t['obs']:10d}")

        if args.verbose:
            print("\n  FEEDS")
            for f in st["feeds"]:
                flag = {"live": "ok  ", "stale": "stale", "empty": "EMPTY"}[f["status"]]
                print(f"    {flag} {f['dataset_id']:22s} {f['domain']:11s} "
                      f"{f['n_series']:5d}s {f['n_obs']:8d}o  age {_fmt_age(f['age_days'])}"
                      f"  {f['last_ts'][:19]}")
                if f["source_url"]:
                    print(f"         {f['source_url']}  |  {f['license'][:60]}")

        cov = eye.coverage()
        print("\n  COVERAGE")
        print(f"    span {cov['first_ts']}  ->  {cov['last_ts']}   "
              f"({cov['n_series']} series with data)")
        for dom, d in sorted(cov["by_domain"].items()):
            print(f"      {dom:12s} {d['n']:5d} series   {d['first_ts'][:10]} -> {d['last_ts'][:10]}")
        if cov["thin"]:
            print(f"    thin series (<=2 obs): {len(cov['thin'])} "
                  f"(these make a fused panel unreliable)")

        if args.latest:
            print("\n  LATEST")
            print(f"    {'SERIES':38s} {'DOMAIN':11s} {'OBS':>7s} {'AGE':>7s}  LAST")
            for r in eye.latest(limit=args.latest):
                print(f"    {r['series_id'][:38]:38s} {r['domain']:11s} {r['n_obs']:7d} "
                      f"{_fmt_age(r['age_days'])}  {r['last_ts'][:19]}")

        if args.correlate:
            print("\n  CROSS-DOMAIN CORRELATION")
            pairs = eye.correlate(top=args.correlate)
            if not pairs:
                print("    (not enough overlapping series)")
            for p in pairs:
                print(f"    {p['corr']:+.3f}  {p['domain_a']:10s} {p['a_title'][:30]:30s}"
                      f"  <->  {p['domain_b']:10s} {p['b_title'][:30]}")
    return 0


def cmd_panel(args: argparse.Namespace) -> int:
    import pandas as pd
    with Lake(root=args.root) as lake:
        df = lake.panel(domain=args.domain, min_obs=args.min_obs)
        print(f"panel: {len(df)} rows, {df['series_id'].nunique()} series, "
              f"time {df['ts'].min()} -> {df['ts'].max()}")
        print("\nfirst rows:")
        print(df.head(10).to_string())
        print("\nseries list:")
        print(df.groupby("series_id").size().to_string())
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="tsdata", description="Local time-series lake")
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("sources", help="list registered sources")
    _add_common(p)
    p.set_defaults(func=cmd_sources)

    p = sub.add_parser("probe", help="prove the plumbing works")
    _add_common(p)
    p.set_defaults(func=cmd_probe)

    p = sub.add_parser("harvest", help="pull sources")
    _add_common(p)
    p.add_argument(
        "--source",
        action="append",
        choices=["all"]
        + [s.key for ss in sources_by_domain().values() for s in ss],
        default=None,
        help="which source(s) to harvest; repeatable. "
             "Omit (or pass 'all') to harvest every registered source.",
    )
    p.add_argument("--force", action="store_true", help="ignore the cache")
    p.set_defaults(func=cmd_harvest)

    p = sub.add_parser("catalog", help="list what we have")
    _add_common(p)
    p.set_defaults(func=cmd_catalog)

    p = sub.add_parser("stats", help="row counts")
    _add_common(p)
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("eye", help="god's-eye view: every feed fused")
    _add_common(p)
    p.add_argument("-v", "--verbose", action="store_true",
                   help="list every feed with its URL and license")
    p.add_argument("--latest", type=int, default=0, metavar="N",
                   help="show the N most recent series")
    p.add_argument("--correlate", type=int, default=0, metavar="N",
                   help="show the N strongest cross-domain correlations")
    p.set_defaults(func=cmd_eye)

    p = sub.add_parser("panel", help="show the tidy multi-series panel")
    _add_common(p)
    p.add_argument("--domain", default=None)
    p.add_argument("--min-obs", type=int, default=30)
    p.set_defaults(func=cmd_panel)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
