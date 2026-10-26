"""Rendering of the published output files."""
import json
from datetime import datetime, timezone
from ipaddress import IPv4Network, IPv6Network
from pathlib import Path

from mbl.fetch import SourceResult

TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
PROJECT_URL = "https://github.com/Psycomy/MikroBlockList"


def _cidr(net: IPv4Network | IPv6Network) -> str:
    host = 32 if net.version == 4 else 128
    return str(net.network_address) if net.prefixlen == host else str(net)


def router_txt(addresses: list[str], stamp: str, script_version: str,
               variant: str = "full", family: str = "ipv4") -> str:
    n = len(addresses)
    lines = [f"# MikroBlockList v3 format=1 variant={variant} family={family} "
             f"generated={stamp} entries={n} script={script_version}"]
    lines += addresses
    lines.append(f"# MBL-END entries={n}")
    return "\n".join(lines) + "\n"


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def source_notice(result: SourceResult, fetched_at: str) -> str:
    """Copyright/date notice to show for a source, with a Spamhaus fallback."""
    if result.notice:
        return result.notice
    if result.source.name.startswith("spamhaus"):
        return f"(c) The Spamhaus Project SLU (fetched {fetched_at})"
    return ""


def _notice(results: list[SourceResult], stamp: str) -> str:
    lines = [
        "# MikroBlockList - data notice",
        "",
        f"Project: {PROJECT_URL}",
        "",
        "**For home / non-commercial use only.**",
        "",
        "The blocklist files on this branch are compiled from the third-party sources",
        "listed below. The data remains under each source's own license or terms of use;",
        "it is not covered by the GPL-3.0 license of this repository, which applies to",
        "the code only. Check each source's terms before any other use.",
        "",
        "MikroBlockList is not affiliated with MikroTik. MikroTik and RouterOS are",
        "trademarks of SIA Mikrotīkls, used only to describe compatible routers.",
        "",
        f"Build: {stamp}",
        "",
        "| source | url | category | license / terms | variants | entries | entries6 | error | notice |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        s = r.source
        entries = sum(1 for n in r.networks if n.version == 4)
        cells = [s.name, s.url, s.category, s.license_note, ", ".join(s.variants),
                 str(entries), str(len(r.networks) - entries), r.error or "", source_notice(r, stamp)]
        lines.append("| " + " | ".join(_cell(c) for c in cells) + " |")
    return "\n".join(lines) + "\n"


def _json(data: dict) -> str:
    return json.dumps(data, indent=2) + "\n"


def ipv6_files(variants6: dict[str, list[IPv6Network]], generated_at: datetime,
               script_version: str = "0.0.0") -> dict[str, str]:
    """Contents of the IPv6 files, by file name."""
    stamp = generated_at.astimezone(timezone.utc).strftime(TIME_FORMAT)
    files = {}
    for v, nets in variants6.items():
        addrs = [_cidr(n) for n in nets]
        plain = "blocklist6.txt" if v == "full" else f"blocklist6-{v}.txt"
        files[plain] = "".join(a + "\n" for a in addrs)
        files[f"routeros6-{v}.txt"] = router_txt(addrs, stamp, script_version,
                                                 variant=v, family="ipv6")
    return files


def write_outputs(
    out_dir: Path,
    variants: dict[str, list[IPv4Network]],
    results: list[SourceResult],
    generated_at: datetime,
    script_version: str = "0.0.0",
    variants6: dict[str, list[IPv6Network]] | None = None,
    ipv6_baseline: dict[str, int] | None = None,
    files6: dict[str, str] | None = None,
    cdn_info: dict[str, dict] | None = None,
) -> None:
    """Write every published file; the "full" variant is mandatory.

    variants6 None means IPv6 is withheld: no IPv6 files, and stats.json keeps
    ipv6_baseline (the last published IPv6 totals) so the next build still
    compares against it. files6 are precomputed IPv6 contents (see ipv6_files).
    """
    if "full" not in variants:
        raise ValueError("variants must include 'full'")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    generated_at = generated_at.astimezone(timezone.utc)
    stamp = generated_at.strftime(TIME_FORMAT)
    addresses = {v: [_cidr(n) for n in nets] for v, nets in variants.items()}
    if variants6 is None:
        totals6 = dict(ipv6_baseline or {})
        files6 = {}
    else:
        totals6 = {v: len(nets) for v, nets in variants6.items()}
        if files6 is None:
            files6 = ipv6_files(variants6, generated_at, script_version)
    total = len(addresses["full"])

    stats = {
        "generated_at": stamp,
        "total": total,
        "variants": {v: {"total": len(a)} for v, a in addresses.items()},
        "ipv6": {
            "published": variants6 is not None,
            "variants": {v: {"total": n} for v, n in totals6.items()},
        },
        "cdn_allowlist": cdn_info or {},
        "sources": {
            r.source.name: {
                "category": r.source.category,
                "entries": sum(1 for n in r.networks if n.version == 4),
                "entries6": sum(1 for n in r.networks if n.version == 6),
                "invalid": r.invalid,
                "error": r.error,
            }
            for r in results
        },
    }
    badge_entries = {
        "schemaVersion": 1,
        "label": "entries",
        "message": f"{total:,}",
        "color": "blue",
    }
    badge_updated = {
        "schemaVersion": 1,
        "label": "updated",
        "message": generated_at.strftime("%Y-%m-%d"),
        "color": "brightgreen",
    }

    files = {
        "stats.json": _json(stats),
        "badge-entries.json": _json(badge_entries),
        "badge-updated.json": _json(badge_updated),
        "NOTICE.md": _notice(results, stamp),
    }
    for v, addrs in addresses.items():
        plain = "blocklist.txt" if v == "full" else f"blocklist-{v}.txt"
        files[plain] = "".join(a + "\n" for a in addrs)
        files[f"routeros-{v}.txt"] = router_txt(addrs, stamp, script_version, variant=v)
    files.update(files6)
    for name, content in files.items():
        (out_dir / name).write_text(content, encoding="utf-8", newline="\n")
