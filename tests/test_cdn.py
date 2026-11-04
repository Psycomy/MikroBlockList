import contextlib
import io
import tempfile
import unittest
import urllib.error
from datetime import date
from ipaddress import ip_network
from pathlib import Path
from unittest import mock

from mbl import cdn
from mbl.__main__ import main

FIXTURES = Path(__file__).parent / "fixtures"
COMMITTED_SNAPSHOT = Path(__file__).parent.parent / "allowlist-cdn.snapshot.txt"
URL_FILES = {
    "https://www.cloudflare.com/ips-v4": "cdn_cloudflare_v4.txt",
    "https://www.cloudflare.com/ips-v6": "cdn_cloudflare_v6.txt",
    "https://api.fastly.com/public-ip-list": "cdn_fastly.json",
    "https://ip-ranges.amazonaws.com/ip-ranges.json": "cdn_cloudfront.json",
}


def _text(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def _fetcher(overrides=None):
    """Serve the fixtures; an override is text to return or an Exception to raise."""
    overrides = overrides or {}

    def fetch(url):
        value = overrides.get(url, None)
        if isinstance(value, Exception):
            raise value
        return _text(URL_FILES[url]) if value is None else value
    return fetch


def _snapshot_text():
    return (
        "# provider cloudflare 2026-01-01\n"
        + "".join(f"10{i}.0.0.0/24\n".replace("10", "20", 1) for i in range(1, 7))
        + "2606:4700::/32\n"
        + "# provider fastly 2026-01-01\n"
        + "".join(f"21{i}.0.0.0/24\n" for i in range(1, 7))
        + "2a04:4e40::/32\n"
        + "# provider cloudfront 2026-01-01\n"
        + "".join(f"22{i}.0.0.0/24\n" for i in range(1, 7))
        + "2600:9000::/28\n"
    )


class CdnTestBase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.snapshot = self.tmp / "allowlist-cdn.snapshot.txt"
        self.snapshot.write_text(_snapshot_text())

    def fetch(self, overrides=None, snapshot=None, today=None):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            extra = {"today": today} if today else {}
            result = cdn.fetch_cdn(snapshot or self.snapshot, _fetcher(overrides), backoff=0,
                                   **extra)
        return result, err.getvalue()


class ExtractionTest(CdnTestBase):
    def test_all_live(self):
        result, err = self.fetch()
        self.assertEqual(err, "")
        self.assertEqual({n: i["source"] for n, i in result.info.items()},
                         {"cloudflare": "live", "fastly": "live", "cloudfront": "live"})
        self.assertEqual(result.info["cloudflare"]["prefixes4"], 15)
        self.assertEqual(result.info["cloudflare"]["prefixes6"], 7)
        self.assertEqual(result.info["fastly"]["prefixes4"], 21)
        self.assertEqual(result.info["fastly"]["prefixes6"], 3)
        self.assertIn(ip_network("104.16.0.0/13"), result.networks)
        self.assertIn(ip_network("151.101.0.0/16"), result.networks)
        self.assertIn(ip_network("2a04:4e40::/32"), result.networks)

    def test_cloudfront_only_cloudfront_service(self):
        result, _ = self.fetch()
        # fixture: 6 CLOUDFRONT and 3 other prefixes per family
        self.assertEqual(result.info["cloudfront"]["prefixes4"], 6)
        self.assertEqual(result.info["cloudfront"]["prefixes6"], 6)

    def test_provider_table(self):
        self.assertEqual(set(cdn.PROVIDERS), {"cloudflare", "fastly", "cloudfront"})


class FallbackTest(CdnTestBase):
    def test_cdn_html_falls_back_to_snapshot(self):
        html = "<html><body>Just a moment...</body></html>"
        result, err = self.fetch({"https://www.cloudflare.com/ips-v4": html})
        self.assertEqual(result.info["cloudflare"],
                         {"source": "snapshot", "prefixes4": 6, "prefixes6": 1})
        self.assertIn("cloudflare", err)
        self.assertIn(ip_network("201.0.0.0/24"), result.networks)
        self.assertEqual(result.info["fastly"]["source"], "live")
        self.assertTrue(any("cloudflare" in w for w in result.warnings))

    def test_cdn_rejects_too_wide(self):
        for wide in ("0.0.0.0/0", "10.0.0.0/7"):
            text = _text("cdn_cloudflare_v4.txt") + "\n" + wide + "\n"
            result, _ = self.fetch({"https://www.cloudflare.com/ips-v4": text})
            self.assertEqual(result.info["cloudflare"]["source"], "snapshot", wide)
            self.assertNotIn(ip_network(wide), result.networks)
        text = _text("cdn_cloudflare_v6.txt") + "\n::/0\n"
        result, _ = self.fetch({"https://www.cloudflare.com/ips-v6": text})
        self.assertEqual(result.info["cloudflare"]["source"], "snapshot")
        self.assertNotIn(ip_network("::/0"), result.networks)

    def test_cdn_accepts_exact_limits(self):
        text = _text("cdn_cloudflare_v4.txt") + "\n20.0.0.0/8\n"
        result, _ = self.fetch({"https://www.cloudflare.com/ips-v4": text})
        self.assertEqual(result.info["cloudflare"]["source"], "live")
        text = _text("cdn_cloudflare_v6.txt") + "\n2000::/16\n"
        result, _ = self.fetch({"https://www.cloudflare.com/ips-v6": text})
        self.assertEqual(result.info["cloudflare"]["source"], "live")

    def test_cdn_too_few_ipv4_rejected(self):
        few = "\n".join(_text("cdn_cloudflare_v4.txt").splitlines()[:4])
        result, _ = self.fetch({"https://www.cloudflare.com/ips-v4": few})
        self.assertEqual(result.info["cloudflare"]["source"], "snapshot")

    def test_cdn_no_ipv6_rejected(self):
        result, _ = self.fetch({"https://www.cloudflare.com/ips-v6": ""})
        self.assertEqual(result.info["cloudflare"]["source"], "snapshot")

    def test_cdn_network_error_falls_back(self):
        result, _ = self.fetch({"https://api.fastly.com/public-ip-list": OSError("down")})
        self.assertEqual(result.info["fastly"]["source"], "snapshot")

    def test_cdn_bad_json_falls_back(self):
        result, _ = self.fetch({"https://ip-ranges.amazonaws.com/ip-ranges.json": "{}"})
        self.assertEqual(result.info["cloudfront"]["source"], "snapshot")

    def test_cdn_missing_everywhere_warns_and_continues(self):
        down = {u: OSError("down") for u in URL_FILES}
        empty = self.tmp / "none.txt"
        result, err = self.fetch(down, snapshot=empty)
        self.assertEqual(result.networks, [])
        self.assertEqual(result.info, {n: {"source": "missing", "prefixes4": 0, "prefixes6": 0}
                                       for n in cdn.PROVIDERS})
        self.assertEqual(len(result.warnings), 3)
        for name in cdn.PROVIDERS:
            self.assertIn(name, err)

    def test_invalid_snapshot_block_counts_as_missing(self):
        self.snapshot.write_text("# provider cloudflare 2026-01-01\n0.0.0.0/0\n")
        down = {u: OSError("down") for u in URL_FILES}
        result, _ = self.fetch(down)
        self.assertEqual(result.info["cloudflare"]["source"], "missing")

    def test_default_fetcher_retries_then_falls_back(self):
        err = urllib.error.URLError("down")
        with mock.patch("mbl.fetch.urllib.request.urlopen", side_effect=err) as urlopen, \
                contextlib.redirect_stderr(io.StringIO()):
            result = cdn.fetch_cdn(self.snapshot, backoff=0)
        self.assertEqual(urlopen.call_count, 9)  # 3 attempts for each of 3 providers
        self.assertEqual({i["source"] for i in result.info.values()}, {"snapshot"})


class SnapshotTest(CdnTestBase):
    def test_committed_snapshot_has_every_provider_and_is_valid(self):
        text = COMMITTED_SNAPSHOT.read_text(encoding="utf-8")
        blocks = cdn.read_snapshot(COMMITTED_SNAPSHOT)
        self.assertEqual(set(blocks), set(cdn.PROVIDERS))
        for name in cdn.PROVIDERS:
            self.assertIn(f"# provider {name} ", text)
            nets = blocks[name]
            # read_snapshot drops an invalid block; validate again explicitly
            cdn._validate([n for n in nets if n.version == 4], [n for n in nets if n.version == 6])

    def test_read_snapshot_not_utf8_is_empty(self):
        self.snapshot.write_bytes(b"# provider cloudflare 2026-01-01\n\xff\xfe\x00bad\n")
        self.assertEqual(cdn.read_snapshot(self.snapshot), {})
        down = {u: OSError("down") for u in URL_FILES}
        result, _ = self.fetch(down)
        self.assertEqual({i["source"] for i in result.info.values()}, {"missing"})

    def test_old_snapshot_block_warns(self):
        # the fixture snapshot is dated 2026-01-01: 91 days before 2026-04-02
        result, err = self.fetch({"https://api.fastly.com/public-ip-list": OSError("down")},
                                 today=date(2026, 4, 2))
        self.assertEqual(result.info["fastly"]["source"], "snapshot")
        self.assertTrue(any("fastly" in w and "91 days old" in w for w in result.warnings),
                        result.warnings)
        self.assertIn("91 days old", err)
        self.assertFalse(any("cloudflare" in w for w in result.warnings))

    def test_recent_snapshot_block_does_not_warn_about_age(self):
        result, err = self.fetch({"https://api.fastly.com/public-ip-list": OSError("down")},
                                 today=date(2026, 4, 1))  # exactly 90 days
        self.assertEqual(result.info["fastly"]["source"], "snapshot")
        self.assertEqual(len(result.warnings), 1)
        self.assertNotIn("days old", err)

    def test_undated_snapshot_block_warns(self):
        self.snapshot.write_text(_snapshot_text().replace("# provider fastly 2026-01-01",
                                                          "# provider fastly"))
        result, _ = self.fetch({"https://api.fastly.com/public-ip-list": OSError("down")},
                               today=date(2026, 1, 2))
        self.assertEqual(result.info["fastly"]["source"], "snapshot")
        self.assertTrue(any("fastly" in w and "unknown age" in w for w in result.warnings),
                        result.warnings)

    def test_write_snapshot_roundtrip(self):
        live, _ = self.fetch()
        blocks = cdn.fetch_live(_fetcher())
        out = self.tmp / "snap.txt"
        cdn.write_snapshot(out, blocks, date(2026, 10, 21))
        text = out.read_text()
        self.assertTrue(text.startswith("# provider cloudflare 2026-10-21\n"))
        self.assertIn("# provider fastly 2026-10-21\n", text)
        self.assertIn("# provider cloudfront 2026-10-21\n", text)
        result, _ = self.fetch({u: OSError("down") for u in URL_FILES}, snapshot=out)
        self.assertEqual({i["source"] for i in result.info.values()}, {"snapshot"})
        self.assertEqual(sorted(map(str, result.networks)), sorted(map(str, live.networks)))

    def test_cdn_snapshot_cli(self):
        out = self.tmp / "cli.txt"
        blocks = cdn.fetch_live(_fetcher())
        with mock.patch("mbl.__main__.fetch_live", return_value=blocks), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["cdn-snapshot", "--out", str(out)]), 0)
        self.assertIn("# provider cloudflare ", out.read_text())

    def test_cdn_snapshot_cli_failure_writes_nothing(self):
        out = self.tmp / "cli.txt"
        with mock.patch("mbl.__main__.fetch_live", side_effect=ValueError("cloudflare: bad")), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(["cdn-snapshot", "--out", str(out)]), 1)
        self.assertFalse(out.exists())


PUBLISHED_URL = "https://example.invalid/lists/cdn-snapshot.txt"


def _published_text():
    return (
        "# provider cloudflare 2026-10-20\n"
        + "".join(f"31{i}.0.0.0/24\n".replace("31", "17", 1) for i in range(1, 7))
        + "2606:4700::/32\n"
        + "# provider fastly 2026-09-01\n"
        + "".join(f"18{i}.0.0.0/24\n" for i in range(1, 7))
        + "2a04:4e40::/32\n"
    )


class PublishedTest(CdnTestBase):
    TODAY = date(2026, 10, 21)

    def fetch_pub(self, overrides=None, published=None, snapshot=None, calls=None):
        """fetch_cdn with a published copy at PUBLISHED_URL (text, or an Exception)."""
        live = _fetcher(overrides)
        calls = calls if calls is not None else []

        def fetch(url):
            calls.append(url)
            if url == PUBLISHED_URL:
                if isinstance(published, Exception):
                    raise published
                return _published_text() if published is None else published
            return live(url)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            result = cdn.fetch_cdn(snapshot or self.snapshot, fetch, backoff=0,
                                   today=self.TODAY, previous_url=PUBLISHED_URL)
        return result, err.getvalue()

    def test_published_preferred_over_committed(self):
        result, err = self.fetch_pub({"https://www.cloudflare.com/ips-v4": OSError("down")})
        self.assertEqual(result.info["cloudflare"],
                         {"source": "published", "prefixes4": 6, "prefixes6": 1})
        self.assertIn(ip_network("171.0.0.0/24"), result.networks)
        self.assertNotIn(ip_network("201.0.0.0/24"), result.networks)
        self.assertEqual(len(result.warnings), 1)
        self.assertIn("yesterday's published copy", result.warnings[0])
        self.assertIn("cloudflare", err)

    def test_provider_absent_from_published_uses_committed(self):
        down = {"https://ip-ranges.amazonaws.com/ip-ranges.json": OSError("down")}
        result, _ = self.fetch_pub(down)
        self.assertEqual(result.info["cloudfront"]["source"], "snapshot")
        self.assertIn("committed snapshot", result.warnings[0])

    def test_unavailable_or_invalid_published_uses_committed(self):
        down = {"https://www.cloudflare.com/ips-v4": OSError("down")}
        not_found = urllib.error.HTTPError(PUBLISHED_URL, 404, "Not Found", {}, None)
        for published in (not_found, OSError("down"), "<html>nope</html>",
                          "# provider cloudflare 2026-10-20\n0.0.0.0/0\n"):
            result, _ = self.fetch_pub(down, published=published)
            self.assertEqual(result.info["cloudflare"]["source"], "snapshot", published)
            self.assertIn(ip_network("201.0.0.0/24"), result.networks)

    def test_neither_published_nor_committed_is_missing(self):
        down = {"https://www.cloudflare.com/ips-v4": OSError("down")}
        result, _ = self.fetch_pub(down, published=OSError("down"),
                                   snapshot=self.tmp / "none.txt")
        self.assertEqual(result.info["cloudflare"]["source"], "missing")
        self.assertNotIn("cloudflare", result.blocks)

    def test_published_fetched_once_and_only_when_needed(self):
        calls = []
        self.fetch_pub(calls=calls)
        self.assertNotIn(PUBLISHED_URL, calls)
        calls = []
        self.fetch_pub({u: OSError("down") for u in URL_FILES}, calls=calls)
        self.assertEqual(calls.count(PUBLISHED_URL), 1)
        calls = []
        self.fetch_pub({u: OSError("down") for u in URL_FILES}, published=OSError("x"),
                       calls=calls)
        self.assertEqual(calls.count(PUBLISHED_URL), 1)

    def test_old_published_block_warns(self):
        # fastly in the published copy is dated 2026-09-01; 91 days later
        self.TODAY = date(2026, 12, 1)
        result, _ = self.fetch_pub({"https://api.fastly.com/public-ip-list": OSError("down")})
        self.assertEqual(result.info["fastly"]["source"], "published")
        self.assertIn("91 days old", result.warnings[0])

    def test_never_raises(self):
        def fetch(url):
            raise RuntimeError("boom")
        with contextlib.redirect_stderr(io.StringIO()):
            result = cdn.fetch_cdn(self.tmp / "none.txt", fetch, backoff=0,
                                   previous_url=PUBLISHED_URL)
        self.assertEqual({i["source"] for i in result.info.values()}, {"missing"})

    def test_blocks_and_dates(self):
        down = {"https://www.cloudflare.com/ips-v4": OSError("down"),
                "https://ip-ranges.amazonaws.com/ip-ranges.json": OSError("down")}
        result, _ = self.fetch_pub(down, snapshot=self.tmp / "none.txt")
        self.assertEqual(set(result.blocks), {"cloudflare", "fastly"})
        self.assertEqual(result.dates, {"cloudflare": date(2026, 10, 20),
                                        "fastly": self.TODAY})
        self.assertEqual(sorted(str(n) for b in result.blocks.values() for n in b),
                         sorted(map(str, result.networks)))

    def test_written_snapshot_dates_and_roundtrip(self):
        # cloudflare from the published copy (2026-10-20), fastly live (today),
        # cloudfront missing everywhere
        down = {"https://www.cloudflare.com/ips-v4": OSError("down"),
                "https://ip-ranges.amazonaws.com/ip-ranges.json": OSError("down")}
        result, _ = self.fetch_pub(down, snapshot=self.tmp / "none.txt")
        out = self.tmp / "cdn-snapshot.txt"
        cdn.write_snapshot(out, result.blocks, result.dates)
        text = out.read_text()
        self.assertTrue(text.startswith("# provider cloudflare 2026-10-20\n"))
        self.assertIn("# provider fastly 2026-10-21\n", text)
        self.assertNotIn("cloudfront", text)
        blocks, dates = cdn._read_snapshot(out)
        self.assertEqual(blocks, result.blocks)
        self.assertEqual(dates, result.dates)

    def test_write_snapshot_undated_block_stays_undated(self):
        out = self.tmp / "snap.txt"
        blocks = cdn.read_snapshot(self.snapshot)
        cdn.write_snapshot(out, blocks, {"cloudflare": None, "fastly": date(2026, 1, 1),
                                         "cloudfront": date(2026, 1, 1)})
        self.assertTrue(out.read_text().startswith("# provider cloudflare\n"))
        self.assertIsNone(cdn._read_snapshot(out)[1]["cloudflare"])


if __name__ == "__main__":
    unittest.main()
