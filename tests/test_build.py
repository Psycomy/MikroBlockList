import contextlib
import gzip
import io
import json
import tempfile
import unittest
from datetime import date, datetime, timezone
from ipaddress import IPv4Network, IPv6Network
from pathlib import Path
from unittest import mock

from mbl.__main__ import main
from mbl.build import _previous_stats, _previous_totals, build
from mbl.cdn import CdnResult, read_snapshot
from mbl.fetch import SourceResult
from mbl.sources import Source

WHEN = datetime(2026, 10, 19, 4, 17, 0, tzinfo=timezone.utc)
SOURCES_TOML = """
[[source]]
name = "main"
url = "https://example.invalid/main"
format = "plain"
category = "attack"
required = true
variants = ["lite", "full"]
"""
FILES = {"blocklist.txt", "stats.json",
         "badge-entries.json", "badge-updated.json", "NOTICE.md", "routeros-full.txt",
         "routeros-lite.txt", "blocklist-lite.txt", "lookup.json", "cdn-snapshot.txt"}


NO_CDN = CdnResult([], {})

# Production IPv4 limits (full 5000..150000, lite 1000..20000) scaled down 10x, so the
# fixtures only need hundreds of entries instead of thousands. The checks themselves are
# unchanged; IPv6 limits are small already and stay as in production.
TEST_LIMITS = {"full": (500, 15000), "lite": (100, 2000)}


def setUpModule():
    # Builds never touch the network for the CDN lists unless a test passes its own.
    for patcher in (mock.patch("mbl.build.fetch_cdn", lambda snapshot, **kw: NO_CDN),
                    mock.patch("mbl.build.LIMITS", TEST_LIMITS)):
        patcher.start()
        unittest.addModuleCleanup(patcher.stop)


def _nets(count):
    return [IPv4Network(f"1.{i // 256}.{i % 256}.1/32") for i in range(count)]


def _fetcher(count=600, error=None):
    def fetch(sources):
        src = sources[0]
        if error:
            return [SourceResult(src, [], 0, error)]
        return [SourceResult(src, _nets(count), 0, None)]
    return fetch


SOURCES6_TOML = SOURCES_TOML + """
[[source]]
name = "v6"
url = "https://example.invalid/v6"
format = "plain"
category = "attack"
variants = ["lite", "full"]
"""
FILES6 = {"routeros6-full.txt", "routeros6-lite.txt", "blocklist6.txt", "blocklist6-lite.txt"}


def _nets6(count):
    return [IPv6Network(f"2a00:{i:x}::1/128") for i in range(1, count + 1)]


def _fetcher6(count6=100, count=600):
    def fetch(sources):
        return [SourceResult(sources[0], _nets(count), 0, None),
                SourceResult(sources[1], _nets6(count6), 0, None)]
    return fetch


class BuildBase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.sources = self.tmp / "sources.toml"
        self.sources.write_text(SOURCES_TOML)
        self.allow = self.tmp / "allowlist.txt"
        self.allow.write_text("# none\n")
        self.out = self.tmp / "dist"
        self.version = self.tmp / "VERSION"
        self.version.write_text("3.0.0\n")

    def _build(self, fetcher, previous=None, sources=None, **kwargs):
        kwargs.setdefault("version_path", self.version)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return build(sources or self.sources, self.allow, self.out, previous,
                         now=WHEN, fetcher=fetcher, **kwargs)


class BuildTest(BuildBase):
    def test_build_writes_outputs(self):
        self.assertEqual(self._build(_fetcher()), 0)
        self.assertEqual({p.name for p in self.out.iterdir()}, FILES)

    def test_build_writes_lookup(self):
        self.assertEqual(self._build(_fetcher()), 0)
        data = json.loads((self.out / "lookup.json").read_text(encoding="utf-8"))
        self.assertEqual(data["format"], 1)
        self.assertEqual(len(data["v4"]["full"]), 600)
        self.assertIsNone(data["dbip_month"])

    def test_build_failure_isolated(self):
        self.out.mkdir()
        (self.out / "lookup.json").write_text("stale")
        err = io.StringIO()
        with mock.patch("mbl.build.lookup_data", side_effect=RuntimeError("bad")), \
                contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = build(self.sources, self.allow, self.out, None, now=WHEN,
                         fetcher=_fetcher(), version_path=self.version)
        self.assertEqual(code, 0)
        self.assertTrue((self.out / "blocklist.txt").exists())
        self.assertTrue((self.out / "routeros-full.txt").exists())
        self.assertFalse((self.out / "lookup.json").exists())
        self.assertIn("lookup.json not published", err.getvalue())

    def test_build_bad_dbip_publishes_without_countries(self):
        bad = self.tmp / "dbip-country-lite-2026-10.csv.gz"
        bad.write_text("not gzip")
        for path in (bad, self.tmp / "missing.csv.gz"):
            err = io.StringIO()
            with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                code = build(self.sources, self.allow, self.out, None, now=WHEN,
                             fetcher=_fetcher(), version_path=self.version, dbip_path=path)
            self.assertEqual(code, 0)
            data = json.loads((self.out / "lookup.json").read_text(encoding="utf-8"))
            self.assertIsNone(data["dbip_month"])
            self.assertEqual({e[2] for e in data["v4"]["full"]}, {""})
            self.assertIn("DB-IP unavailable", err.getvalue())
            self.assertIn("lookup.json without countries", err.getvalue())

    def test_build_with_dbip(self):
        dbip = self.tmp / "dbip-country-lite-2026-10.csv.gz"
        with gzip.open(dbip, "wt", encoding="utf-8") as fh:
            fh.write("1.0.0.0,1.255.255.255,DE\n")
        self.assertEqual(self._build(_fetcher(), dbip_path=dbip), 0)
        data = json.loads((self.out / "lookup.json").read_text(encoding="utf-8"))
        self.assertEqual(data["dbip_month"], "2026-10")
        self.assertEqual({e[2] for e in data["v4"]["full"]}, {"DE"})

    def test_build_refuses_on_required_failure(self):
        self.assertEqual(self._build(_fetcher(error="boom")), 1)
        self.assertFalse((self.out / "blocklist.txt").exists())

    def test_build_skips_drop_check_without_previous(self):
        self.assertEqual(self._build(_fetcher(), previous=None), 0)

    def test_build_refuses_below_minimum(self):
        self.assertEqual(self._build(_fetcher(count=10)), 1)
        self.assertFalse(self.out.exists())

    def test_build_bad_config_returns_2(self):
        self.sources.write_text('[[source]]\nname = "x"\n')
        self.assertEqual(self._build(_fetcher()), 2)
        self.assertEqual(self._build(_fetcher(), sources=self.tmp / "missing.toml"), 2)

    def test_build_unreachable_previous_is_ignored(self):
        self.assertEqual(self._build(_fetcher(), previous="http://127.0.0.1:9/stats.json"), 0)

    @mock.patch("mbl.build._previous_stats", return_value=({"full": 600, "lite": 600}, {}))
    def test_build_refuses_growth_above_double(self, _prev):
        self.assertEqual(self._build(_fetcher(count=1201), previous="x"), 1)
        self.assertFalse(self.out.exists())

    @mock.patch("mbl.build._previous_stats", return_value=({"full": 600, "lite": 600}, {}))
    def test_build_allow_growth_publishes(self, _prev):
        self.assertEqual(self._build(_fetcher(count=1201), previous="x", allow_growth=True), 0)
        self.assertEqual({p.name for p in self.out.iterdir()}, FILES)

    def test_build_splits_by_variant(self):
        two = SOURCES_TOML + """
[[source]]
name = "extra"
url = "https://example.invalid/extra"
format = "plain"
category = "attack"
"""
        self.sources.write_text(two)

        def fetch(sources):
            main, extra = sources
            return [SourceResult(main, _nets(200), 0, None),
                    SourceResult(extra, [IPv4Network(f"9.{i // 256}.{i % 256}.1/32")
                                         for i in range(400)], 0, None)]
        self.assertEqual(self._build(fetch), 0)
        lite = (self.out / "blocklist-lite.txt").read_text().splitlines()
        full = (self.out / "blocklist.txt").read_text().splitlines()
        self.assertEqual((len(lite), len(full)), (200, 600))
        self.assertTrue(all(line.startswith("1.") for line in lite))

    def test_build_refuses_when_lite_fails(self):
        two = SOURCES_TOML.replace('variants = ["lite", "full"]\n', "") + """
[[source]]
name = "small"
url = "https://example.invalid/small"
format = "plain"
category = "attack"
variants = ["lite", "full"]
"""
        self.sources.write_text(two)

        def fetch(sources):
            main, small = sources
            return [SourceResult(main, _nets(600), 0, None),
                    SourceResult(small, [IPv4Network(f"9.0.{i // 256}.{i % 256}/32")
                                         for i in range(50)], 0, None)]
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = build(self.sources, self.allow, self.out, None, now=WHEN,
                         fetcher=fetch, version_path=self.version)
        self.assertEqual(code, 1)
        self.assertFalse(self.out.exists())
        self.assertTrue(err.getvalue().startswith("error: lite:"), err.getvalue())

    @mock.patch("mbl.build._previous_stats", return_value=({"full": 2000}, {}))
    def test_first_lite_publish_skips_drop_and_growth(self, _prev):
        # lite has no previous total; compared against full's 2000, 300 would be an 85% drop
        two = SOURCES_TOML.replace('variants = ["lite", "full"]', 'variants = ["full"]') + """
[[source]]
name = "small"
url = "https://example.invalid/small"
format = "plain"
category = "attack"
variants = ["lite", "full"]
"""
        self.sources.write_text(two)

        def fetch(sources):
            main, small = sources
            return [SourceResult(main, _nets(1200), 0, None),
                    SourceResult(small, [IPv4Network(f"9.{i // 256}.{i % 256}.1/32")
                                         for i in range(300)], 0, None)]
        self.assertEqual(self._build(fetch, previous="x"), 0)
        self.assertEqual(len((self.out / "blocklist-lite.txt").read_text().splitlines()), 300)

    LITE_PAIR = SOURCES_TOML.replace('variants = ["lite", "full"]', 'variants = ["full"]') + """
[[source]]
name = "lite-a"
url = "https://example.invalid/a"
format = "plain"
category = "attack"
variants = ["lite", "full"]

[[source]]
name = "lite-b"
url = "https://example.invalid/b"
format = "plain"
category = "attack"
variants = ["lite", "full"]
"""

    def _lite_drop(self, previous_stats, b_error="boom"):
        """full 750 (prev 1050, -29%); lite 150 (prev 450, -67%); lite-b failing."""
        self.sources.write_text(self.LITE_PAIR)

        def fetch(sources):
            main, a, b = sources
            return [SourceResult(main, _nets(600), 0, None),
                    SourceResult(a, [IPv4Network(f"9.{i // 256}.{i % 256}.1/32")
                                     for i in range(150)], 0, None),
                    SourceResult(b, [], 0, b_error)]
        err = io.StringIO()
        with mock.patch("mbl.build._previous_stats", return_value=previous_stats), \
                contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = build(self.sources, self.allow, self.out, "x", now=WHEN,
                         fetcher=fetch, version_path=self.version)
        return code, err.getvalue()

    def test_drop_explained_by_failed_optional_source_publishes(self):
        code, err = self._lite_drop(({"full": 1050, "lite": 450},
                                     {"lite-a": 150, "lite-b": 300}))
        self.assertEqual(code, 0, err)
        self.assertTrue((self.out / "blocklist-lite.txt").exists())
        self.assertIn("lite: drop of 300 explained by failed sources (lite-b), "
                      "drop check skipped", err)

    def test_failed_optional_source_without_previous_lite_total(self):
        code, err = self._lite_drop(({"full": 1050}, {"lite-a": 150, "lite-b": 300}))
        self.assertEqual(code, 0, err)
        self.assertTrue((self.out / "blocklist-lite.txt").exists())

    def test_failed_optional_source_without_previous_stats(self):
        code, err = self._lite_drop(({}, {}))
        self.assertEqual(code, 0, err)
        self.assertTrue((self.out / "blocklist-lite.txt").exists())

    def test_drop_larger_than_explained_refuses(self):
        code, err = self._lite_drop(({"full": 1050, "lite": 450},
                                     {"lite-a": 150, "lite-b": 299}))
        self.assertEqual(code, 1)
        self.assertFalse(self.out.exists())
        self.assertIn("error: lite:", err)

    def test_drop_without_failed_sources_refuses(self):
        code, err = self._lite_drop(({"full": 1050, "lite": 450},
                                     {"lite-a": 150, "lite-b": 300}), b_error=None)
        self.assertEqual(code, 1)
        self.assertFalse(self.out.exists())
        self.assertIn("error: lite:", err)
        self.assertNotIn("error: full:", err)

    def test_drop_without_previous_source_entries_refuses(self):
        code, err = self._lite_drop(({"full": 1050, "lite": 450}, {}))
        self.assertEqual(code, 1)
        self.assertIn("error: lite:", err)

    def test_required_failure_counts_only_for_its_variants(self):
        two = SOURCES_TOML + """
[[source]]
name = "big"
url = "https://example.invalid/big"
format = "plain"
category = "attack"
required = true
"""
        self.sources.write_text(two)

        def fetch(sources):
            main, big = sources
            return [SourceResult(main, _nets(600), 0, None), SourceResult(big, [], 0, "boom")]
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = build(self.sources, self.allow, self.out, None, now=WHEN,
                         fetcher=fetch, version_path=self.version)
        self.assertEqual(code, 1)
        self.assertIn("error: full: required sources failed: big", err.getvalue())
        self.assertNotIn("lite: required", err.getvalue())

    def _stats(self, payload):
        resp = io.BytesIO(json.dumps(payload).encode())
        resp.__enter__ = lambda self=resp: self
        resp.__exit__ = lambda *a: None
        return mock.patch("urllib.request.urlopen", return_value=resp)

    def test_previous_full_from_top_level_total(self):
        with self._stats({"total": 7000}):
            self.assertEqual(_previous_totals("http://x"), {"full": 7000})

    def test_previous_totals_per_variant(self):
        with self._stats({"total": 7000, "variants": {"full": {"total": 7001},
                                                      "lite": {"total": 3000}}}):
            self.assertEqual(_previous_totals("http://x"), {"full": 7001, "lite": 3000})

    def test_previous_malformed_total_skips_only_that_variant(self):
        err = io.StringIO()
        with self._stats({"total": 7000, "variants": {"full": {"total": 7001},
                                                      "lite": {"total": "x"}}}), \
                contextlib.redirect_stderr(err):
            self.assertEqual(_previous_totals("http://x"), {"full": 7001})
        self.assertIn("lite", err.getvalue())

    def test_previous_stats_source_entries(self):
        with self._stats({"total": 7000, "sources": {"a": {"entries": 10}, "b": {"entries": "x"},
                                                     "c": {}, "d": 5}}):
            self.assertEqual(_previous_stats("http://x"), ({"full": 7000}, {"a": 10}))

    def test_previous_stats_without_sources(self):
        with self._stats({"total": 7000}):
            self.assertEqual(_previous_stats("http://x"), ({"full": 7000}, {}))

    def _build_err(self, fetcher, **kwargs):
        kwargs.setdefault("version_path", self.version)
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            code = build(self.sources, self.allow, self.out, kwargs.pop("previous", None),
                         now=WHEN, fetcher=fetcher, **kwargs)
        return code, err.getvalue()

    def test_writes_ipv6_files(self):
        self.sources.write_text(SOURCES6_TOML)
        self.assertEqual(self._build(_fetcher6()), 0)
        self.assertEqual({p.name for p in self.out.iterdir()}, FILES | FILES6)
        self.assertIn("family=ipv6", (self.out / "routeros6-full.txt").read_text().splitlines()[0])

    def test_ipv6_failure_does_not_block_ipv4(self):
        self.sources.write_text(SOURCES6_TOML)
        code, err = self._build_err(_fetcher6(count6=10))
        self.assertEqual(code, 0)
        self.assertEqual({p.name for p in self.out.iterdir()}, FILES)
        stats = json.loads((self.out / "stats.json").read_text())
        self.assertFalse(stats["ipv6"]["published"])
        self.assertIn("ipv6", err)
        self.assertIn("below the minimum", err)

    def test_ipv6_failure_is_reported_in_step_summary(self):
        self.sources.write_text(SOURCES6_TOML)
        summary = self.tmp / "summary.md"
        with mock.patch.dict("os.environ", {"GITHUB_STEP_SUMMARY": str(summary)}):
            self.assertEqual(self._build(_fetcher6(count6=10)), 0)
        self.assertIn("IPv6", summary.read_text())

    def test_ipv6_lite_failure_withholds_all_ipv6(self):
        self.sources.write_text(SOURCES6_TOML)
        self.assertEqual(self._build(_fetcher6(count6=19)), 0)  # full needs 50, lite 20
        self.assertEqual({p.name for p in self.out.iterdir()}, FILES)

    @mock.patch("mbl.build._previous_stats", return_value=(
        {"full": 600, "lite": 600, "ipv6:full": 100, "ipv6:lite": 100}, {}))
    def test_ipv6_drop_and_growth_withhold_ipv6_only(self, _prev):
        self.sources.write_text(SOURCES6_TOML)
        self.assertEqual(self._build(_fetcher6(count6=40), previous="x"), 0)
        self.assertEqual({p.name for p in self.out.iterdir()}, FILES)
        self.assertEqual(self._build(_fetcher6(count6=201), previous="x"), 0)
        self.assertEqual({p.name for p in self.out.iterdir()}, FILES)

    @mock.patch("mbl.build._previous_stats", return_value=(
        {"full": 600, "lite": 600, "ipv6:full": 100, "ipv6:lite": 100},
        {"v6": 50, "ipv6:v6": 60}))
    def test_ipv6_explained_drop_uses_ipv6_entries(self, _prev):
        self.sources.write_text(SOURCES6_TOML + """
[[source]]
name = "v6b"
url = "https://example.invalid/v6b"
format = "plain"
category = "attack"
variants = ["lite", "full"]
""")

        def fetch(sources):
            return [SourceResult(sources[0], _nets(600), 0, None),
                    SourceResult(sources[1], _nets6(40), 0, None),
                    SourceResult(sources[2], [], 0, "boom")]
        # v6b is missing from previous entries -> explains 0 -> 100 -> 40 is a >50% drop
        self.assertEqual(self._build(fetch, previous="x"), 0)
        self.assertEqual({p.name for p in self.out.iterdir()}, FILES)

    @mock.patch("mbl.build._previous_stats", return_value=(
        {"full": 600, "lite": 600, "ipv6:full": 200, "ipv6:lite": 200},
        {"v6b": 150, "ipv6:v6b": 150, "ipv6:v6": 50}))
    def test_ipv6_explained_drop_publishes(self, _prev):
        self.sources.write_text(SOURCES6_TOML + """
[[source]]
name = "v6b"
url = "https://example.invalid/v6b"
format = "plain"
category = "attack"
variants = ["lite", "full"]
""")

        def fetch(sources):
            return [SourceResult(sources[0], _nets(600), 0, None),
                    SourceResult(sources[1], _nets6(60), 0, None),
                    SourceResult(sources[2], [], 0, "boom")]
        # 200 -> 60 is a >50% drop, explained by v6b's 150 previous IPv6 entries
        self.assertEqual(self._build(fetch, previous="x"), 0)
        self.assertEqual({p.name for p in self.out.iterdir()}, FILES | FILES6)

    def test_ipv6_stats_and_previous_totals(self):
        self.sources.write_text(SOURCES6_TOML)
        self.assertEqual(self._build(_fetcher6(count6=100)), 0)
        stats = json.loads((self.out / "stats.json").read_text())
        self.assertEqual(stats["ipv6"], {"published": True, "variants": {
            "full": {"total": 100}, "lite": {"total": 100}}})
        self.assertEqual(stats["sources"]["main"]["entries"], 600)
        self.assertEqual(stats["sources"]["main"]["entries6"], 0)
        self.assertEqual(stats["sources"]["v6"]["entries"], 0)
        self.assertEqual(stats["sources"]["v6"]["entries6"], 100)
        with self._stats(stats):
            totals, entries = _previous_stats("http://x")
        self.assertEqual(totals, {"full": 600, "lite": 600, "ipv6:full": 100, "ipv6:lite": 100})
        self.assertEqual(entries["ipv6:v6"], 100)
        self.assertEqual(entries["v6"], 0)

    def test_previous_without_ipv6_section_has_no_ipv6_totals(self):
        with self._stats({"total": 7000, "variants": {"full": {"total": 7000}}}):
            self.assertEqual(_previous_totals("http://x"), {"full": 7000})

    def test_ipv4_outputs_unchanged_by_ipv6(self):
        self.assertEqual(self._build(_fetcher()), 0)
        before = {n: (self.out / n).read_bytes()
                  for n in FILES - {"stats.json", "NOTICE.md", "lookup.json"}}
        self.sources.write_text(SOURCES6_TOML)
        self.assertEqual(self._build(_fetcher6()), 0)
        for name, data in before.items():
            self.assertEqual((self.out / name).read_bytes(), data, name)

    def _chain(self, counts6):
        """Run one build per IPv6 count, each reading the previous run's stats.json."""
        self.sources.write_text(SOURCES6_TOML)
        published = []
        for i, count6 in enumerate(counts6):
            previous = None
            if i:
                stats = json.loads((self.out / "stats.json").read_text())
                for name in self.out.iterdir():
                    name.unlink()
                ctx = self._stats(stats)
                previous = "http://x"
            else:
                ctx = contextlib.nullcontext()
            with ctx:
                self.assertEqual(self._build(_fetcher6(count6=count6), previous=previous), 0)
            stats = json.loads((self.out / "stats.json").read_text())
            published.append((stats["ipv6"]["published"], stats["ipv6"]["variants"]["full"]["total"],
                              (self.out / "routeros6-full.txt").exists()))
        return published

    def test_withheld_ipv6_carries_forward_baseline_after_drop(self):
        self.assertEqual(self._chain([648, 100, 100]),
                         [(True, 648, True), (False, 648, False), (False, 648, False)])

    def test_withheld_ipv6_carries_forward_baseline_after_growth(self):
        self.assertEqual(self._chain([100, 300, 300]),
                         [(True, 100, True), (False, 100, False), (False, 100, False)])

    def _assert_ipv6_withheld_on_exception(self, target):
        self.sources.write_text(SOURCES6_TOML)
        summary = self.tmp / "summary.md"
        with mock.patch(target, side_effect=RuntimeError("kaboom")), \
                mock.patch.dict("os.environ", {"GITHUB_STEP_SUMMARY": str(summary)}):
            code, err = self._build_err(_fetcher6())
        self.assertEqual(code, 0)
        self.assertEqual({p.name for p in self.out.iterdir()}, FILES)
        stats = json.loads((self.out / "stats.json").read_text())
        self.assertFalse(stats["ipv6"]["published"])
        self.assertIn("warning: IPv6 not published", err)
        self.assertIn("kaboom", err)
        self.assertIn("kaboom", summary.read_text())

    def test_normalize6_exception_withholds_ipv6(self):
        self._assert_ipv6_withheld_on_exception("mbl.build.normalize6")

    def test_ipv6_render_exception_withholds_ipv6(self):
        self._assert_ipv6_withheld_on_exception("mbl.build.ipv6_files")

    def test_build_reads_version(self):
        self.version.write_text("9.8.7\n")
        self.assertEqual(self._build(_fetcher()), 0)
        header = (self.out / "routeros-full.txt").read_text().splitlines()[0]
        self.assertIn("script=9.8.7", header)

    def test_build_missing_version_returns_2(self):
        missing = self.tmp / "nope"
        self.assertEqual(self._build(_fetcher(), version_path=missing), 2)
        self.assertFalse(self.out.exists())

    def test_build_empty_version_returns_2(self):
        self.version.write_text("  \n")
        self.assertEqual(self._build(_fetcher()), 2)

    def test_build_malformed_version_returns_2(self):
        for bad in ("3.0", "v3.0.0", "3.0.0 x=1", "3.0.0\nentries=5", "3.0.0-rc1"):
            with self.subTest(version=bad):
                self.version.write_text(bad + "\n")
                self.assertEqual(self._build(_fetcher()), 2)
                self.assertFalse(self.out.exists())


class MainTest(unittest.TestCase):
    @mock.patch("mbl.__main__.build", return_value=0)
    def test_allow_growth_flag(self, build_mock):
        self.assertEqual(main(["build"]), 0)
        self.assertFalse(build_mock.call_args.kwargs["allow_growth"])
        self.assertEqual(main(["build", "--allow-growth"]), 0)
        self.assertTrue(build_mock.call_args.kwargs["allow_growth"])

    @mock.patch("mbl.__main__.build", return_value=0)
    def test_dbip_flag(self, build_mock):
        main(["build"])
        self.assertIsNone(build_mock.call_args.kwargs["dbip_path"])
        main(["build", "--dbip", "x/db.csv.gz"])
        self.assertEqual(build_mock.call_args.kwargs["dbip_path"], Path("x/db.csv.gz"))

    @mock.patch("mbl.__main__.build", return_value=0)
    def test_version_file_flag(self, build_mock):
        main(["build"])
        self.assertEqual(build_mock.call_args.kwargs["version_path"], Path("VERSION"))
        main(["build", "--version-file", "x/V"])
        self.assertEqual(build_mock.call_args.kwargs["version_path"], Path("x/V"))


    @mock.patch("mbl.__main__.build", return_value=0)
    def test_previous_cdn_flag(self, build_mock):
        main(["build"])
        self.assertIsNone(build_mock.call_args.kwargs["previous_cdn_url"])
        main(["build", "--previous-cdn", "https://x/lists/cdn-snapshot.txt"])
        self.assertEqual(build_mock.call_args.kwargs["previous_cdn_url"],
                         "https://x/lists/cdn-snapshot.txt")


class CdnAllowlistTest(BuildBase):
    def _cdn_build(self, fetcher, result, **kwargs):
        return self._build(fetcher, cdn_fetcher=lambda snapshot, **kw: result, **kwargs)

    def test_allowlist_cap_fails_build(self):
        # 1.0.0.0/17 covers 128 of the 900 /32 entries: 14.2 %
        self.allow.write_text("1.0.0.0/17\n")
        self.assertEqual(self._build(_fetcher(900)), 1)
        self.assertFalse(self.out.exists())

    def test_allowlist_under_cap_builds(self):
        self.allow.write_text("1.0.0.0/18\n")  # 64 of 900: 7.1 %
        self.assertEqual(self._build(_fetcher(900)), 0)

    def test_cdn_networks_cap_fails_build(self):
        result = CdnResult([IPv4Network("1.0.0.0/17")], {})
        self.assertEqual(self._cdn_build(_fetcher(900), result), 1)

    def test_cdn_networks_are_subtracted(self):
        result = CdnResult([IPv4Network("1.0.0.0/18"), IPv6Network("2a00:1::/32")], {})
        self.assertEqual(self._cdn_build(_fetcher(900), result), 0)
        lines = (self.out / "blocklist.txt").read_text().splitlines()
        self.assertNotIn("1.0.0.1", lines)
        self.assertNotIn("1.0.63.1", lines)
        self.assertIn("1.0.64.1", lines)

    def test_allowlist_cap_ipv6_withholds(self):
        self.sources.write_text(SOURCES6_TOML)
        self.allow.write_text("".join(f"2a00:{i:x}::1\n" for i in range(1, 12)))  # 11 of 100
        self.assertEqual(self._build(_fetcher6(count6=100)), 0)
        self.assertEqual({p.name for p in self.out.iterdir()}, FILES)
        stats = json.loads((self.out / "stats.json").read_text())
        self.assertFalse(stats["ipv6"]["published"])

    def test_allowlist_ipv6_under_cap_publishes(self):
        self.sources.write_text(SOURCES6_TOML)
        self.allow.write_text("".join(f"2a00:{i:x}::1\n" for i in range(1, 6)))  # 5 of 100
        self.assertEqual(self._build(_fetcher6(count6=100)), 0)
        self.assertEqual({p.name for p in self.out.iterdir()}, FILES | FILES6)

    def _build_err(self, fetcher, sources=None):
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            code = build(sources or self.sources, self.allow, self.out, None, now=WHEN,
                         fetcher=fetcher, version_path=self.version)
        return code, err.getvalue()

    @staticmethod
    def _wide_fetcher(count=900, count6=None):
        """count /32 entries plus 5.0.0.0/8: almost every address is in the /8."""
        def fetch(sources):
            results = [SourceResult(sources[0], [*_nets(count), IPv4Network("5.0.0.0/8")],
                                    0, None)]
            if count6 is not None:
                results.append(SourceResult(
                    sources[1], [*_nets6(count6), IPv6Network("2a01::/32")], 0, None))
            return results
        return fetch

    def test_allowlist_cap_counts_entries_touched(self):
        # 1.0.0.0/17 covers 128 of 901 entries (14.2 %) but only 0.0008 % of the addresses
        self.allow.write_text("1.0.0.0/17\n")
        code, err = self._build_err(self._wide_fetcher())
        self.assertEqual(code, 1)
        self.assertFalse(self.out.exists())
        self.assertIn("128 of 901 entries", err)
        self.assertIn("128 of 16778116 addresses", err)

    def test_allowlist_cap_counts_partially_covered_entries(self):
        # 5.0.0.0/24 touches the /8 entry (1 of 901) and removes 256 of its addresses:
        # under the cap on both measures, so the build publishes
        self.allow.write_text("5.0.0.0/24\n")
        code, _ = self._build_err(self._wide_fetcher())
        self.assertEqual(code, 0)
        cap = json.loads((self.out / "stats.json").read_text())["cdn_allowlist"]["cap"]
        self.assertEqual(cap["ipv4"]["entries_touched"], 1)
        self.assertEqual(cap["ipv4"]["addresses_removed"], 256)

    def test_allowlist_cap_ipv6_counts_entries_touched(self):
        self.sources.write_text(SOURCES6_TOML)
        # 11 of the 101 IPv6 entries (10.9 %), a negligible share of the addresses
        self.allow.write_text("".join(f"2a00:{i:x}::1\n" for i in range(1, 12)))
        code, err = self._build_err(self._wide_fetcher(count6=100))
        self.assertEqual(code, 0)
        self.assertEqual({p.name for p in self.out.iterdir()}, FILES)
        stats = json.loads((self.out / "stats.json").read_text())
        self.assertFalse(stats["ipv6"]["published"])
        self.assertIn("11 of 101 entries", err)
        cap6 = stats["cdn_allowlist"]["cap"]["ipv6"]
        self.assertEqual((cap6["entries_touched"], cap6["entries"]), (11, 101))
        self.assertEqual(cap6["entries_pct"], 10.89)

    def test_stats_cdn_allowlist(self):
        info = {"cloudflare": {"source": "live", "prefixes4": 15, "prefixes6": 7},
                "fastly": {"source": "snapshot", "prefixes4": 21, "prefixes6": 3},
                "cloudfront": {"source": "missing", "prefixes4": 0, "prefixes6": 0}}
        self.allow.write_text("1.0.0.0/19\n")  # 32 of the 600 /32 entries
        self.assertEqual(self._cdn_build(_fetcher(), CdnResult([], info)), 0)
        stats = json.loads((self.out / "stats.json").read_text())
        cdn_stats = dict(stats["cdn_allowlist"])
        cap = cdn_stats.pop("cap")
        self.assertEqual(cdn_stats, info)
        self.assertEqual(cap["limit_pct"], 10.0)
        self.assertEqual(cap["ipv4"], {"addresses": 600, "addresses_removed": 32,
                                       "addresses_pct": 5.33, "entries": 600,
                                       "entries_touched": 32, "entries_pct": 5.33})

    def test_previous_cdn_url_reaches_fetch_cdn(self):
        seen = {}

        def cdn_fetcher(snapshot, **kwargs):
            seen.update(kwargs, snapshot=snapshot)
            return NO_CDN
        url = "https://x/lists/cdn-snapshot.txt"
        self.assertEqual(self._build(_fetcher(), cdn_fetcher=cdn_fetcher,
                                     previous_cdn_url=url), 0)
        self.assertEqual(seen["previous_url"], url)
        self.assertEqual(self._build(_fetcher(), cdn_fetcher=cdn_fetcher), 0)
        self.assertIsNone(seen["previous_url"])

    def test_build_writes_cdn_snapshot(self):
        nets = [IPv4Network(f"9.{i}.0.0/24") for i in range(6)] + [IPv6Network("2a09::/32")]
        old = [IPv4Network(f"8.{i}.0.0/24") for i in range(6)] + [IPv6Network("2a08::/32")]
        info = {"cloudflare": {"source": "live", "prefixes4": 6, "prefixes6": 1},
                "fastly": {"source": "published", "prefixes4": 6, "prefixes6": 1},
                "cloudfront": {"source": "missing", "prefixes4": 0, "prefixes6": 0}}
        result = CdnResult([*nets, *old], info,
                           blocks={"cloudflare": nets, "fastly": old},
                           dates={"cloudflare": date(2026, 10, 19),
                                  "fastly": date(2026, 9, 1)})
        self.assertEqual(self._cdn_build(_fetcher(), result), 0)
        self.assertEqual({p.name for p in self.out.iterdir()}, FILES)
        text = (self.out / "cdn-snapshot.txt").read_text()
        self.assertEqual(text.splitlines()[0], "# provider cloudflare 2026-10-19")
        self.assertIn("# provider fastly 2026-09-01\n8.0.0.0/24\n", text)
        self.assertNotIn("cloudfront", text)
        self.assertEqual(read_snapshot(self.out / "cdn-snapshot.txt"),
                         {"cloudflare": nets, "fastly": old})

    def test_cdn_snapshot_write_failure_does_not_break_build(self):
        with mock.patch("mbl.build.write_snapshot", side_effect=OSError("disk")):
            self.assertEqual(self._build(_fetcher()), 0)
        self.assertEqual({p.name for p in self.out.iterdir()}, FILES - {"cdn-snapshot.txt"})

    def test_cdn_warnings_go_to_step_summary(self):
        summary = self.tmp / "summary.md"
        result = CdnResult([], {}, ["cloudflare: using snapshot (boom)"])
        with mock.patch.dict("os.environ", {"GITHUB_STEP_SUMMARY": str(summary)}):
            self.assertEqual(self._cdn_build(_fetcher(), result), 0)
        self.assertIn("cloudflare: using snapshot (boom)", summary.read_text())


if __name__ == "__main__":
    unittest.main()
