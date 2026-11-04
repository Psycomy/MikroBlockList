"""Command line entry point: python -m mbl build | cdn-snapshot | health."""
import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from mbl.build import build
from mbl.cdn import DEFAULT_SNAPSHOT, fetch_live, write_snapshot
from mbl.geo import fetch_dbip
from mbl.health import run as health


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mbl")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("build", help="fetch sources and write the blocklist files")
    p.add_argument("--sources", type=Path, default=Path("sources.toml"))
    p.add_argument("--allowlist", type=Path, default=Path("allowlist.txt"))
    p.add_argument("--out", type=Path, default=Path("dist"))
    p.add_argument("--version-file", type=Path, default=Path("VERSION"))
    p.add_argument("--previous-stats", default=None, metavar="URL")
    p.add_argument("--allow-growth", action="store_true",
                   help="skip only the check that refuses a total more than 2x the previous one")
    p.add_argument("--cdn-snapshot", type=Path, default=DEFAULT_SNAPSHOT,
                   help="CDN provider lists used when a live download is rejected and "
                        "the previous build's copy is unavailable")
    p.add_argument("--previous-cdn", default=None, metavar="URL",
                   help="cdn-snapshot.txt published by the previous build (first fallback)")
    p.add_argument("--dbip", type=Path, default=None, metavar="PATH",
                   help="DB-IP country file (from dbip-fetch) for the countries in lookup.json")
    s = sub.add_parser("cdn-snapshot", help="download the CDN lists into a snapshot file")
    s.add_argument("--out", type=Path, default=DEFAULT_SNAPSHOT)
    h = sub.add_parser("health", help="check a published build; prints one line per problem")
    h.add_argument("--stats", required=True, metavar="URL_OR_PATH")
    d = sub.add_parser("dbip-fetch", help="download the monthly DB-IP country file; prints its path")
    d.add_argument("--cache", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "dbip-fetch":
        try:
            found = fetch_dbip(args.cache, datetime.now(timezone.utc).date())
        except Exception as exc:  # a DB-IP problem must never fail the workflow
            print(f"warning: DB-IP fetch failed: {exc}", file=sys.stderr)
            found = None
        if found is None:
            print("warning: no DB-IP country file available", file=sys.stderr)
        else:
            print(found)
        return 0
    if args.command == "health":
        found = health(args.stats)
        for line in found:
            print(line)
        return 3 if found else 0
    if args.command == "cdn-snapshot":
        try:
            blocks = fetch_live()
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        write_snapshot(args.out, blocks, datetime.now(timezone.utc).date())
        print(f"wrote {args.out}: " + ", ".join(f"{k} {len(v)}" for k, v in blocks.items()))
        return 0
    return build(args.sources, args.allowlist, args.out, args.previous_stats,
                 allow_growth=args.allow_growth, version_path=args.version_file,
                 cdn_snapshot=args.cdn_snapshot, dbip_path=args.dbip,
                 previous_cdn_url=args.previous_cdn)


if __name__ == "__main__":
    sys.exit(main())
