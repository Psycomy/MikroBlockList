"""Health check of a published build, for the daily build-health issue."""
import json
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

from mbl.fetch import USER_AGENT

STATS_TIMEOUT = 15
MAX_STATS_AGE_DAYS = 2  # the build runs daily; older means it stopped publishing


def load_stats(location: str) -> dict:
    """stats.json from a URL or a local path."""
    if location.startswith(("http://", "https://")):
        request = urllib.request.Request(location, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=STATS_TIMEOUT) as resp:
            data = json.load(resp)
    else:
        data = json.loads(Path(location).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("stats.json is not an object")
    return data


def problems(stats: dict, today: date) -> list[str]:
    """One line per problem; empty when the build is healthy."""
    found = []
    stamp = stats.get("generated_at")
    try:
        built = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).date()
    except (TypeError, ValueError):
        found.append(f"stats.json has no valid generated_at ({stamp!r})")
    else:
        age = (today - built).days
        if age > MAX_STATS_AGE_DAYS:
            found.append(f"the published lists are {age} days old (built {built.isoformat()})")

    sources = stats.get("sources")
    for name, info in (sources.items() if isinstance(sources, dict) else []):
        if not isinstance(info, dict):
            continue
        if info.get("error"):
            found.append(f"source {name} failed: {info['error']}")
        elif not info.get("entries") and not info.get("entries6"):
            found.append(f"source {name} returned no entries")

    cdn = stats.get("cdn_allowlist")
    for name, info in (cdn.items() if isinstance(cdn, dict) else []):
        if name == "cap" or not isinstance(info, dict) or info.get("source") == "live":
            continue
        if info.get("source") == "published":
            found.append(f"CDN allowlist {name}: live list rejected, "
                         "used yesterday's published copy")
        elif info.get("source") == "snapshot":
            found.append(f"CDN allowlist {name}: live list rejected, used the committed snapshot")
        else:
            found.append(f"CDN allowlist {name}: live list rejected and no snapshot, built without it")

    ipv6 = stats.get("ipv6")
    if isinstance(ipv6, dict) and ipv6.get("published") is False:
        found.append("IPv6 lists were withheld (the IPv4 lists were published)")
    return found


def run(stats_location: str, today: date | None = None) -> list[str]:
    today = today or datetime.now(timezone.utc).date()
    try:
        stats = load_stats(stats_location)
    except Exception as exc:  # network error, 404, malformed JSON...
        return [f"stats.json unavailable ({type(exc).__name__}: {exc})"]
    return problems(stats, today)
