import json
import tempfile
import unittest
from datetime import datetime, timezone
from ipaddress import IPv4Network, IPv6Network
from pathlib import Path

from mbl.fetch import SourceResult
from mbl.render import ipv6_files, router_txt, write_outputs
from mbl.sources import Source

WHEN = datetime(2026, 10, 19, 4, 17, 0, tzinfo=timezone.utc)


def _results(nets):
    src = Source("spamhaus-drop", "https://x", "plain", "attack", required=True)
    return [SourceResult(src, list(nets), 2, None)]


class RenderTest(unittest.TestCase):
    def _write(self, nets, tmp):
        write_outputs(Path(tmp) / "out", {"full": nets}, _results(nets), WHEN)
        return Path(tmp) / "out"

    def test_router_txt_format(self):
        out = router_txt(["1.2.3.4", "5.6.0.0/16"], "2026-10-21T04:17:00Z", "3.0.0")
        self.assertEqual(out, (
            "# MikroBlockList v3 format=1 variant=full family=ipv4 "
            "generated=2026-10-21T04:17:00Z entries=2 script=3.0.0\n"
            "1.2.3.4\n5.6.0.0/16\n# MBL-END entries=2\n"))

    def test_write_outputs_writes_router_txt(self):
        nets = [IPv4Network("1.10.16.0/20"), IPv4Network("8.8.8.8/32")]
        with tempfile.TemporaryDirectory() as tmp:
            out = self._write(nets, tmp)
            lines = (out / "routeros-full.txt").read_text().splitlines()
            plain = (out / "blocklist.txt").read_text().splitlines()
        self.assertEqual(lines[1:-1], plain)
        self.assertIn("entries=2", lines[0])
        self.assertIn("script=0.0.0", lines[0])
        self.assertEqual(lines[-1], "# MBL-END entries=2")

    def test_write_outputs_writes_both_variants(self):
        full = [IPv4Network("1.10.16.0/20"), IPv4Network("8.8.8.8/32")]
        lite = [IPv4Network("8.8.8.8/32")]
        with tempfile.TemporaryDirectory() as tmp:
            write_outputs(Path(tmp), {"full": full, "lite": lite}, _results(full), WHEN)
            out = Path(tmp)
            names = {p.name for p in out.iterdir()}
            head = (out / "routeros-lite.txt").read_text().splitlines()[0]
            lite_txt = (out / "blocklist-lite.txt").read_text()
        self.assertTrue({"routeros-full.txt", "routeros-lite.txt", "blocklist.txt",
                         "blocklist-lite.txt"} <= names)
        self.assertIn("variant=lite", head)
        self.assertEqual(lite_txt, "8.8.8.8\n")

    def test_stats_variants(self):
        full = [IPv4Network("1.10.16.0/20"), IPv4Network("8.8.8.8/32")]
        lite = [IPv4Network("8.8.8.8/32")]
        with tempfile.TemporaryDirectory() as tmp:
            write_outputs(Path(tmp), {"full": full, "lite": lite}, _results(full), WHEN)
            stats = json.loads((Path(tmp) / "stats.json").read_text())
        self.assertEqual(stats["total"], 2)
        self.assertEqual(stats["variants"], {"full": {"total": 2}, "lite": {"total": 1}})

    def test_writes_ipv6_files(self):
        full = [IPv4Network("8.8.8.8/32")]
        full6 = [IPv6Network("2a00:1::/32"), IPv6Network("2a00:2::1/128")]
        lite6 = [IPv6Network("2a00:2::1/128")]
        with tempfile.TemporaryDirectory() as tmp:
            write_outputs(Path(tmp), {"full": full}, _results(full), WHEN, "3.3.0",
                          variants6={"full": full6, "lite": lite6})
            out = Path(tmp)
            r6 = (out / "routeros6-full.txt").read_text()
            r6l = (out / "routeros6-lite.txt").read_text().splitlines()
            b6 = (out / "blocklist6.txt").read_text()
            b6l = (out / "blocklist6-lite.txt").read_text()
            stats = json.loads((out / "stats.json").read_text())
        self.assertEqual(r6, router_txt(["2a00:1::/32", "2a00:2::1"], "2026-10-19T04:17:00Z",
                                        "3.3.0", variant="full", family="ipv6"))
        self.assertIn("family=ipv6", r6.splitlines()[0])
        self.assertIn("variant=lite", r6l[0])
        self.assertEqual(b6, "2a00:1::/32\n2a00:2::1\n")
        self.assertEqual(b6l, "2a00:2::1\n")
        self.assertEqual(stats["ipv6"], {"published": True, "variants": {
            "full": {"total": 2}, "lite": {"total": 1}}})

    def test_no_ipv6_files_without_variants6(self):
        full = [IPv4Network("8.8.8.8/32")]
        with tempfile.TemporaryDirectory() as tmp:
            write_outputs(Path(tmp), {"full": full}, _results(full), WHEN)
            names = {p.name for p in Path(tmp).iterdir()}
            stats = json.loads((Path(tmp) / "stats.json").read_text())
        self.assertFalse([n for n in names if "6" in n])
        self.assertEqual(stats["ipv6"], {"published": False, "variants": {}})

    def test_withheld_ipv6_keeps_baseline_totals(self):
        full = [IPv4Network("8.8.8.8/32")]
        with tempfile.TemporaryDirectory() as tmp:
            write_outputs(Path(tmp), {"full": full}, _results(full), WHEN,
                          ipv6_baseline={"full": 648, "lite": 120})
            names = {p.name for p in Path(tmp).iterdir()}
            stats = json.loads((Path(tmp) / "stats.json").read_text())
        self.assertFalse([n for n in names if "6" in n])
        self.assertEqual(stats["ipv6"], {"published": False, "variants": {
            "full": {"total": 648}, "lite": {"total": 120}}})

    def test_precomputed_ipv6_files_are_written(self):
        full = [IPv4Network("8.8.8.8/32")]
        full6 = [IPv6Network("2a00:1::/32")]
        files6 = ipv6_files({"full": full6}, WHEN, "3.3.0")
        self.assertEqual(set(files6), {"blocklist6.txt", "routeros6-full.txt"})
        with tempfile.TemporaryDirectory() as tmp:
            write_outputs(Path(tmp), {"full": full}, _results(full), WHEN, "3.3.0",
                          variants6={"full": full6}, files6=files6)
            self.assertEqual((Path(tmp) / "blocklist6.txt").read_text(), "2a00:1::/32\n")

    def test_source_entries_count_only_ipv4(self):
        src = Source("mix", "https://x", "plain", "attack")
        res = [SourceResult(src, [IPv4Network("8.8.8.8/32"), IPv6Network("2a00::1/128"),
                                  IPv6Network("2a00::2/128")], 0, None)]
        with tempfile.TemporaryDirectory() as tmp:
            write_outputs(Path(tmp), {"full": []}, res, WHEN)
            stats = json.loads((Path(tmp) / "stats.json").read_text())
        self.assertEqual(stats["sources"]["mix"]["entries"], 1)
        self.assertEqual(stats["sources"]["mix"]["entries6"], 2)

    def test_write_outputs_requires_full(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                write_outputs(Path(tmp), {"lite": []}, [], WHEN)

    def test_blocklist_txt_has_no_header(self):
        nets = [IPv4Network("8.8.8.8/32")]
        with tempfile.TemporaryDirectory() as tmp:
            out = self._write(nets, tmp)
            first = (out / "blocklist.txt").read_text().splitlines()[0]
        self.assertFalse(first.startswith("#"))

    def test_stats_and_badges(self):
        nets = [IPv4Network(f"10.{i // 256}.{i % 256}.0/24") for i in range(61234)]
        with tempfile.TemporaryDirectory() as tmp:
            out = self._write(nets, tmp)
            stats = json.loads((out / "stats.json").read_text())
            entries = json.loads((out / "badge-entries.json").read_text())
            updated = json.loads((out / "badge-updated.json").read_text())
        self.assertEqual(stats["total"], 61234)
        self.assertEqual(stats["generated_at"], "2026-10-19T04:17:00Z")
        self.assertEqual(
            stats["sources"]["spamhaus-drop"],
            {"category": "attack", "entries": 61234, "entries6": 0, "invalid": 2, "error": None},
        )
        self.assertEqual(
            entries,
            {"schemaVersion": 1, "label": "entries", "message": "61,234", "color": "blue"},
        )
        self.assertEqual(
            updated,
            {"schemaVersion": 1, "label": "updated", "message": "2026-10-19", "color": "brightgreen"},
        )

    def test_notice(self):
        ok = Source("spamhaus-drop", "https://www.spamhaus.org/drop/drop.txt", "plain",
                    "attack", required=True, license_note="Spamhaus DROP terms")
        bad = Source("dshield", "https://www.dshield.org/block.txt", "dshield",
                     "attack", license_note="CC BY-NC-SA")
        nets = [IPv4Network("1.10.16.0/20"), IPv4Network("8.8.8.8/32")]
        results = [SourceResult(ok, nets, 0, None),
                   SourceResult(bad, [], 0, "HTTPError: HTTP Error 503: down")]
        with tempfile.TemporaryDirectory() as tmp:
            write_outputs(Path(tmp), {"full": nets}, results, WHEN)
            notice = (Path(tmp) / "NOTICE.md").read_text()
        self.assertIn("MikroBlockList", notice)
        self.assertIn("https://github.com/Psycomy/MikroBlockList", notice)
        self.assertIn("not affiliated with MikroTik", notice)
        self.assertIn("For home / non-commercial use only", notice)
        self.assertIn("third-party sources", notice)
        self.assertIn("GPL-3.0", notice)
        self.assertIn("2026-10-19T04:17:00Z", notice)
        rows = [l for l in notice.splitlines() if l.startswith("| ")]
        self.assertEqual(rows[0], "| source | url | category | license / terms | variants | entries | entries6 | error | notice |")
        self.assertIn("| spamhaus-drop | https://www.spamhaus.org/drop/drop.txt | attack "
                      "| Spamhaus DROP terms | full | 2 | 0 |  | (c) The Spamhaus Project SLU (fetched 2026-10-19T04:17:00Z) |", rows)
        self.assertIn("| dshield | https://www.dshield.org/block.txt | attack | CC BY-NC-SA "
                      "| full | 0 | 0 | HTTPError: HTTP Error 503: down |  |", rows)

    def test_notice_counts_ipv4_and_ipv6_separately(self):
        src = Source("mix", "https://x", "plain", "attack")
        res = [SourceResult(src, [IPv4Network("8.8.8.8/32"), IPv6Network("2a00::1/128"),
                                  IPv6Network("2a00::2/128")], 0, None)]
        with tempfile.TemporaryDirectory() as tmp:
            write_outputs(Path(tmp), {"full": []}, res, WHEN)
            notice = (Path(tmp) / "NOTICE.md").read_text()
        self.assertIn("| mix | https://x | attack |  | full | 1 | 2 |  |  |", notice)

    def test_notice_escapes_pipes(self):
        src = Source("s", "https://x", "plain", "attack", license_note="a | b")
        with tempfile.TemporaryDirectory() as tmp:
            write_outputs(Path(tmp), {"full": []}, [SourceResult(src, [], 0, "x|y")], WHEN)
            notice = (Path(tmp) / "NOTICE.md").read_text()
        self.assertIn("| s | https://x | attack | a \\| b | full | 0 | 0 | x\\|y |  |", notice)

    def test_notice_md_shows_source_notices(self):
        src = Source("spamhaus-drop", "https://x", "plain", "attack")
        other = Source("other", "https://y", "plain", "attack")
        res = [SourceResult(src, [IPv4Network("1.1.1.0/24")], 0, None,
                            "Spamhaus DROP List 2026/10/22 - (c) 2026 The Spamhaus Project SLU"),
               SourceResult(other, [IPv4Network("2.2.2.0/24")], 0, None)]
        with tempfile.TemporaryDirectory() as tmp:
            write_outputs(Path(tmp), {"full": []}, res, WHEN)
            notice = (Path(tmp) / "NOTICE.md").read_text()
        self.assertIn("| Spamhaus DROP List 2026/10/22 - (c) 2026 The Spamhaus Project SLU |",
                      notice)
        self.assertIn("| other | https://y | attack |  | full | 1 | 0 |  |  |", notice)


if __name__ == "__main__":
    unittest.main()
