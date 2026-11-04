import contextlib
import io
import json
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from mbl import health
from mbl.__main__ import main

TODAY = date(2026, 10, 22)


def _stats(**changes):
    stats = {
        "generated_at": "2026-10-22T04:20:00Z",
        "ipv6": {"published": True},
        "cdn_allowlist": {
            "cloudflare": {"source": "live"}, "fastly": {"source": "live"},
            "cloudfront": {"source": "live"}, "cap": {"limit_pct": 10.0},
        },
        "sources": {
            "spamhaus-drop": {"entries": 1688, "entries6": 0, "error": None},
            "spamhaus-drop-v6": {"entries": 0, "entries6": 91, "error": None},
        },
    }
    stats.update(changes)
    return stats


class ProblemsTest(unittest.TestCase):
    def test_healthy(self):
        self.assertEqual(health.problems(_stats(), TODAY), [])

    def test_stale_build(self):
        found = health.problems(_stats(generated_at="2026-10-19T04:20:00Z"), TODAY)
        self.assertEqual(found, ["the published lists are 3 days old (built 2026-10-19)"])
        # two days old is still fine (a failed run plus the next one)
        self.assertEqual(health.problems(_stats(generated_at="2026-10-20T04:20:00Z"), TODAY), [])

    def test_bad_generated_at(self):
        found = health.problems(_stats(generated_at="yesterday"), TODAY)
        self.assertEqual(len(found), 1)
        self.assertIn("generated_at", found[0])

    def test_failed_and_empty_sources(self):
        sources = {
            "a": {"entries": 0, "entries6": 0, "error": "HTTPError: 410"},
            "b": {"entries": 0, "entries6": 0, "error": None},
            "c": {"entries": 0, "entries6": 5, "error": None},
        }
        found = health.problems(_stats(sources=sources), TODAY)
        self.assertEqual(found, ["source a failed: HTTPError: 410", "source b returned no entries"])

    def test_cdn_snapshot_used(self):
        stats = _stats()
        stats["cdn_allowlist"]["fastly"] = {"source": "snapshot"}
        found = health.problems(stats, TODAY)
        self.assertEqual(found, ["CDN allowlist fastly: live list rejected, used the committed snapshot"])

    def test_cdn_published_used(self):
        stats = _stats()
        stats["cdn_allowlist"]["cloudflare"] = {"source": "published"}
        found = health.problems(stats, TODAY)
        self.assertEqual(found, ["CDN allowlist cloudflare: live list rejected, "
                                 "used yesterday's published copy"])

    def test_cdn_missing(self):
        stats = _stats()
        stats["cdn_allowlist"]["cloudfront"] = {"source": "missing"}
        found = health.problems(stats, TODAY)
        self.assertEqual(found, ["CDN allowlist cloudfront: live list rejected and no snapshot, built without it"])

    def test_ipv6_withheld(self):
        found = health.problems(_stats(ipv6={"published": False}), TODAY)
        self.assertEqual(found, ["IPv6 lists were withheld (the IPv4 lists were published)"])


class CliTest(unittest.TestCase):
    def _run(self, stats_text):
        with tempfile.TemporaryDirectory() as tmp:
            stats = Path(tmp, "stats.json")
            stats.write_text(stats_text)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = main(["health", "--stats", str(stats)])
        return code, out.getvalue()

    def test_cli_problem_exit_code(self):
        stats = _stats(generated_at="2000-01-01T00:00:00Z")
        code, out = self._run(json.dumps(stats))
        self.assertEqual(code, 3)
        self.assertIn("the published lists are", out)

    def test_cli_healthy(self):
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        code, out = self._run(json.dumps(_stats(generated_at=now)))
        self.assertEqual((code, out), (0, ""))

    def test_cli_unreadable_stats(self):
        code, out = self._run("not json")
        self.assertEqual(code, 3)
        self.assertIn("stats.json unavailable", out)


if __name__ == "__main__":
    unittest.main()
