"""End-to-end cases for routeros/install.rsc and uninstall.rsc on a real CHR.

Skipped unless MBL_E2E=1. By default boots a disposable CHR in QEMU (image
cached in MBL_E2E_CACHE, default <tmp>/mbl-e2e-cache). With
MBL_E2E_SSH=user@host[:port] (and optionally MBL_E2E_KEY) it uses that CHR by
SSH key instead; the HTTPS test server is then served on this host's LAN
address as seen from that router.

Existing-host mode is DESTRUCTIVE: it reinstalls MBL, reboots the router and
finally runs uninstall.rsc, so any previous MBL installation there is gone.
It therefore also requires MBL_E2E_DESTRUCTIVE=1; without it the class is
skipped. A best-effort uninstall (which also removes the test's /ip dns static
entries, comment MBLTEST) also runs if a case aborts early or the run is
interrupted (atexit); after a run killed outright, run uninstall.rsc by hand.

Other environment variables: MBL_E2E_CHR_VERSION / MBL_E2E_CHR_SHA256 override the
pinned tests/e2e/chr-version.txt (an empty SHA256 skips the checksum), and
MBL_E2E_ARTIFACTS=<dir> receives console.log and qemu.log (written there while
the VM runs, so they survive a killed run) and router-log.txt (the router's
/log print, when the run ends), for CI failure artifacts.

The cases share one router and run in name order (test_01 ... test_15).
"""
from __future__ import annotations

import atexit
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import suppress
from datetime import datetime, timezone
from ipaddress import IPv4Address, IPv4Interface, IPv4Network, IPv6Network
from pathlib import Path

from mbl.render import write_outputs
from tests.e2e.harness import Chr, ChrError, TlsServer, ensure_image

REPO = Path(__file__).resolve().parents[2]
ROUTEROS = REPO / "routeros"
DEFAULT_HOST = "raw.githubusercontent.com"
DEFAULT_URL = f"https://{DEFAULT_HOST}/Psycomy/MikroBlockList/lists/routeros-full.txt"
UNREACHABLE = "192.0.2.1"  # TEST-NET-1 (RFC 5737)
DNS_COMMENT = "MBLTEST"
TEST_CA_CN = "MBL Test CA"
# routeros-full.txt must reach mbl-update's 100,000-byte minimum size:
# 10,000 entries from 45.0.0.0 are ~113 KB (5,000 would be ~55 KB).
LIST_SIZE = 10000
MIN_ENTRIES = 1000
# IPv6 lists (served, loaded only with :global mblIpv6 true): global /48s
# under 2a0e::/16 that pass the generator's normalize6.
LIST6_SIZE = 300
LITE6_SIZE = 60
# test_14m: valid entries served plus the invalid lines below (IPv4 line, /0,
# /129, a prefix wider than /19, garbage); 5 of 600 stays under the 1 % budget.
IPV6_VALIDATION_SIZE = 600
IPV6_INVALID = ["1.2.3.4", "::/0", "2a0e::/129", "2a0e::/4", "abc"]
# Lines that must never run on the router: they are only data, counted as invalid.
# 12 lines, under the 1 % budget (100 with LIST_SIZE entries).
MALICIOUS = [
    "/system reboot",
    "/ip firewall address-list add list=mbl-pwned address=1.1.1.1",
    "1.2.3.4; /ip firewall address-list add list=mbl-pwned address=1.1.1.2",
    "$([/ip firewall address-list add list=mbl-pwned address=1.1.1.3])",
    "[/ip firewall address-list add list=mbl-pwned address=1.1.1.4]",
    ':execute "/ip firewall address-list add list=mbl-pwned address=1.1.1.5"',
    "999.1.1.1",
    "1.2.3.0/40",
    "1.2.3.0/4",
    "abc",
    "",
    "9" * 100_000,  # over-long line: spans several 32 KiB chunks
]
# (action, log, log-prefix) of the three drop rules in each :global mblMode
DROP_RULES = ("MBL: in", "MBL: out", "MBL: router-out")
BLOCK_ACTIONS = {"MBL: in": ("drop", "false", ""),
                 "MBL: out": ("drop", "true", "MBL-OUT"),
                 "MBL: router-out": ("drop", "false", "")}
LOG_ACTIONS = {"MBL: in": ("passthrough", "false", ""),
               "MBL: out": ("log", "false", "MBL-OUT"),
               "MBL: router-out": ("log", "false", "MBL-ROUTER")}
# mbl-update's counter report; groups: verb, since, in, out, router-out, then
# the IPv6 in, out, router-out (None with IPv6 off)
COUNTER_RE = re.compile(
    r"MBL: (dropped|would have dropped) since (boot|\d{4}-\d\d-\d\d \d\d:\d\d:\d\d): "
    r"in (\d+), out (\d+), router-out (\d+) packets"
    r"(?:; ipv6 in (\d+), out (\d+), router-out (\d+))?$")
STAMP_RE = re.compile(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$")
REMINDER = ('MBL: log-only mode, nothing is blocked (reinstall with :global mblMode "block" '
            "to block)")
PINGS = 3


def _older(version: str) -> str:
    """A strictly lower MAJOR.MINOR.PATCH than `version` (patch-1, else minor-1, else major-1)."""
    major, minor, patch = (int(x) for x in version.split("."))
    if patch:
        return f"{major}.{minor}.{patch - 1}"
    if minor:
        return f"{major}.{minor - 1}.0"
    assert major, "VERSION 0.0.0 has no lower version"
    return f"{major - 1}.0.0"


_INSTALLED = (REPO / "VERSION").read_text().strip()
_MAJOR, _MINOR, _PATCH = (int(x) for x in _INSTALLED.split("."))

# Served lists that differ from the good one only in the header's script= value.
SCRIPT_VARIANTS = {
    "routeros-full-script-newer.txt": f"{_MAJOR + 1}.0.0",
    "routeros-full-script-patch.txt": f"{_MAJOR}.{_MINOR}.{_PATCH + 1}",
    "routeros-full-script-same.txt": _INSTALLED,
    "routeros-full-script-older.txt": _older(_INSTALLED),
    # a string comparison would order "10.0.0" before "3.1.0"; numerically it is newer
    "routeros-full-script-10.txt": "10.0.0",
    "routeros-full-script-3-10.txt": f"{_MAJOR}.{_MINOR + 9}.0",
    "routeros-full-script-inject.txt": "3.1.0;/system/reboot",
    "routeros-full-script-abc.txt": "abc",
    "routeros-full-script-empty.txt": "",
}


def _synthetic_list(out_dir: Path) -> None:
    """LIST_SIZE global /32s from 45.0.0.0 (full) and 2,000 from 46.0.0.0 (lite), plus
    LIST6_SIZE /48s from 2a0e:b000:: (full) and LITE6_SIZE from 2a0e:c000:: (lite);
    no real sources."""
    first = int(IPv4Address("45.0.0.0"))
    networks = [IPv4Network(IPv4Address(first + i)) for i in range(LIST_SIZE)]
    lite_first = int(IPv4Address("46.0.0.0"))
    lite = [IPv4Network(IPv4Address(lite_first + i)) for i in range(2000)]
    networks6 = [IPv6Network(f"2a0e:{0xb000 + i:x}::/48") for i in range(LIST6_SIZE)]
    lite6 = [IPv6Network(f"2a0e:{0xc000 + i:x}::/48") for i in range(LITE6_SIZE)]
    write_outputs(out_dir, {"full": networks, "lite": lite}, [], datetime.now(timezone.utc),
                  variants6={"full": networks6, "lite": lite6})


def _local_ip_towards(host: str) -> str:
    out = subprocess.run(
        ["ip", "route", "get", host], capture_output=True, text=True, check=True,
    ).stdout
    m = re.search(r"\bsrc (\S+)", out)
    if not m:
        raise RuntimeError(f"cannot find the local address towards {host}: {out!r}")
    return m.group(1)


class RouterOsTest(unittest.TestCase):
    chr: Chr
    server: TlsServer
    base: str

    @classmethod
    def setUpClass(cls):
        if os.environ.get("MBL_E2E") != "1":
            raise unittest.SkipTest("set MBL_E2E=1 to run the CHR end-to-end tests")
        ssh = os.environ.get("MBL_E2E_SSH")
        if ssh and os.environ.get("MBL_E2E_DESTRUCTIVE") != "1":
            raise unittest.SkipTest(
                "MBL_E2E_SSH mode reinstalls, reboots and finally uninstalls MBL on "
                f"{ssh} (a previous MBL install there is lost); "
                "set MBL_E2E_DESTRUCTIVE=1 to allow it"
            )
        tmp = tempfile.TemporaryDirectory(prefix="mbl-e2e-")
        cls.addClassCleanup(tmp.cleanup)
        tmpdir = Path(tmp.name)

        served = tmpdir / "served"
        _synthetic_list(served)
        lines = (served / "routeros-full.txt").read_text().splitlines(keepends=True)
        assert lines[-1] == f"# MBL-END entries={LIST_SIZE}\n", lines[-1]
        for name, size, var in (("routeros6-full.txt", LIST6_SIZE, "full"),
                                ("routeros6-lite.txt", LITE6_SIZE, "lite")):
            lines6 = (served / name).read_text().splitlines()
            assert f"variant={var} family=ipv6 " in lines6[0], lines6[0]
            assert lines6[-1] == f"# MBL-END entries={size}", lines6[-1]
        (served / "routeros-full-truncated.txt").write_text("".join(lines[:-1]))
        lines6 = (served / "routeros6-full.txt").read_text().splitlines()
        (served / "routeros6-full-truncated.txt").write_text(
            "\n".join(lines6[:-1]) + "\n")
        # 600 valid /48s plus 5 invalid lines: 5 is under the 1 % budget of 600
        # (with the standard 300 it would be over). Header and footer count 600.
        head6, foot6 = lines6[0], lines6[-1]
        big6 = [f"2a0e:{0xd000 + i:x}::/48" for i in range(IPV6_VALIDATION_SIZE)]
        text6 = "\n".join([head6.replace(f"entries={LIST6_SIZE}", f"entries={IPV6_VALIDATION_SIZE}"),
                          *big6, *IPV6_INVALID,
                          foot6.replace(f"entries={LIST6_SIZE}", f"entries={IPV6_VALIDATION_SIZE}")]) + "\n"
        assert f"entries={IPV6_VALIDATION_SIZE}" in text6.splitlines()[0], head6
        (served / "routeros6-full-validation.txt").write_text(text6)
        header, body, footer = lines[0], [ln.rstrip("\n") for ln in lines[1:-1]], lines[-1]
        assert len(body) == LIST_SIZE
        nl = "\n"

        def variant(name: str, head: str, entries: list[str], foot: str, eol: str = nl) -> None:
            text = eol.join([head.rstrip("\n"), *entries, foot.rstrip("\n")]) + eol
            (served / name).write_bytes(text.encode())

        variant("routeros-full-injected.txt", header, body + MALICIOUS, footer)
        wrong = LIST_SIZE + 100
        variant("routeros-full-mismatch.txt",
                header.replace(f"entries={LIST_SIZE}", f"entries={wrong}"), body,
                footer.replace(f"entries={LIST_SIZE}", f"entries={wrong}"))
        variant("routeros-full-crlf.txt", header,
                [ln + (" " if i < 10 else "") for i, ln in enumerate(body)], footer, "\r\n")
        assert header.startswith("# MikroBlockList v3 "), header
        variant("routeros-full-oldname.txt",
                header.replace("# MikroBlockList v3 ", "# MikrotikBlockList v3 ", 1), body, footer)
        variant("routeros-full-invalid.txt", header,
                body + [f"bad-line-{i}" for i in range(LIST_SIZE // 50)], footer)
        lite_text = (served / "routeros-lite.txt").read_text()
        assert "variant=lite" in lite_text.splitlines()[0], lite_text.splitlines()[0]
        (served / "routeros-lite-wrongheader.txt").write_text(
            lite_text.replace("variant=lite", "variant=full", 1))
        cls.installed_version = (REPO / "VERSION").read_text().strip()
        assert re.search(r" script=\S*$", header.rstrip("\n")), header
        for name, published in SCRIPT_VARIANTS.items():
            variant(name, re.sub(r"script=\S*$", f"script={published}", header.rstrip("\n")),
                    body, footer)

        if ssh:
            key = os.environ.get("MBL_E2E_KEY")
            cls.chr = Chr.existing(ssh, Path(key).expanduser() if key else None)
            lan_ip = _local_ip_towards(cls.chr.host)
            cls.server = TlsServer(served, extra_ips=(lan_ip,))
            host = lan_ip
        else:
            cls.server = TlsServer(served)
            host = "10.0.2.2"
        cls.addClassCleanup(cls.server.stop)
        cls.server.start()
        cls.base = f"https://{host}:{cls.server.port}"

        if ssh:
            cls.chr.wait_ssh(30)
        else:
            version, sha256 = (REPO / "tests/e2e/chr-version.txt").read_text().split()
            if os.environ.get("MBL_E2E_CHR_VERSION"):
                version = os.environ["MBL_E2E_CHR_VERSION"]
                sha256 = os.environ.get("MBL_E2E_CHR_SHA256") or None
            cache = Path(os.environ.get("MBL_E2E_CACHE")
                         or Path(tempfile.gettempdir()) / "mbl-e2e-cache")
            image = ensure_image(version, sha256, cache)
            cls.chr = Chr.boot(image, tmpdir / "vm", log_dir=cls._artifacts())
            cls.addClassCleanup(cls.chr.stop)
        cls.addClassCleanup(cls._save_router_log)

        cls.addClassCleanup(cls._remove_test_ca)
        cls._remove_test_ca()
        cls.chr.upload(cls.server.ca_pem, "ca.crt")
        cls.chr.run("/certificate import file-name=ca.crt trusted=yes passphrase=\"\"")
        cls.chr.run('/file remove [find where name="ca.crt"]')
        if ssh:
            # Never leave the user's router half-installed or pointing at the
            # (gone) test server if a case aborts early. Runs before the CA
            # removal and the server stop (cleanups are LIFO); atexit covers
            # an interrupted run (Ctrl-C) that skips the class cleanups.
            cls._uninstalled = False
            cls.addClassCleanup(cls._best_effort_uninstall)
            atexit.register(cls._best_effort_uninstall)

    @classmethod
    def _best_effort_uninstall(cls):
        if cls._uninstalled:
            return
        cls._uninstalled = True
        with suppress(ChrError, OSError):
            cls.chr.upload(ROUTEROS / "uninstall.rsc", "uninstall.rsc")
            cls.chr.run("/import uninstall.rsc", timeout=300)
        with suppress(ChrError, OSError):
            cls.chr.run(':global mblUrlBase ""')
        with suppress(ChrError, OSError):
            cls.chr.run(f'/ip dns static remove [find where comment="{DNS_COMMENT}"]')

    @classmethod
    def _artifacts(cls) -> Path | None:
        d = os.environ.get("MBL_E2E_ARTIFACTS")
        if not d:
            return None
        Path(d).mkdir(parents=True, exist_ok=True)
        return Path(d)

    @classmethod
    def _save_router_log(cls):
        out = cls._artifacts()
        if out is not None:
            with suppress(ChrError, OSError):
                (out / "router-log.txt").write_text(cls.chr.run("/log print", timeout=60))

    @classmethod
    def _remove_test_ca(cls):
        cls.chr.run(f'/certificate remove [find where common-name="{TEST_CA_CN}"]')

    # -- helpers ------------------------------------------------------------

    def install(self, base: str | None) -> str:
        self.chr.upload(ROUTEROS / "install.rsc", "install.rsc")
        cmd = "/import install.rsc"
        if base is not None:
            cmd = f':global mblUrlBase "{base}"; ' + cmd
        return self.chr.run(cmd, timeout=600)

    def uninstall(self) -> str:
        self.chr.upload(ROUTEROS / "uninstall.rsc", "uninstall.rsc")
        return self.chr.run("/import uninstall.rsc", timeout=300)

    def update(self, boot: bool = False) -> None:
        cmd = "/system script run mbl-update"
        if boot:
            cmd = ":global mblBoot true; " + cmd
        self.chr.run(cmd, timeout=600)

    def put(self, expr: str) -> str:
        return self.chr.run(f":put ({expr})").strip()

    def active_list(self) -> str:
        return self.put('[/ip firewall raw get [find where comment="MBL: in"] src-address-list]')

    def count(self, list_: str) -> int:
        return int(self.put(f'[:len [/ip firewall address-list find where list="{list_}"]]'))

    def num(self, path: str, where: str) -> int:
        return int(self.put(f"[:len [{path} find where {where}]]"))

    def running(self) -> bool:
        return self.chr.run(":global mblRunning; :put $mblRunning").strip() == "true"

    def log(self) -> str:
        """MBL log lines as '<topics> <message>', oldest first."""
        return self.chr.run(
            ':foreach i in=[/log find where message~"MBL"] do={'
            ':put ([:tostr [/log get $i topics]] . " " . [/log get $i message])}'
        )

    def mark(self) -> str:
        token = f"MBLTEST mark {time.time_ns()}"
        self.chr.run(f':log info "{token}"')
        return token

    def log_after(self, token: str) -> str:
        """Log lines after `token`; the whole log if the token is gone (reboot)."""
        text = self.log()
        i = text.find(token)
        return text if i < 0 else text[i + len(token):]

    def set_update_path(self, old: str, new: str) -> None:
        """Replace `old` with `new` in mbl-update's source, on the router."""
        self.chr.run(
            '{:local s [/system script get [find where name="mbl-update"] source];'
            f':local o "{old}"; :local p [:find $s $o];'
            ':if ([:typeof $p] = "nil") do={:error "MBLTEST: path not found"};'
            '/system script set [find where name="mbl-update"] '
            f'source=([:pick $s 0 $p] . "{new}" . [:pick $s ($p + [:len $o]) [:len $s]])}}'
        )

    def update_source(self) -> str:
        return self.chr.run(':put [/system script get [find where name="mbl-update"] source]')

    def assert_failed_update_keeps_active(self, path: str, error: str) -> None:
        """mbl-update requests `path`, fails with an `error` log line, keeps the list."""
        active, n = self.active_list(), self.count(self.active_list())
        token = self.mark()
        self.server.requests.clear()
        self.update()
        self.assertEqual(self.active_list(), active)
        self.assertEqual(self.count(active), n)
        self.assertIn(path, self.server.requests)
        log = self.log_after(token)
        errors = [ln for ln in log.splitlines()
                  if "error" in ln.split(" ", 1)[0] and "MBL:" in ln]
        self.assertTrue(any(error in ln for ln in errors), log)

    def serve_variant(self, name: str) -> None:
        self.set_update_path("/routeros-full.txt", f"/{name}")

    def restore_path(self, name: str) -> None:
        # no assertions: this runs in `finally` and must not mask the real failure
        self.set_update_path(f"/{name}", "/routeros-full.txt")

    def sample_protection(self) -> str:
        """'<list the in drop rule points to> <its entries>', or 'none 0' without the rule.

        The rule is "MBL: in", or "MBL-OLD: MBL: in" while a reinstall renames
        the old rules (they keep dropping until the new ones are added)."""
        return self.chr.run(
            '{:local r [/ip firewall raw find where comment="MBL: in"];'
            ':if ([:len $r] = 0) do={:set r [/ip firewall raw find where comment="MBL-OLD: MBL: in"]};'
            ':if ([:len $r] = 0) do={:put "none 0"} else={'
            ':local l [/ip firewall raw get ($r->0) src-address-list];'
            ':put ($l . " " . [:len [/ip firewall address-list find where list=$l]])}}'
        ).strip()

    def install_sampled(self) -> list[str]:
        """Install (test URL base) while another thread samples sample_protection()
        every 0.5 s; return the samples, the last one taken after the install."""
        return self.sampled(lambda: self.install(self.base))[1]

    def sampled(self, action):
        """Run action() while another thread samples sample_protection() every 0.5 s;
        return its result and the samples, the last one taken after it."""
        samples: list[str] = []
        stop = threading.Event()

        def poll():
            while not stop.is_set():
                try:
                    samples.append(self.sample_protection())
                except ChrError as e:
                    samples.append(f"error {e}")
                stop.wait(0.5)

        poller = threading.Thread(target=poll, daemon=True)
        poller.start()
        try:
            result = action()
        finally:
            stop.set()
            poller.join(30)
        samples.append(self.sample_protection())
        return result, samples

    def assert_always_protected(self, samples: list[str]) -> None:
        """Every sample has an in drop rule (see sample_protection) on a list with
        at least MIN_ENTRIES."""
        self.assertGreaterEqual(len(samples), 3, samples)
        for smp in samples:
            name, _, n = smp.partition(" ")
            self.assertNotEqual(name, "none", f"rule missing: {samples}")
            self.assertTrue(n.isdigit() and int(n) >= MIN_ENTRIES, f"{smp!r} in {samples}")

    # -- cases --------------------------------------------------------------

    def test_01_clean_install(self):
        out = self.install(self.base)
        self.assertEqual(self.num("/ip firewall raw", 'comment~"^MBL:"'), 6, out)
        for name in ("mbl-daily", "mbl-boot"):
            self.assertEqual(self.num("/system scheduler", f'name="{name}"'), 1, name)
        # every line is loaded, including those cut between two /file read chunks
        self.assertEqual(self.count(self.active_list()), LIST_SIZE)
        log = self.log()
        self.assertIn(f"MBL: installed (script {self.installed_version}, variant full, ipv6 no, mode block)", log)
        warning = f"MBL: using test URL base {self.base}"
        self.assertTrue(any(warning in ln and "warning" in ln.split(" ", 1)[0]
                            for ln in log.splitlines()), log)
        self.assertIn(warning, out)

    def test_02_update_alternates(self):
        old = self.active_list()
        self.update()
        new = self.active_list()
        self.assertEqual({old, new}, {"mbl-a", "mbl-b"})
        self.assertEqual(self.count(old), 0)
        self.assertEqual(self.count(new), LIST_SIZE)

    def test_03_truncated_keeps_active(self):
        try:
            self.set_update_path("/routeros-full.txt", "/routeros-full-truncated.txt")
            self.assertIn("/routeros-full-truncated.txt", self.update_source())
            # the footer is missing: the file is rejected
            self.assert_failed_update_keeps_active(
                "/routeros-full-truncated.txt", "MBL: mbl-new.txt rejected (no footer)")
        except BaseException:
            # restore on a best-effort basis without masking the real error
            with suppress(ChrError):
                self.set_update_path("/routeros-full-truncated.txt", "/routeros-full.txt")
            raise
        self.set_update_path("/routeros-full-truncated.txt", "/routeros-full.txt")
        self.assertIn(f"{self.base}/routeros-full.txt", self.update_source())

    def test_04_dead_url_keeps_active(self):
        self.server.fail_paths = {"/routeros-full.txt"}
        try:
            self.assert_failed_update_keeps_active("/routeros-full.txt", "MBL: download failed (")
        finally:
            self.server.fail_paths = set()

    def test_05_reboot_reloads(self):
        token = self.mark()
        self.chr.reboot()
        deadline = time.monotonic() + 150
        while "MBL: loaded" not in self.log_after(token):
            self.assertLess(time.monotonic(), deadline,
                            f"no 'MBL: loaded' after reboot: {self.log_after(token)}")
            time.sleep(3)
        self.assertGreaterEqual(self.count(self.active_list()), MIN_ENTRIES)
        # mbl-boot runs a second (download) update 60 s after boot; let it
        # finish so it does not hold the lock during the next case.
        deadline = time.monotonic() + 180
        while self.log_after(token).count("MBL: loaded") < 2 or self.running():
            self.assertLess(time.monotonic(), deadline,
                            f"boot download update did not finish: {self.log_after(token)}")
            time.sleep(3)

    def test_06_flash_path(self):
        if self.num("/file", 'name="flash"') > 0:
            # never touch a real flash/ (it would be removed at the end)
            self.skipTest("a flash directory already exists on this router")
        self.chr.run("/file add type=directory name=flash")
        try:
            self.update()
            self.assertEqual(self.num("/file", 'name="flash/mbl-last.txt"'), 1,
                             self.chr.run("/file print"))
            before = self.active_list()
            token = self.mark()
            self.update(boot=True)
            after = self.log_after(token)
            self.assertIn("MBL: loaded", after)
            self.assertNotIn("boot: no", after)
            self.assertNotEqual(self.active_list(), before)
            self.assertGreaterEqual(self.count(self.active_list()), MIN_ENTRIES)
        finally:
            self.chr.run('/file remove [find where name~"^flash/"]')
            self.chr.run('/file remove [find where name="flash"]')
        self.assertEqual(self.num("/file", 'name~"^flash"'), 0)

    def test_07_reinstall_no_duplicates(self):
        admin = 'list="mbl-allow" and comment="MBL: admin"'
        admins = self.num("/ip firewall address-list", admin)
        self.assertGreaterEqual(admins, 1)
        self.install(self.base)
        self.assertEqual(self.num("/ip firewall raw", 'comment~"^MBL:"'), 6)
        self.assertEqual(self.num("/system scheduler", 'name~"^mbl-"'), 2)
        self.assertEqual(self.num("/ip firewall address-list", admin), admins)
        self.assertGreaterEqual(self.count(self.active_list()), MIN_ENTRIES)

    def test_07b_reinstall_no_rule_gap(self):
        # v3 -> v3: the old rules keep dropping (as MBL-OLD:) until the new ones exist
        self.assertGreaterEqual(self.count(self.active_list()), MIN_ENTRIES)
        self.assert_always_protected(self.install_sampled())
        self.assertEqual(self.num("/ip firewall raw", 'comment~"^MBL:"'), 6)
        self.assertEqual(self.num("/ip firewall raw", 'comment~"^MBL-OLD:"'), 0)

    def test_07c_old_rules_cleaned(self):
        # what an install aborted between the rename and the cleanup leaves behind
        self.chr.run('/ip firewall raw add chain=prerouting in-interface-list=WAN '
                     'src-address-list=mbl-a action=drop comment="MBL-OLD: in"')
        self.assertEqual(self.num("/ip firewall raw", 'comment="MBL-OLD: in"'), 1)
        self.install(self.base)
        self.assertEqual(self.num("/ip firewall raw", 'comment~"^MBL-OLD:"'), 0)
        self.assertEqual(self.num("/ip firewall raw", 'comment~"^MBL:"'), 6)
        self.assertGreaterEqual(self.count(self.active_list()), MIN_ENTRIES)

    def wait_not_running(self, timeout: float = 300) -> None:
        deadline = time.monotonic() + timeout
        while self.running():
            self.assertLess(time.monotonic(), deadline, "mbl-update still running")
            time.sleep(1)

    def start_update_bg(self) -> None:
        """Start mbl-update in the background and wait until it holds the lock."""
        self.chr.run(":execute {/system script run mbl-update}")
        deadline = time.monotonic() + 30
        while not self.running():
            self.assertLess(time.monotonic(), deadline, "mbl-update did not start")
            time.sleep(0.2)

    def test_07d_update_skips_family_without_rules(self):
        active, n = self.active_list(), self.count(self.active_list())
        other = "mbl-b" if active == "mbl-a" else "mbl-a"
        self.chr.run('/ip firewall raw set [find where comment="MBL: in"] comment="MBLTEST: in"')
        try:
            token = self.mark()
            self.server.requests.clear()
            self.update()
            log = self.log_after(token)
            self.assertIn("MBL: ipv4 rules missing, update skipped", log)
            self.assertNotIn("MBL: loaded", log)
            self.assertEqual(self.server.requests, [])
            self.assertEqual(self.count(active), n)
            self.assertEqual(self.count(other), 0)
            self.assertFalse(self.running())
        finally:
            self.chr.run('/ip firewall raw set [find where comment="MBLTEST: in"] '
                         'comment="MBL: in"')
        self.assertEqual(self.active_list(), active)

    def test_07e_rules_changed_during_update(self):
        # what a reinstall does to the rules while an update is loading a list
        active, n = self.active_list(), self.count(self.active_list())
        target = "mbl-b" if active == "mbl-a" else "mbl-a"
        token = self.mark()
        try:
            # the router itself waits for the first loaded entry and renames the
            # rule at once (a poll over SSH can miss the ~2 s load of 10000 entries)
            self.chr.run(
                '{:execute {/system script run mbl-update}; :local i 0;'
                f':while ([:len [/ip firewall address-list find where list="{target}"]] = 0'
                ' && $i < 1200) do={:delay 100ms; :set i ($i + 1)};'
                '/ip firewall raw set [find where comment="MBL: in"] '
                'comment="MBL-OLD: MBL: in"}', timeout=300)
            self.wait_not_running()
        finally:
            self.chr.run('/ip firewall raw set [find where comment="MBL-OLD: MBL: in"] '
                         'comment="MBL: in"')
        log = self.log_after(token)
        self.assertIn("MBL: ipv4 rules changed during update, aborted", log)
        self.assertNotIn("MBL: loaded", log)
        self.assertEqual(self.active_list(), active)
        self.assertEqual(self.count(active), n)
        self.assertEqual(self.count(target), 0)
        for rule in ("MBL: out", "MBL: router-out"):
            self.assertEqual(self.put(f'[/ip firewall raw get [find where comment="{rule}"] '
                                      'dst-address-list]'), active, rule)

    def test_07f_reinstall_during_update(self):
        # an update runs in the background when the installer starts: the installer
        # refuses (nothing changed), or completes when it took the lock first
        self.assertFalse(self.running())
        source = self.update_source()
        self.chr.upload(ROUTEROS / "install.rsc", "install.rsc")

        def race() -> str:
            try:
                out = self.chr.run(':execute {/system script run mbl-update}; '
                                   f':global mblUrlBase "{self.base}"; /import install.rsc',
                                   timeout=600)
            except ChrError as e:
                out = str(e)
            self.wait_not_running()
            return out

        token = self.mark()
        out, samples = self.sampled(race)
        log = self.log_after(token)
        refused = "an update is running (mblRunning is set)" in out
        self.assertTrue(refused or "MBL: installed" in out, out)
        # which branch ran: the installer refused, or it completed and the
        # background update loaded first, was skipped on the lock / on missing
        # rules, or dropped its load because the rules changed
        if refused:
            branch = "installer refused"
        elif "update already running, skipped" in log:
            branch = "installer completed, update skipped (lock)"
        elif "rules missing, update skipped" in log:
            branch = "installer completed, update skipped (rules missing)"
        elif "rules changed during update, aborted" in log:
            branch = "installer completed, update aborted (rules changed)"
        else:
            branch = "installer completed, update loaded"
        print(f"\n  test_07f branch: {branch}", file=sys.stderr)
        self.assertIn(branch, {
            "installer refused", "installer completed, update skipped (lock)",
            "installer completed, update skipped (rules missing)",
            "installer completed, update aborted (rules changed)",
            "installer completed, update loaded"}, log)
        if refused:
            self.assertIn("Nothing was changed.", out)
            self.assertEqual(self.num("/system script", 'name="mbl-update"'), 1)
            self.assertEqual(self.update_source(), source)
        else:
            self.assertIn("MBL: installed", log)
        self.assertGreaterEqual(self.count(self.active_list()), MIN_ENTRIES, branch)
        self.assert_always_protected(samples)
        self.assertEqual(self.num("/ip firewall raw", 'comment~"^MBL:"'), 6)
        self.assertEqual(self.num("/ip firewall raw", 'comment~"^MBL-OLD:"'), 0)
        self.assertEqual(self.num("/system scheduler", 'name~"^mbl-"'), 2)
        self.assertGreaterEqual(self.count(self.active_list()), MIN_ENTRIES)
        self.assertGreater(self.count("mbl-a") + self.count("mbl-b"), 0)
        self.assertFalse(self.running())

    def test_07g_installer_refuses_while_locked(self):
        # deterministic form of 07f's refusal: the lock is held when it starts
        before = (self.active_list(), self.count(self.active_list()), self.update_source())
        self.server.requests.clear()
        self.chr.upload(ROUTEROS / "install.rsc", "install.rsc")
        try:
            out = self.chr.run(':global mblRunning true; '
                               f':global mblUrlBase "{self.base}"; /import install.rsc',
                               timeout=600)
        except ChrError as e:
            out = str(e)
        finally:
            self.chr.run(":global mblRunning false")
        self.assertIn("MBL: an update is running (mblRunning is set); try again in a minute, "
                      "or clear it with :global mblRunning false. Nothing was changed.", out)
        self.assertEqual(self.server.requests, [])
        self.assertEqual((self.active_list(), self.count(self.active_list()),
                          self.update_source()), before)
        self.assertEqual(self.num("/ip firewall raw", 'comment~"^MBL:"'), 6)
        self.assertEqual(self.num("/ip firewall raw", 'comment~"^MBL-OLD:"'), 0)

    def test_07h_lock_released_after_install_error(self):
        # an installer whose step 5 fails (a placeholder that is not in mbl-update)
        installer = (ROUTEROS / "install.rsc").read_text()
        good = ':set mblSrc [$mblInject $mblSrc "@MBL_IPV6@" $mblV6]'
        self.assertIn(good, installer)
        with tempfile.TemporaryDirectory() as d:
            broken = Path(d) / "install.rsc"
            broken.write_text(installer.replace(good, ':set mblSrc [$mblInject $mblSrc "@MBL_NOPE@" $mblV6]'))
            self.chr.upload(broken, "install.rsc")
        active, n = self.active_list(), self.count(self.active_list())
        try:
            out = self.chr.run(f':global mblUrlBase "{self.base}"; /import install.rsc',
                               timeout=600)
        except ChrError as e:
            out = str(e)
        self.assertIn("placeholder @MBL_NOPE@ not found", out)
        self.assertFalse(self.running())
        # the renamed old rules keep dropping on the old list
        self.assertEqual(self.num("/ip firewall raw", 'comment~"^MBL:"'), 0)
        self.assertEqual(self.num("/ip firewall raw", 'comment~"^MBL-OLD:"'), 6)
        self.assertEqual(self.sample_protection(), f"{active} {n}")
        # a reinstall finds the old list through "MBL-OLD: MBL: in" and cleans up
        self.assert_always_protected(self.install_sampled())
        self.assertEqual(self.num("/ip firewall raw", 'comment~"^MBL:"'), 6)
        self.assertEqual(self.num("/ip firewall raw", 'comment~"^MBL-OLD:"'), 0)
        self.assertEqual(self.num("/system scheduler", 'name~"^mbl-"'), 2)
        self.assertEqual(self.count(self.active_list()), LIST_SIZE)
        self.assertFalse(self.running())

    def test_07i_update_during_reinstall_skipped(self):
        # the installer holds the lock from step 2 through its own first load:
        # an update started meanwhile (scheduler, by hand) is skipped
        self.assertFalse(self.running())
        token = self.mark()
        result: dict[str, str] = {}

        def install():
            try:
                result["out"] = self.install(self.base)
            except ChrError as e:
                result["out"] = str(e)

        installer = threading.Thread(target=install, daemon=True)
        installer.start()
        try:
            deadline = time.monotonic() + 120
            # the lock is held and mbl-update exists again (step 3 removes it, step 5
            # adds the new one): what a daily scheduler run would meet
            while not (self.running() and self.num("/system script", 'name="mbl-update"')):
                self.assertTrue(installer.is_alive(), f"installer ended first: {result}")
                self.assertLess(time.monotonic(), deadline, "installer never took the lock")
                time.sleep(0.2)
            self.update()
        finally:
            installer.join(600)
        self.assertFalse(installer.is_alive())
        log = self.log_after(token)
        self.assertIn("MBL: installed", result.get("out", ""), result)
        self.assertIn("MBL: update already running, skipped", log)
        loads = [ln for ln in log.splitlines() if ln.startswith("script;") and "MBL: loaded" in ln]
        self.assertEqual(len(loads), 1, log)  # only the installer's first load
        self.assertEqual(self.num("/ip firewall raw", 'comment~"^MBL:"'), 6)
        self.assertEqual(self.num("/ip firewall raw", 'comment~"^MBL-OLD:"'), 0)
        self.assertEqual(self.count(self.active_list()), LIST_SIZE)
        self.assertFalse(self.running())
        self.assertEqual(self.chr.run(":global mblInstallRun; :put $mblInstallRun").strip(),
                         "false")

    def test_08_uninstall_clean(self):
        lists = 'name="WAN" or name="LAN"'
        members = 'list="WAN" or list="LAN"'
        n_lists = self.num("/interface list", lists)
        n_members = self.num("/interface list member", members)
        self.uninstall()
        self.assertEqual(self.num("/ip firewall raw", 'comment~"^MBL:"'), 0)
        self.assertEqual(self.num("/system script", 'name~"^mbl-"'), 0)
        self.assertEqual(self.num("/system scheduler", 'name~"^mbl-"'), 0)
        self.assertEqual(self.count("mbl-a"), 0)
        self.assertEqual(self.count("mbl-b"), 0)
        self.assertEqual(self.num("/file", 'name~"mbl-.*\\\\.(rsc|txt)"'), 0,
                         self.chr.run("/file print"))
        self.assertEqual(self.num("/interface list", lists), 2)
        self.assertEqual(self.num("/interface list", lists), n_lists)
        self.assertEqual(self.num("/interface list member", members), n_members)

    def test_09_default_url_without_override(self):
        # No internet needed: the default host resolves to an unreachable
        # address, so the first load fails (fetch timeout, ~10 s) and only
        # the URL is checked.
        dns = f'/ip dns static remove [find where comment="{DNS_COMMENT}"]'
        try:
            self.chr.run(f'/ip dns static add name={DEFAULT_HOST} '
                         f'address={UNREACHABLE} comment="{DNS_COMMENT}"')
            self.chr.run("/ip dns cache flush")
            self.chr.run(':global mblUrlBase ""')
            self.install(None)
            self.assertIn(DEFAULT_URL, self.update_source())
        finally:
            try:
                self.uninstall()
            finally:
                self.chr.run(dns)

    def test_10_reinstall_after_uninstall(self):
        # test_09 leaves the router uninstalled; the cases below need it installed.
        out = self.install(self.base)
        self.assertEqual(self.num("/ip firewall raw", 'comment~"^MBL:"'), 6, out)
        self.assertEqual(self.count(self.active_list()), LIST_SIZE)

    def test_11_injected_lines_never_run(self):
        name = "routeros-full-injected.txt"
        self.serve_variant(name)
        try:
            old = self.active_list()
            # a global variable is lost on reboot: proves `/system reboot` did not run
            self.chr.run(':global mblE2eAlive "yes"')
            token = self.mark()
            self.server.requests.clear()
            self.update()
            self.assertEqual(self.chr.run(":global mblE2eAlive; :put $mblE2eAlive").strip(), "yes")
            self.assertIn(token, self.log())
            self.assertNotEqual(self.active_list(), old)
            self.assertEqual(self.count(self.active_list()), LIST_SIZE)
            self.assertEqual(self.count("mbl-pwned"), 0)
            self.assertIn(f"/{name}", self.server.requests)
            self.assertIn("MBL: loaded", self.log_after(token))
        finally:
            self.restore_path(name)
            with suppress(ChrError, OSError):
                self.chr.run('/system script environment remove [find where name="mblE2eAlive"]')

    def test_12_count_mismatch_rejected(self):
        name = "routeros-full-mismatch.txt"
        self.serve_variant(name)
        try:
            self.assert_failed_update_keeps_active(
                f"/{name}", "MBL: mbl-new.txt rejected (count mismatch)")
        finally:
            self.restore_path(name)

    def test_13_crlf_accepted(self):
        name = "routeros-full-crlf.txt"
        self.serve_variant(name)
        try:
            old = self.active_list()
            token = self.mark()
            self.update()
            self.assertNotEqual(self.active_list(), old)
            self.assertEqual(self.count(self.active_list()), LIST_SIZE)
            self.assertIn(f"MBL: loaded {LIST_SIZE} entries", self.log_after(token))
        finally:
            self.restore_path(name)

    def test_13b_old_project_name_header_rejected(self):
        name = "routeros-full-oldname.txt"
        self.serve_variant(name)
        try:
            self.assert_failed_update_keeps_active(
                f"/{name}", "MBL: mbl-new.txt rejected (no header)")
        finally:
            self.restore_path(name)

    def test_14_too_many_invalid_rejected(self):
        name = "routeros-full-invalid.txt"
        self.serve_variant(name)
        try:
            self.assert_failed_update_keeps_active(
                f"/{name}", "MBL: mbl-new.txt rejected (too many invalid lines")
        finally:
            self.restore_path(name)

    def script_notices(self, name: str) -> tuple[str, list[str]]:
        """Serve `name`, run mbl-update, assert the load worked; return (log, notice lines)."""
        self.serve_variant(name)
        try:
            old = self.active_list()
            token = self.mark()
            self.server.requests.clear()
            self.update()
            self.assertIn(f"/{name}", self.server.requests)
            self.assertNotEqual(self.active_list(), old)
            self.assertEqual(self.count(self.active_list()), LIST_SIZE)
            log = self.log_after(token)
            self.assertIn(f"MBL: loaded {LIST_SIZE} entries", log)
            return log, [ln for ln in log.splitlines() if "MBL: script" in ln]
        finally:
            self.restore_path(name)

    def test_14b_newer_script_notice(self):
        _, notices = self.script_notices("routeros-full-script-newer.txt")
        self.assertEqual(len(notices), 1, notices)
        topics, _, msg = notices[0].partition(" ")
        self.assertIn("warning", topics)
        self.assertIn(
            f"MBL: script {_MAJOR + 1}.0.0 available (installed {self.installed_version}) - "
            'reinstall: /tool fetch url="https://raw.githubusercontent.com/Psycomy/'
            'MikroBlockList/main/routeros/install.rsc" check-certificate=yes; '
            "/import install.rsc", msg)

    def test_14c_no_notice_when_not_newer(self):
        for name in ("routeros-full-script-same.txt", "routeros-full-script-older.txt"):
            with self.subTest(name):
                _, notices = self.script_notices(name)
                self.assertEqual(notices, [])

    def test_14d_bad_script_version_ignored(self):
        self.chr.run(':global mblE2eAlive "yes"')
        try:
            for name in ("routeros-full-script-inject.txt", "routeros-full-script-abc.txt",
                         "routeros-full-script-empty.txt"):
                with self.subTest(name):
                    _, notices = self.script_notices(name)
                    self.assertEqual(notices, [])
                    # a global variable is lost on reboot: proves `/system reboot` did not run
                    self.assertEqual(
                        self.chr.run(":global mblE2eAlive; :put $mblE2eAlive").strip(), "yes")
        finally:
            with suppress(ChrError, OSError):
                self.chr.run('/system script environment remove [find where name="mblE2eAlive"]')

    def test_14e_numeric_comparison(self):
        # The patch component counts, and the comparison is numeric: 10.0.0 is newer than 3.1.0
        # although a string comparison would order "10.0.0" before "3.1.0".
        for name, published in (
                ("routeros-full-script-patch.txt", f"{_MAJOR}.{_MINOR}.{_PATCH + 1}"),
                ("routeros-full-script-10.txt", "10.0.0"),
                ("routeros-full-script-3-10.txt", f"{_MAJOR}.{_MINOR + 9}.0")):
            with self.subTest(published):
                _, notices = self.script_notices(name)
                self.assertEqual(len(notices), 1, notices)
                self.assertIn(f"MBL: script {published} available", notices[0])

    def install_variant(self, value: str) -> str:
        """Install with `:global mblVariant`; the global persists, so always set it."""
        self.chr.upload(ROUTEROS / "install.rsc", "install.rsc")
        return self.chr.run(
            f':global mblUrlBase "{self.base}"; :global mblVariant "{value}"; /import install.rsc',
            timeout=600)

    def test_14f_install_lite(self):
        self.server.requests.clear()
        token = self.mark()
        out = self.install_variant("lite")
        self.assertIn("/routeros-lite.txt", self.server.requests)
        self.assertEqual(self.count(self.active_list()), 2000)
        self.assertIn(f"MBL: installed (script {self.installed_version}, variant lite, ipv6 no, mode block)",
                      self.log_after(token))
        self.assertIn("variant lite", out)
        self.assertIn("routeros-lite.txt", self.update_source())

    def test_14g_reinstall_keeps_lite(self):
        self.server.requests.clear()
        self.install_variant("")
        self.assertIn("/routeros-lite.txt", self.server.requests)
        self.assertNotIn("/routeros-full.txt", self.server.requests)
        self.assertEqual(self.count(self.active_list()), 2000)
        self.assertIn("routeros-lite.txt", self.update_source())

    def test_14h_invalid_variant_rejected(self):
        active, n = self.active_list(), self.count(self.active_list())
        self.server.requests.clear()
        try:
            out = self.install_variant("mini")
        except ChrError as e:
            out = str(e)
        self.assertIn("invalid mblVariant", out)
        self.assertEqual(self.num("/ip firewall raw", 'comment~"^MBL:"'), 6)
        self.assertEqual(self.active_list(), active)
        self.assertEqual(self.count(active), n)
        self.assertEqual(self.server.requests, [])
        self.assertIn("routeros-lite.txt", self.update_source())

    def test_14i_variant_mismatch_rejected(self):
        name = "routeros-lite-wrongheader.txt"
        self.assertIn("routeros-lite.txt", self.update_source())
        self.set_update_path("/routeros-lite.txt", f"/{name}")
        try:
            self.assert_failed_update_keeps_active(
                f"/{name}", "MBL: mbl-new.txt rejected (bad header)")
        finally:
            self.set_update_path(f"/{name}", "/routeros-lite.txt")

    def test_14j_back_to_full(self):
        self.server.requests.clear()
        self.install_variant("full")
        self.assertIn("/routeros-full.txt", self.server.requests)
        self.assertEqual(self.count(self.active_list()), LIST_SIZE)
        self.assertIn("routeros-full.txt", self.update_source())

    def install_ipv6(self, ipv6: str, variant: str = "full") -> str:
        """Install with explicit :global mblIpv6 ('true', 'false' or '' = keep) and mblVariant."""
        self.chr.upload(ROUTEROS / "install.rsc", "install.rsc")
        value = ipv6 if ipv6 in ("true", "false") else '""'
        return self.chr.run(
            f':global mblUrlBase "{self.base}"; :global mblVariant "{variant}"; '
            f":global mblIpv6 {value}; /import install.rsc", timeout=600)

    def active_list6(self) -> str:
        return self.put(
            '[/ipv6 firewall raw get [find where comment="MBL: in"] src-address-list]')

    def count6(self, list_: str) -> int:
        return int(self.put(f'[:len [/ipv6 firewall address-list find where list="{list_}"]]'))

    def mbl6_objects(self) -> dict[str, int]:
        """Every IPv6 MBL object on the router: raw rules, mbl6-* list entries, mbl6-* files."""
        return {
            "rules": self.num("/ipv6 firewall raw", 'comment~"^MBL(-OLD)?:"'),
            "entries": self.num("/ipv6 firewall address-list", 'list~"^mbl6-"'),
            "files": self.num("/file", 'name~"mbl6-"'),
        }

    def requests6(self) -> list[str]:
        return [r for r in self.server.requests if r.startswith("/routeros6-")]

    def test_14k_no_ipv6_by_default(self):
        self.assertEqual(self.mbl6_objects(), {"rules": 0, "entries": 0, "files": 0})
        self.server.requests.clear()
        token = self.mark()
        out = self.install_ipv6("")
        self.assertIn("/routeros-full.txt", self.server.requests)  # positive control
        self.assertEqual(self.requests6(), [])
        self.assertEqual(self.mbl6_objects(), {"rules": 0, "entries": 0, "files": 0})
        self.assertEqual(self.count6("mbl6-a") + self.count6("mbl6-b"), 0)
        self.assertIn(':local ipv6 "no"', self.update_source())
        self.assertEqual(self.num("/ip firewall raw", 'comment~"^MBL:"'), 6)
        self.assertEqual(self.count(self.active_list()), LIST_SIZE)
        self.assertIn(f"MBL: installed (script {self.installed_version}, variant full, ipv6 no, mode block)",
                      self.log_after(token))
        self.assertIn("ipv6 no", out)
        # an update with IPv6 off never asks for the IPv6 list either
        self.server.requests.clear()
        self.update()
        self.assertTrue(self.server.requests)
        self.assertEqual(self.requests6(), [])
        self.assertEqual(self.mbl6_objects(), {"rules": 0, "entries": 0, "files": 0})

    def test_14l_install_ipv6(self):
        # a failed first IPv6 load still installs IPv4, and the message says so
        self.server.fail_paths = {"/routeros6-full.txt"}
        try:
            token = self.mark()
            out = self.install_ipv6("true")
        finally:
            self.server.fail_paths = set()
        note = (f"MBL: installed (script {self.installed_version}, variant full, "
                "ipv6 yes, mode block - first ipv6 load failed, see log)")
        self.assertIn(note, out)
        self.assertIn(note, self.log_after(token))
        self.assertEqual(self.count(self.active_list()), LIST_SIZE)
        self.assertEqual(self.count6("mbl6-a") + self.count6("mbl6-b"), 0)
        self.server.requests.clear()
        token = self.mark()
        out = self.install_ipv6("true")
        self.assertIn("/routeros-full.txt", self.server.requests)
        self.assertIn("/routeros6-full.txt", self.server.requests)
        self.assertEqual(self.num("/ipv6 firewall raw", 'comment~"^MBL:"'), 6)
        active6 = self.active_list6()
        self.assertIn(active6, ("mbl6-a", "mbl6-b"))
        self.assertEqual(self.count6(active6), LIST6_SIZE)
        log = self.log_after(token)
        self.assertIn(f"loaded {LIST6_SIZE} ipv6 entries into {active6}", log)
        self.assertIn(f"MBL: installed (script {self.installed_version}, variant full, ipv6 yes, mode block)",
                      log)
        self.assertIn("ipv6 yes, mode block)", out)
        self.assertNotIn("first ipv6 load failed", out)
        self.assertEqual(self.count(self.active_list()), LIST_SIZE)
        self.assertEqual(self.num("/ip firewall raw", 'comment~"^MBL:"'), 6)
        self.assertIn(':local ipv6 "yes"', self.update_source())
        # reinstalling with mblIpv6 empty keeps IPv6 on
        self.server.requests.clear()
        self.install_ipv6("")
        self.assertIn("/routeros6-full.txt", self.server.requests)
        self.assertEqual(self.num("/ipv6 firewall raw", 'comment~"^MBL:"'), 6)
        self.assertEqual(self.count6(self.active_list6()), LIST6_SIZE)
        self.assertEqual(self.count(self.active_list()), LIST_SIZE)

    def test_14m_ipv6_line_validation(self):
        name = "routeros6-full-validation.txt"
        self.set_update_path("/routeros6-full.txt", f"/{name}")
        try:
            old = self.active_list6()
            token = self.mark()
            self.server.requests.clear()
            self.update()
            self.assertIn(f"/{name}", self.server.requests)
            new = self.active_list6()
            self.assertNotEqual(new, old)
            self.assertEqual(self.count6(new), IPV6_VALIDATION_SIZE)
            self.assertEqual(self.count6(old), 0)
            self.assertIn(f"MBL: loaded {IPV6_VALIDATION_SIZE} ipv6 entries into {new}",
                          self.log_after(token))
            # none of the invalid lines became an entry
            self.assertEqual(self.num("/ipv6 firewall address-list",
                                      f'list="{new}" and address~"^2a0e::"'), 0)
            self.assertEqual(self.num("/ipv6 firewall address-list",
                                      f'list="{new}" and address~"^::"'), 0)
        finally:
            self.set_update_path(f"/{name}", "/routeros6-full.txt")

    def test_14n_ipv6_failure_never_touches_ipv4(self):
        cases = {"routeros6-full-404.txt": "MBL: ipv6 download failed",
                 "routeros6-full-truncated.txt": "rejected"}
        for name, reason in cases.items():
            with self.subTest(name):
                self.set_update_path("/routeros6-full.txt", f"/{name}")
                try:
                    old4, old6 = self.active_list(), self.active_list6()
                    n6 = self.count6(old6)
                    self.assertGreater(n6, 0)
                    token = self.mark()
                    self.server.requests.clear()
                    self.update()
                    self.assertIn("/routeros-full.txt", self.server.requests)
                    self.assertIn(f"/{name}", self.server.requests)
                    self.assertNotEqual(self.active_list(), old4)
                    self.assertEqual(self.count(self.active_list()), LIST_SIZE)
                    self.assertEqual(self.num("/ip firewall raw", 'comment~"^MBL:"'), 6)
                    self.assertEqual(self.active_list6(), old6)
                    self.assertEqual(self.count6(old6), n6)
                    log = self.log_after(token)
                    self.assertIn(f"MBL: loaded {LIST_SIZE} entries", log)
                    self.assertTrue(any("MBL: ipv6" in ln for ln in log.splitlines()), log)
                    self.assertTrue(any(reason in ln and ("ipv6" in ln or "mbl6" in ln)
                                        for ln in log.splitlines()), log)
                    self.assertFalse(self.running())
                finally:
                    self.set_update_path(f"/{name}", "/routeros6-full.txt")

    def install_mode(self, mode: str, ipv6: str = "") -> str:
        """Install with :global mblMode `mode` ('' = keep), variant full, and
        :global mblIpv6 ('true', 'false' or '' = keep)."""
        self.chr.upload(ROUTEROS / "install.rsc", "install.rsc")
        value = ipv6 if ipv6 in ("true", "false") else '""'
        return self.chr.run(
            f':global mblUrlBase "{self.base}"; :global mblVariant "full"; '
            f':global mblIpv6 {value}; :global mblMode "{mode}"; /import install.rsc',
            timeout=600)

    def drop_rule_actions(self, path: str = "/ip firewall raw") -> dict[str, tuple[str, ...]]:
        """(action, log, log-prefix) of each drop rule, which must exist once."""
        out = {}
        for rule in DROP_RULES:
            self.assertEqual(self.num(path, f'comment="{rule}"'), 1, f"{path} {rule}")
            r = f'([{path} find where comment="{rule}"]->0)'
            action, log, prefix = (self.put(f"[{path} get {r} {prop}]")
                                   for prop in ("action", "log", "log-prefix"))
            # a rule added without log= reads back an empty log
            out[rule] = (action, log or "false", prefix)
        return out

    def counter_lines(self, log: str) -> list[re.Match]:
        return [m for ln in log.splitlines() if (m := COUNTER_RE.search(ln))]

    def reminders(self, log: str) -> list[str]:
        return [ln for ln in log.splitlines() if REMINDER in ln]

    def stats_since(self) -> str:
        return self.chr.run(":global mblStatsSince; :put $mblStatsSince").strip()

    def ping_listed(self) -> None:
        """PINGS pings from the router to the first entry of the active list (router-out).

        On the QEMU CHR the address is routed via a gateway that does not answer
        ARP (slirp only answers for its own addresses), so a ping that passes the
        rule (log mode) still never leaves the VM; the raw output chain sees it
        before the neighbour lookup."""
        entry = self.put(f'[/ip firewall address-list get ([find where list="{self.active_list()}"]->0) address]')
        addr = entry.split("/")[0]
        route = None
        if not os.environ.get("MBL_E2E_SSH"):
            own = IPv4Interface(self.put('[/ip address get ([find where interface="ether1"]->0) address]'))
            route = own.network.network_address + 99
            self.assertNotEqual(route, own.ip)
            self.chr.run(f"/ip route add dst-address={addr}/32 gateway={route} comment={DNS_COMMENT}")
        try:
            self.chr.run(f":do {{/ping {addr} count={PINGS} interval=200ms}} on-error={{}}", timeout=60)
        finally:
            if route is not None:
                self.chr.run(f'/ip route remove [find where comment="{DNS_COMMENT}"]')

    def test_14nb_ipv6_log_mode(self):
        token = self.mark()
        out = self.install_mode("log")
        msg = f"MBL: installed (script {self.installed_version}, variant full, ipv6 yes, mode log)"
        self.assertIn(msg, out)
        self.assertIn(msg, self.log_after(token))
        self.assertEqual(self.drop_rule_actions(), LOG_ACTIONS)
        self.assertEqual(self.drop_rule_actions("/ipv6 firewall raw"), LOG_ACTIONS)
        self.assertEqual(self.num("/ipv6 firewall raw", 'comment~"^MBL:"'), 6)
        self.assertEqual(self.count6(self.active_list6()), LIST6_SIZE)
        token = self.mark()
        self.update()
        log = self.log_after(token)
        lines = self.counter_lines(log)
        self.assertEqual(len(lines), 1, log)
        self.assertEqual(lines[0].group(1), "would have dropped")
        self.assertIsNotNone(lines[0].group(6), log)  # the IPv6 counters
        # back to block, IPv6 still on (test_14o turns it off)
        out = self.install_mode("block")
        self.assertIn("ipv6 yes, mode block)", out)
        self.assertEqual(self.drop_rule_actions(), BLOCK_ACTIONS)
        self.assertEqual(self.drop_rule_actions("/ipv6 firewall raw"), BLOCK_ACTIONS)
        self.assertEqual(self.count6(self.active_list6()), LIST6_SIZE)
        token = self.mark()
        self.update()
        log = self.log_after(token)
        lines = self.counter_lines(log)
        self.assertEqual(len(lines), 1, log)
        self.assertEqual(lines[0].group(1), "dropped")
        self.assertIsNotNone(lines[0].group(6), log)
        self.assertEqual(self.reminders(log), [])

    def test_14o_disable_ipv6_cleans_up(self):
        # an MBL-OLD: IPv6 rule, as an aborted reinstall leaves, goes too
        self.chr.run('/ipv6 firewall raw add chain=prerouting src-address-list=mbl6-a '
                     'action=drop comment="MBL-OLD: MBL: in"')
        self.assertEqual(self.num("/ipv6 firewall raw", 'comment~"^MBL-OLD:"'), 1)
        before = self.mbl6_objects()  # something to clean up
        self.assertGreater(before["rules"], 0, before)
        self.assertGreater(before["entries"], 0, before)
        self.assertGreater(before["files"], 0, before)
        self.server.requests.clear()
        token = self.mark()
        out = self.install_ipv6("false")
        self.assertIn("/routeros-full.txt", self.server.requests)
        self.assertEqual(self.requests6(), [])
        self.assertEqual(self.mbl6_objects(), {"rules": 0, "entries": 0, "files": 0})
        self.assertEqual(self.num("/ipv6 firewall raw", 'comment~"^MBL-OLD:"'), 0)
        self.assertEqual(self.num("/ip firewall raw", 'comment~"^MBL:"'), 6)
        self.assertEqual(self.count(self.active_list()), LIST_SIZE)
        self.assertIn(f"MBL: installed (script {self.installed_version}, variant full, ipv6 no, mode block)",
                      self.log_after(token))
        self.assertIn("ipv6 no", out)
        source = self.update_source()
        self.assertIn("routeros-full.txt", source)
        self.assertIn(':local ipv6 "no"', source)

    def test_14p_install_log_mode(self):
        token = self.mark()
        out = self.install_mode("log")
        msg = f"MBL: installed (script {self.installed_version}, variant full, ipv6 no, mode log)"
        self.assertIn(msg, out)
        log = self.log_after(token)
        self.assertIn(msg, log)
        self.assertEqual(self.counter_lines(log), [], log)  # not on the installer's first load
        self.assertEqual(self.drop_rule_actions(), LOG_ACTIONS)
        self.assertEqual(self.num("/ip firewall raw", 'comment~"^MBL:"'), 6)
        self.assertEqual(self.mbl6_objects(), {"rules": 0, "entries": 0, "files": 0})
        self.assertEqual(self.count(self.active_list()), LIST_SIZE)
        self.assertIn(':local mode "log"', self.update_source())
        self.assertTrue(STAMP_RE.match(self.stats_since()), self.stats_since())
        # the router's own traffic to a listed host is counted and logged, not dropped
        if not os.environ.get("MBL_E2E_SSH"):  # an existing router's ping would leave it
            token = self.mark()
            self.ping_listed()
            self.assertIn("MBL-ROUTER", self.log_after(token))
        token = self.mark()
        self.update()
        log = self.log_after(token)
        self.assertIn("MBL: loaded", log)
        reminders = self.reminders(log)
        self.assertEqual(len(reminders), 1, log)
        self.assertIn("warning", reminders[0].split(" ", 1)[0])
        lines = self.counter_lines(log)
        self.assertEqual(len(lines), 1, log)
        self.assertEqual(lines[0].group(1), "would have dropped")
        self.assertIsNone(lines[0].group(6))
        if not os.environ.get("MBL_E2E_SSH"):
            self.assertGreaterEqual(int(lines[0].group(5)), PINGS, log)
        # a boot run: no reminder, no counters
        token = self.mark()
        self.update(boot=True)
        log = self.log_after(token)
        self.assertIn("MBL: loaded", log)
        self.assertEqual(self.reminders(log), [])
        self.assertEqual(self.counter_lines(log), [])

    def test_14q_reinstall_keeps_log_mode(self):
        out = self.install_mode("")
        self.assertIn("ipv6 no, mode log)", out)
        self.assertEqual(self.drop_rule_actions(), LOG_ACTIONS)
        self.assertIn(':local mode "log"', self.update_source())
        self.assertEqual(self.count(self.active_list()), LIST_SIZE)

    def test_14r_invalid_mode_rejected(self):
        before = (self.drop_rule_actions(), self.active_list(), self.count(self.active_list()),
                  self.update_source())
        self.server.requests.clear()
        try:
            out = self.install_mode("drop")
        except ChrError as e:
            out = str(e)
        self.assertIn('MBL: invalid mblMode "drop" (use "block" or "log").', out)
        self.assertIn("Nothing was changed.", out)
        self.assertEqual(self.server.requests, [])
        self.assertEqual(self.num("/ip firewall raw", 'comment~"^MBL:"'), 6)
        self.assertEqual((self.drop_rule_actions(), self.active_list(),
                          self.count(self.active_list()), self.update_source()), before)

    def test_14s_back_to_block(self):
        token = self.mark()
        out = self.install_mode("block")
        msg = f"MBL: installed (script {self.installed_version}, variant full, ipv6 no, mode block)"
        self.assertIn(msg, out)
        self.assertIn(msg, self.log_after(token))
        self.assertEqual(self.drop_rule_actions(), BLOCK_ACTIONS)
        self.assertEqual(self.num("/ip firewall raw", 'comment~"^MBL:"'), 6)
        self.assertIn(':local mode "block"', self.update_source())
        self.assertEqual(self.count(self.active_list()), LIST_SIZE)
        token = self.mark()
        self.update()
        log = self.log_after(token)
        self.assertEqual(self.reminders(log), [])
        lines = self.counter_lines(log)
        self.assertEqual(len(lines), 1, log)
        self.assertEqual(lines[0].group(1), "dropped")

    def test_14t_drop_counters(self):
        self.update()  # a fresh counter period
        since = self.stats_since()
        self.assertTrue(STAMP_RE.match(since), since)
        self.ping_listed()
        token = self.mark()
        self.update()
        log = self.log_after(token)
        lines = self.counter_lines(log)
        self.assertEqual(len(lines), 1, log)
        self.assertEqual(lines[0].group(1), "dropped")
        self.assertEqual(lines[0].group(2), since)
        self.assertGreaterEqual(int(lines[0].group(5)), PINGS, log)
        # the report reset the counters
        token = self.mark()
        self.update()
        log = self.log_after(token)
        lines = self.counter_lines(log)
        self.assertEqual(len(lines), 1, log)
        self.assertEqual(lines[0].group(5), "0", log)
        self.assertEqual(self.put('[/ip firewall raw get ([find where comment="MBL: router-out"]->0) packets]'), "0")

    def test_14u_boot_run_no_counters(self):
        token = self.mark()
        self.update(boot=True)
        log = self.log_after(token)
        self.assertIn("MBL: loaded", log)
        self.assertEqual(self.counter_lines(log), [], log)
        # after a reboot the period start is unset: the next report says "since boot"
        self.chr.run('/system script environment remove [find where name="mblStatsSince"]')
        token = self.mark()
        self.update()
        lines = self.counter_lines(self.log_after(token))
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0].group(2), "boot")
        self.assertTrue(STAMP_RE.match(self.stats_since()), self.stats_since())

    def test_07j_leaked_install_flag_does_not_stick_lock(self):
        # an installer killed at the wrong instant can leave mblInstallRun=true behind;
        # a later scheduler run must not count as "owned" unless the lock is really held
        self.install_sampled()
        self.chr.run(":global mblInstallRun true; :global mblRunning false")
        token = self.mark()
        self.update()
        self.assertIn("MBL: loaded", self.log_after(token))
        self.assertFalse(self.running())
        self.assertEqual(self.chr.run(":global mblInstallRun; :put $mblInstallRun").strip(),
                         "false")

    def test_15_final_uninstall(self):
        # uninstall removes both families: give IPv6 something to remove first
        self.install_ipv6("true")
        self.assertGreater(self.mbl6_objects()["rules"], 0)
        # and an MBL-OLD: rule per family, as an interrupted reinstall leaves
        for path, lst in (("/ip firewall raw", "mbl-a"), ("/ipv6 firewall raw", "mbl6-a")):
            self.chr.run(f'{path} add chain=prerouting src-address-list={lst} action=drop '
                         'comment="MBL-OLD: in"')
            self.assertEqual(self.num(path, 'comment~"^MBL-OLD:"'), 1, path)
        self.uninstall()
        self.assertEqual(self.num("/ip firewall raw", 'comment~"^MBL:"'), 0)
        self.assertEqual(self.num("/system script", 'name~"^mbl-"'), 0)
        self.assertEqual(self.count("mbl-a") + self.count("mbl-b"), 0)
        self.assertEqual(self.mbl6_objects(), {"rules": 0, "entries": 0, "files": 0})
        for path in ("/ip firewall raw", "/ipv6 firewall raw"):
            self.assertEqual(self.num(path, 'comment~"^MBL-OLD:"'), 0, path)


if __name__ == "__main__":
    unittest.main()
