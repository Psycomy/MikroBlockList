"""Build pipeline: fetch, normalize, check and write the blocklist."""
import json
import os
import re
import sys
import tomllib
import urllib.request
from bisect import bisect_right
from datetime import datetime, timezone
from pathlib import Path

from mbl.cdn import DEFAULT_SNAPSHOT, CdnResult, fetch_cdn, write_snapshot
from mbl.checks import LIMITS, LIMITS6, MAX_DROP, safety_errors
from mbl.fetch import USER_AGENT, SourceResult, fetch_all
from mbl.geo import CountryDb
from mbl.lookup import lookup_data, write_lookup
from mbl.normalize import normalize, normalize6
from mbl.render import ipv6_files, write_outputs
from mbl.sources import VARIANTS, load_allowlist, load_sources

PREVIOUS_TIMEOUT = 15
MAX_ALLOWLIST_REMOVAL = 0.10
IPV6_PREFIX = "ipv6:"
CDN_SNAPSHOT_FILE = "cdn-snapshot.txt"  # the next build's first CDN fallback


def _int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _previous_stats(url: str | None) -> tuple[dict[str, int], dict[str, int]]:
    """Previous total per variant and entries per source; empty when unavailable.

    IPv6 values share the dicts under an "ipv6:" prefix ("ipv6:full" total,
    "ipv6:<source>" entries); without an ipv6 section there are none.

    A variant with a missing or malformed total is left out (its drop and growth
    checks are skipped); a source without integer entries is left out (counts as 0).
    """
    if not url:
        return {}, {}
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=PREVIOUS_TIMEOUT) as resp:
            data = json.load(resp)
        if not isinstance(data, dict):
            raise ValueError("stats.json is not an object")
    except Exception as exc:  # network error, 404, malformed JSON...
        print(f"warning: previous stats unavailable ({type(exc).__name__}: {exc}); "
              "skipping drop check", file=sys.stderr)
        return {}, {}
    totals: dict[str, int] = {}
    variants = data.get("variants")
    for name in VARIANTS:
        entry = variants.get(name) if isinstance(variants, dict) else None
        total = entry.get("total") if isinstance(entry, dict) else None
        if total is None and name == "full":
            total = data.get("total")  # stats.json from before the variants
        if total is None:
            continue
        if not _int(total):
            print(f"warning: previous {name} total is not an integer ({total!r}); "
                  f"skipping {name} drop and growth checks", file=sys.stderr)
            continue
        totals[name] = total
    ipv6 = data.get("ipv6")
    variants6 = ipv6.get("variants") if isinstance(ipv6, dict) else None
    for name in VARIANTS:
        entry = variants6.get(name) if isinstance(variants6, dict) else None
        total = entry.get("total") if isinstance(entry, dict) else None
        if _int(total):
            totals[f"{IPV6_PREFIX}{name}"] = total
    sources = data.get("sources")
    entries: dict[str, int] = {}
    for name, info in (sources.items() if isinstance(sources, dict) else ()):
        if not isinstance(info, dict):
            continue
        if _int(info.get("entries")):
            entries[name] = info["entries"]
        if _int(info.get("entries6")):
            entries[f"{IPV6_PREFIX}{name}"] = info["entries6"]
    return totals, entries


def _previous_totals(url: str | None) -> dict[str, int]:
    """Previous total per variant; empty when unavailable (checks are skipped)."""
    return _previous_stats(url)[0]


def _addresses(networks) -> int:
    return sum(n.num_addresses for n in networks)


def _touched(entries, allowlist, version: int) -> int:
    """Number of entries that overlap at least one allowlist network of that version."""
    ranges = sorted((int(n.network_address), int(n.broadcast_address))
                    for n in allowlist if n.version == version)
    merged: list[list[int]] = []
    for start, end in ranges:
        if merged and start <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    starts = [start for start, _ in merged]
    count = 0
    for n in entries:
        # the last merged range starting at or before the entry's end is the only
        # one that can overlap it (the ranges are disjoint and sorted)
        i = bisect_right(starts, int(n.broadcast_address)) - 1
        if i >= 0 and merged[i][1] >= int(n.network_address):
            count += 1
    return count


def _pct(part: int, whole: int) -> float:
    return round(part * 100 / whole, 2) if whole else 0.0


def _allowlist_removal(nets, normalizer, allowlist, version) -> tuple[list, dict, str | None]:
    """Normalize nets with the allowlist; also measure what it takes away.

    Two measures, both against the list normalized without the allowlist: the
    addresses removed and the entries touched (overlapping an allowlist
    network, wholly or partly). Either one over 10 % is an error.
    """
    kept = normalizer(nets, allowlist)
    before = normalizer(nets, [])
    addresses = _addresses(before)
    removed = addresses - _addresses(kept)
    touched = _touched(before, allowlist, version)
    measure = {"addresses": addresses, "addresses_removed": removed,
               "addresses_pct": _pct(removed, addresses), "entries": len(before),
               "entries_touched": touched, "entries_pct": _pct(touched, len(before))}
    over = (removed > addresses * MAX_ALLOWLIST_REMOVAL
            or touched > len(before) * MAX_ALLOWLIST_REMOVAL)
    error = None
    if over:
        error = (f"allowlist removes {removed} of {addresses} addresses "
                 f"({measure['addresses_pct']}%) and touches {touched} of {len(before)} "
                 f"entries ({measure['entries_pct']}%), limit {MAX_ALLOWLIST_REMOVAL:.0%} "
                 "on either")
    return kept, measure, error


def _summary(results: list[SourceResult], total: int) -> tuple[str, str]:
    rows = [(r.source.name, r.source.category, str(sum(n.version == 4 for n in r.networks)),
             str(sum(n.version == 6 for n in r.networks)), str(r.invalid),
             "yes" if r.source.required else "no", r.error or "ok") for r in results]
    head = ("source", "category", "entries", "entries6", "invalid", "required", "status")
    widths = [max(len(h), *(len(row[i]) for row in rows)) if rows else len(h)
              for i, h in enumerate(head)]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    text = "\n".join([fmt.format(*head), *(fmt.format(*row) for row in rows),
                      f"total after normalization: {total}"]) + "\n"
    md = "\n".join(["| " + " | ".join(head) + " |", "|" + "---|" * len(head),
                    *("| " + " | ".join(row) + " |" for row in rows),
                    "", f"Total after normalization: **{total}**"]) + "\n"
    return text, md


def build(sources_path: Path, allowlist_path: Path, out_dir: Path,
          previous_stats_url: str | None, now: datetime | None = None,
          fetcher=fetch_all, allow_growth: bool = False,
          version_path: Path = Path("VERSION"), cdn_fetcher=None,
          cdn_snapshot: Path = DEFAULT_SNAPSHOT, dbip_path: Path | None = None,
          previous_cdn_url: str | None = None) -> int:
    """Return 0 on success, 1 if safety checks fail, 2 on configuration errors."""
    try:
        sources = load_sources(Path(sources_path))
        allowlist = load_allowlist(Path(allowlist_path))
        script_version = Path(version_path).read_text(encoding="utf-8").strip()
        if not script_version:
            raise ValueError(f"{version_path} is empty")
        if not re.fullmatch(r"\d+\.\d+\.\d+", script_version, re.ASCII):
            raise ValueError(f"{version_path} is not MAJOR.MINOR.PATCH: {script_version!r}")
    except (ValueError, TypeError, tomllib.TOMLDecodeError, OSError) as exc:
        print(f"configuration error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    now = now or datetime.now(timezone.utc)
    results = fetcher(sources)
    cdn: CdnResult = (cdn_fetcher or fetch_cdn)(Path(cdn_snapshot),
                                                previous_url=previous_cdn_url)
    allowlist = [*allowlist, *cdn.networks]
    variants, cap_errors = {}, []
    cap = {"limit_pct": round(MAX_ALLOWLIST_REMOVAL * 100, 2)}
    for v in VARIANTS:
        nets = [n for r in results if v in r.source.variants for n in r.networks]
        if v == "full":
            variants[v], cap["ipv4"], error = _allowlist_removal(nets, normalize, allowlist, 4)
            cap_errors += [f"{v}: {error}"] if error else []
        else:
            variants[v] = normalize(nets, allowlist)
    total = len(variants["full"])

    text, md = _summary(results, total)
    print(text, end="")
    print("".join(f"total {v}: {len(nets)}\n" for v, nets in variants.items()), end="")
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as fh:
            fh.write(md)
            if cdn.warnings:
                fh.write("\n**Warning: CDN allowlist**\n\n"
                         + "".join(f"- {w}\n" for w in cdn.warnings))

    previous, previous_entries = _previous_stats(previous_stats_url)

    def check(v, nets, limits, prefix="", label=""):
        low, high = limits[v]
        mine = [r for r in results if v in r.source.variants and r.error]
        failed = [r.source.name for r in mine if r.source.required]
        optional = [r.source.name for r in mine if not r.source.required]
        explained = sum(previous_entries.get(prefix + name, 0) for name in optional)
        prev = previous.get(prefix + v)
        drop = prev - len(nets) if prev else 0
        if prev and optional and drop > prev * MAX_DROP and drop <= explained:
            print(f"warning: {label}{v}: drop of {drop} explained by failed sources "
                  f"({', '.join(optional)}), drop check skipped", file=sys.stderr)
        return [f"{label}{v}: {e}" for e in safety_errors(
            len(nets), prev, failed, min_total=low, max_total=high,
            allow_growth=allow_growth, explained_drop=explained)]

    errors = list(cap_errors)
    for v, nets in variants.items():
        errors += check(v, nets, LIMITS)
    if errors:
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
        return 1

    # IPv6 is best effort: any problem (a failed check or an unexpected exception)
    # withholds all IPv6 files, never fails the build nor blocks the IPv4 files.
    variants6, files6, errors6 = None, None, []
    try:
        variants6 = {}
        for v in VARIANTS:
            nets6 = [n for r in results if v in r.source.variants for n in r.networks]
            if v == "full":
                variants6[v], cap["ipv6"], error = _allowlist_removal(
                    nets6, normalize6, allowlist, 6)
                errors6 += [f"ipv6 {v}: {error}"] if error else []
            else:
                variants6[v] = normalize6(nets6, allowlist)
        print("".join(f"total ipv6 {v}: {len(nets)}\n" for v, nets in variants6.items()),
              end="")
        for v, nets in variants6.items():
            errors6 += check(v, nets, LIMITS6, IPV6_PREFIX, "ipv6 ")
        if not errors6:
            files6 = ipv6_files(variants6, now, script_version)
    except Exception as exc:
        errors6 = [f"ipv6: unexpected {type(exc).__name__}: {exc}"]
    if errors6:
        for error in errors6:
            print(f"warning: IPv6 not published, {error}", file=sys.stderr)
        if summary_path:
            with open(summary_path, "a", encoding="utf-8") as fh:
                fh.write("\n**Warning: IPv6 lists not published**\n\n"
                         + "".join(f"- {e}\n" for e in errors6))
        variants6, files6 = None, None

    # A withheld day keeps the last published IPv6 totals in stats.json, so the
    # next build still compares against that baseline, not against today's.
    baseline6 = {v: previous[IPV6_PREFIX + v] for v in VARIANTS if IPV6_PREFIX + v in previous}
    write_outputs(Path(out_dir), variants, results, now, script_version, variants6,
                  ipv6_baseline=baseline6, files6=files6,
                  cdn_info={**cdn.info, "cap": cap})
    # The CDN blocks used today, for tomorrow's fallback: best effort, a failure
    # only means the next build falls back to the committed snapshot.
    try:
        write_snapshot(Path(out_dir) / CDN_SNAPSHOT_FILE, cdn.blocks, cdn.dates)
    except Exception as exc:
        print(f"warning: {CDN_SNAPSHOT_FILE} not published ({type(exc).__name__}: {exc})",
              file=sys.stderr)
    # The lookup page data is best effort: without DB-IP it has no countries, and
    # any other failure only skips lookup.json (removing a stale one).
    geo = None
    if dbip_path:
        try:
            geo = CountryDb.load(dbip_path)
        except Exception as exc:
            print(f"warning: DB-IP unavailable ({type(exc).__name__}: {exc}); "
                  "lookup.json without countries", file=sys.stderr)
    try:
        write_lookup(Path(out_dir), lookup_data(variants, variants6, results, now, geo))
    except Exception as exc:
        print(f"warning: lookup.json not published ({type(exc).__name__}: {exc})", file=sys.stderr)
        try:
            (Path(out_dir) / "lookup.json").unlink(missing_ok=True)
        except OSError as err:
            print(f"warning: stale lookup.json not removed ({err})", file=sys.stderr)
    return 0
