"""Automatic CDN allowlist: live provider lists, falling back to yesterday's published copy
and then to the committed snapshot."""
import json
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from functools import partial
from ipaddress import IPv4Network, IPv6Network, ip_network
from pathlib import Path

from mbl.fetch import _download

Network = IPv4Network | IPv6Network

MIN_PREFIXES4 = 5
MIN_PREFIXES6 = 1
MIN_PREFIXLEN4 = 8   # a wider IPv4 prefix is rejected
MIN_PREFIXLEN6 = 16  # a wider IPv6 prefix is rejected
DEFAULT_SNAPSHOT = Path("allowlist-cdn.snapshot.txt")
MAX_SNAPSHOT_AGE_DAYS = 90  # an older snapshot block is still used, with a warning


def _lines(text: str) -> tuple[list[str], list[str]]:
    entries = [line.strip() for line in text.splitlines() if line.strip()]
    return [e for e in entries if ":" not in e], [e for e in entries if ":" in e]


def _cloudflare(texts: list[str]) -> tuple[list[str], list[str]]:
    return _lines(texts[0])[0], _lines(texts[1])[1]


def _fastly(texts: list[str]) -> tuple[list[str], list[str]]:
    data = json.loads(texts[0])
    return list(data["addresses"]), list(data["ipv6_addresses"])


def _cloudfront(texts: list[str]) -> tuple[list[str], list[str]]:
    data = json.loads(texts[0])
    return ([p["ip_prefix"] for p in data["prefixes"] if p["service"] == "CLOUDFRONT"],
            [p["ipv6_prefix"] for p in data["ipv6_prefixes"] if p["service"] == "CLOUDFRONT"])


@dataclass(frozen=True)
class Provider:
    urls: tuple[str, ...]
    extract: Callable[[list[str]], tuple[list[str], list[str]]]


PROVIDERS: dict[str, Provider] = {
    "cloudflare": Provider(("https://www.cloudflare.com/ips-v4",
                            "https://www.cloudflare.com/ips-v6"), _cloudflare),
    "fastly": Provider(("https://api.fastly.com/public-ip-list",), _fastly),
    "cloudfront": Provider(("https://ip-ranges.amazonaws.com/ip-ranges.json",), _cloudfront),
}


@dataclass
class CdnResult:
    networks: list[Network] = field(default_factory=list)
    info: dict[str, dict] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    # the block used per provider (missing ones left out) and its date: today for a
    # live block, the header date (None if undated) for a fallback block
    blocks: dict[str, list[Network]] = field(default_factory=dict)
    dates: dict[str, date | None] = field(default_factory=dict)


def _validate(nets4: list[IPv4Network], nets6: list[IPv6Network]) -> None:
    """Raise ValueError if a provider's prefixes are unusable."""
    if len(nets4) < MIN_PREFIXES4:
        raise ValueError(f"only {len(nets4)} IPv4 prefixes (need {MIN_PREFIXES4})")
    if len(nets6) < MIN_PREFIXES6:
        raise ValueError("no IPv6 prefixes")
    for net in nets4:
        if net.prefixlen < MIN_PREFIXLEN4:
            raise ValueError(f"IPv4 prefix {net} is wider than /{MIN_PREFIXLEN4}")
    for net in nets6:
        if net.prefixlen < MIN_PREFIXLEN6:
            raise ValueError(f"IPv6 prefix {net} is wider than /{MIN_PREFIXLEN6}")


def _checked(strings4: list[str], strings6: list[str]) -> list[Network]:
    nets4 = [IPv4Network(s, strict=False) for s in strings4]
    nets6 = [IPv6Network(s, strict=False) for s in strings6]
    _validate(nets4, nets6)
    return [*nets4, *nets6]


def _live(provider: Provider, fetcher: Callable[[str], str]) -> list[Network]:
    texts = [fetcher(url) for url in provider.urls]
    return _checked(*provider.extract(texts))


def _default_fetcher(backoff: float) -> Callable[[str], str]:
    return partial(_download, timeout=30, retries=2, backoff=backoff)


def fetch_live(fetcher: Callable[[str], str] | None = None,
               backoff: float = 2) -> dict[str, list[Network]]:
    """Download every provider; raise ValueError naming the first one that is rejected."""
    fetcher = fetcher or _default_fetcher(backoff)
    blocks = {}
    for name, provider in PROVIDERS.items():
        try:
            blocks[name] = _live(provider, fetcher)
        except Exception as exc:
            raise ValueError(f"{name}: {type(exc).__name__}: {exc}") from exc
    return blocks


Blocks = tuple[dict[str, list[Network]], dict[str, date | None]]


def _parse_snapshot(text: str) -> Blocks:
    """Valid provider blocks of a snapshot text and the date in each block header (None if none)."""
    raw: dict[str, list[str]] = {}
    dates: dict[str, date | None] = {}
    current = None
    for line in text.splitlines():
        line = line.strip()
        header = re.fullmatch(r"#\s*provider\s+(\S+)(?:\s+(\S+))?", line)
        if header:
            current = header.group(1)
            raw[current] = []
            try:
                dates[current] = date.fromisoformat(header.group(2) or "")
            except ValueError:
                dates[current] = None
        elif line and not line.startswith("#") and current:
            raw[current].append(line)
    blocks = {}
    for name, entries in raw.items():
        if name not in PROVIDERS:
            continue
        try:
            blocks[name] = _checked(*_lines("\n".join(entries)))
        except ValueError:
            continue
    return blocks, {name: dates[name] for name in blocks}


def _read_snapshot(path: Path) -> Blocks:
    """_parse_snapshot of a file; an unreadable file has no blocks."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}, {}
    return _parse_snapshot(text)


def _published(url: str | None, fetcher: Callable[[str], str]) -> Blocks:
    """Blocks of the copy published by the previous build; any problem means none."""
    if not url:
        return {}, {}
    try:
        return _parse_snapshot(fetcher(url))
    except Exception as exc:  # 404, network error...
        print(f"warning: previous CDN snapshot unavailable ({type(exc).__name__}: {exc})",
              file=sys.stderr)
        return {}, {}


def read_snapshot(path: Path) -> dict[str, list[Network]]:
    """Provider blocks of the snapshot; an unreadable file or invalid block is left out."""
    return _read_snapshot(path)[0]


def write_snapshot(path: Path, blocks: dict[str, list[Network]],
                   day: date | dict[str, date | None]) -> None:
    """One '# provider <name> <date>' header and one CIDR per line, per provider.

    day is one date for every block, or a date per provider (None: no date in the header).
    """
    lines = []
    for name in PROVIDERS:
        if name in blocks:
            when = day.get(name) if isinstance(day, dict) else day
            lines.append(f"# provider {name} {when.isoformat()}" if when
                         else f"# provider {name}")
            lines.extend(str(n) for n in blocks[name])
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def _info(source: str, nets: list[Network]) -> dict:
    return {"source": source,
            "prefixes4": sum(n.version == 4 for n in nets),
            "prefixes6": sum(n.version == 6 for n in nets)}


def _age_note(day: date | None, today: date, source: str = "snapshot") -> str:
    """'' for a block at most MAX_SNAPSHOT_AGE_DAYS old, else a note for the warning."""
    if day is None:
        return f"; the {source} block is of unknown age (no date in its header)"
    age = (today - day).days
    if age > MAX_SNAPSHOT_AGE_DAYS:
        note = f"; the {source} block is {age} days old ({day.isoformat()})"
        if source == "snapshot":
            note += ", refresh it with: python3 -m mbl cdn-snapshot"
        return note
    return ""


FALLBACK_LABELS = {"published": "yesterday's published copy", "snapshot": "the committed snapshot"}


def fetch_cdn(snapshot_path: Path = DEFAULT_SNAPSHOT,
              fetcher: Callable[[str], str] | None = None, backoff: float = 2,
              today: date | None = None, previous_url: str | None = None) -> CdnResult:
    """Live lists per provider; a rejected provider falls back to its block in the
    copy published by the previous build (previous_url), then in the committed snapshot.

    The published copy is downloaded only when a live list is rejected, at most
    once. Never raises: a provider with no usable block is left out and reported
    as missing. A fallback block older than MAX_SNAPSHOT_AGE_DAYS (by its header
    date, against today, default the current UTC date) is used with a louder warning.
    """
    fetcher = fetcher or _default_fetcher(backoff)
    today = today or datetime.now(timezone.utc).date()
    fallbacks: dict[str, Blocks] = {}

    def fallback(source: str) -> Blocks:
        if source not in fallbacks:
            fallbacks[source] = (_published(previous_url, fetcher) if source == "published"
                                 else _read_snapshot(snapshot_path))
        return fallbacks[source]

    result = CdnResult()
    for name, provider in PROVIDERS.items():
        try:
            nets, source, day = _live(provider, fetcher), "live", today
        except Exception as exc:
            problem = f"{type(exc).__name__}: {exc}"
            nets, source, day = [], "missing", None
            for candidate in FALLBACK_LABELS:
                blocks, dates = fallback(candidate)
                if name in blocks:
                    nets, source, day = blocks[name], candidate, dates[name]
                    break
            if source == "missing":
                warning = (f"CDN allowlist {name}: live list rejected ({problem}) and no "
                           "published or snapshot block; building without it")
            else:
                warning = (f"CDN allowlist {name}: live list rejected ({problem}); using "
                           f"{FALLBACK_LABELS[source]}" + _age_note(day, today, source))
            result.warnings.append(warning)
            print(f"warning: {warning}", file=sys.stderr)
        result.networks.extend(nets)
        result.info[name] = _info(source, nets)
        if source != "missing":
            result.blocks[name] = nets
            result.dates[name] = day
    return result
