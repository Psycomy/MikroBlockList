# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/). The version in use is in [`VERSION`](VERSION), and the
published list carries it as `script=<version>` in its header.

## [3.5.1] - 2026-10-26

### Added
- Lookup page at <https://psycomy.github.io/MikroBlockList/>: search an IPv4, IPv6 or CIDR and see
  which sources list it and where in the world the listed ranges are. It runs in the browser and
  sends nothing anywhere (ES/EN). Deployed by the new `pages` workflow.
- `lookup.json` on the `lists` branch, built daily with countries from DB-IP Country Lite
  (CC BY 4.0, cached monthly). If it cannot be built the run only warns and the lists still publish.
- A test that the false-positive issue form lists every enabled source.

### Changed
- The installer warns (without stopping) when the `full` variant is installed on a small router:
  MIPS, PowerPC or Tile CPU, or less than 200 MiB of RAM, and suggests `lite`. On an RB2011UiAS
  (MIPS 600 MHz, 128 MiB) a daily full update kept the CPU at 100 % for about 18 minutes; lite took
  41 seconds.
- The installer writes the settings into the stored `mbl-update` with a single change instead of
  six. RouterOS logs every change of a script's source in full, so an install now adds two copies
  of the script to the system log instead of seven.
- `NOTICE.md` has a "Notice" column with each source's copyright line (Spamhaus' copyright and
  date, as their DROP FAQ asks).
- The false-positive form and the README link to the lookup page.

### Tested
- First run on physical hardware: RB2011UiAS with RouterOS 7.25 (see `docs/testing-chr.md`).

## [3.5.0] - 2026-10-22

### Added
- Log-only mode: `:global mblMode "log"` before the install. The three drop rules count
  (`MBL: in`, `passthrough`) or log (`MBL: out` as `MBL-OUT`, `MBL: router-out` as `MBL-ROUTER`)
  instead of dropping, and each update logs a warning that nothing is blocked. Reinstall with
  `:global mblMode "block"` to block. The mode is kept on a reinstall, like the variant.
- Drop counters: each update (not at boot, not the installer's first load) logs
  `MBL: dropped since <time>: in N, out N, router-out N packets` (`would have dropped` in log mode,
  plus `; ipv6 ...` with IPv6 on) and resets the counters of those rules.
- Automatic maintenance issues, each closed again by the next healthy run:
  - `build-health` (daily): failed or empty sources, CDN snapshot fallback, IPv6 withheld,
    stale lists, or a run that did not publish. The check is also a command:
    `python -m mbl health --stats <url>`.
  - `routeros-e2e` (weekly): the latest stable RouterOS fails the end-to-end tests.
  - `cdn-snapshot` (monthly): the live CDN ranges differ from the snapshot. The refreshed file is
    pushed to a branch, and the issue links to open the PR.
- Dependabot for the GitHub Actions versions (monthly, one grouped PR).
- `docs/maintenance.md`: what to do for each issue, script releases, yearly source review.

## [3.4.1] - 2026-10-22

### Changed
- Lists use only the new name in their first line: `# MikroBlockList v3 ...`. The installer no longer accepts the
  pre-rename `# MikrotikBlockList ` header. Routers on script 3.3.2 or older (and v2) stop
  loading the list and must reinstall; 3.4.0 accepts both headers.

### Removed
- v2 support: the legacy `blocklist.rsc` is no longer published, and the installer no longer
  cleans up v2 boot copies (`mbl-*.rsc`). The uninstaller still removes them.

## [3.4.0] - 2026-10-22

### Changed
- Project renamed from MikrotikBlockList to **MikroBlockList** (repository
  `Psycomy/MikroBlockList`), so the name no longer uses the MikroTik trademark. GitHub redirects the
  old URLs, so routers already installed keep updating; reinstall to store the new URL.
- The installer accepts list headers starting with `# MikroBlockList ` as well as
  `# MikrotikBlockList `. Published lists keep the old header until 2027-04-30 so routers on
  script 3.3.2 or older keep loading them.
- The legacy v2 `blocklist.rsc` keeps its `# MikrotikBlockList v2` first line too: the v2 updater
  rejects the file without it.

### Added
- Trademark notice (not affiliated with MikroTik) in the README and NOTICE.md.

## [3.3.2] - 2026-10-22

### Fixed
- Lock hardening in the stored `mbl-update` script: a run now counts as the installer's own only if
  `mblInstallRun` is set and the lock (`mblRunning`) appears held (best-effort: there is no lock-owner token). Before, a leaked
  `mblInstallRun` flag (installer killed at the wrong instant) made a later scheduler run take the
  lock and never release it. Reinstall to pick it up.

### Added
- C2IntelFeeds (`c2intel`, 30-day C2 IPs, CC BY-NC-SA 4.0) re-added to `full` and `lite`. It had been
  removed as fully contained in ThreatFox; with ThreatFox disabled it is again the main dedicated C2
  feed (+~220 IPs; `lite` back to about 2.35k IPv4 entries).

### Removed
- ThreatFox source disabled (`enabled = false` in `sources.toml`). Its public CSV export now returns
  410 and the API requires an abuse.ch Auth-Key; abuse.ch's Terms of Use (§7.3) forbid copying or
  making derivative works from their data without express consent, and ThreatFox is no longer CC0
  (§3.4), so this project must not republish it. `lite` drops to about 2.1k IPv4 entries. No router
  change: installed routers keep working and need no reinstall.

## [3.3.1] - 2026-10-21

### Added
- Automatic CDN allowlist (`mbl/cdn.py`): Cloudflare, Fastly and CloudFront ranges are downloaded
  on every build and subtracted from the lists, with fallback to `allowlist-cdn.snapshot.txt`
  (refreshed by hand with `python -m mbl cdn-snapshot --out allowlist-cdn.snapshot.txt`, via PR).
  Providers are rejected when unreadable, with fewer than 5 IPv4 prefixes, or too wide. A snapshot
  block older than 90 days is used with a louder warning.
- A 10 % allowlist cap measured two ways: addresses removed and entries touched (entries overlapping
  any allowlist range). Either one over 10 % fails the build for IPv4 `full` and withholds IPv6; the
  error gives both. `stats.json` gains `cdn_allowlist` (per provider) with `cdn_allowlist.cap`
  (both measures per family, in percent).
- Issue forms (false positive, bug report) and `docs/security.md` (threat model, GitHub hardening
  checklist, private vulnerability reporting).
- README: diagram with both families and variants, CDN allowlist section, links to the new docs.

### Changed
- `allowlist.txt` keeps only GitHub and public DNS; Cloudflare ranges now come from the automatic list.
- Reinstall has no gap: existing `MBL:` rules are renamed `MBL-OLD:`, the new ones are created, and
  only then are the old ones removed; an aborted install keeps protecting, and the next install or
  uninstall cleans up `MBL-OLD:` (it also finds the active list through `MBL-OLD: MBL: in`).
- Reinstall and update no longer race: the installer refuses to start while `mbl-update` holds its
  lock (`mblRunning`, nothing changed); it takes the lock in one check-and-set right before its first
  change and holds it through its own first load, releasing it on any error and at the end. `mbl-update` skips a family without its `MBL: in` rule, and drops a
  load whose `MBL: in` rule changed meanwhile (the active list is kept).
- CI and end-to-end hardening: the manual workflow version input is validated strictly, and the
  e2e cleanup also removes `MBLTEST` static DNS entries.

## [3.3.0] - 2026-10-21

### Added
- Optional IPv6, off by default. New source `spamhaus-drop-v6` (format `jsonl_cidr`, `full` and `lite`).
  IPv6 sources today: Spamhaus DROPv6 (91 networks), blocklist.de (about 523), Turris (49) and
  ThreatCluster (1): 648 IPv6 entries in `full`, 86 in `lite`.
- `normalize6`: drops non-global IPv6 (ULA, link-local, documentation, multicast, `::/8`,
  IPv4-mapped, NAT64, 6to4, Teredo) and anything broader than /19, subtracts the IPv6 allowlist
  (Cloudflare, GitHub's `2606:50c0::/32`, public DNS) and aggregates.
- New published files: `routeros6-full.txt`, `routeros6-lite.txt`, `blocklist6.txt`,
  `blocklist6-lite.txt` (header `family=ipv6`; `/128` written without mask). IPv6 limits: `full`
  50 to 20,000, `lite` 20 to 5,000, with the 50% drop and 2x growth checks. Drop and growth
  compare against the last published IPv6 baseline: on a day IPv6 is withheld, `stats.json` keeps
  the previous IPv6 totals (with `published: false`), so a bad day cannot become the new baseline.
- Any unexpected error in the IPv6 build path also withholds IPv6 (with a warning) instead of
  failing the build; the IPv4 files are still written.
- `NOTICE.md` shows IPv4 `entries` and IPv6 `entries6` in separate columns.
- Best effort: if an IPv6 variant fails its checks, no IPv6 file is published that day, with a
  warning on stderr and in the step summary; IPv4 is still published and the exit code stays 0.
  `stats.json` gains `ipv6` (`published`, `variants.<v>.total`) and per-source `entries6`;
  `entries` now counts IPv4 only.
- Installer: `:global mblIpv6 true; ` before the one-liner (combinable with `mblVariant`) creates
  `mbl6-a`, `mbl6-b`, `mbl6-allow`, six `MBL:` rules in `/ipv6 firewall raw` and `mbl6-last.txt`.
  With the global empty the installed choice is kept; `false` removes every MBL IPv6 object (your
  own `mbl6-allow` entries are kept); any other value stops with `:error` before changing anything.
  The uninstaller removes both families.
- `mbl-update` processes `routeros6-<variant>.txt` after the IPv4 update (lines validated with
  `:toip6` and a prefix from 19 to 128, header `family=ipv6`, at least 200 bytes and 50/20
  entries). An IPv6 failure logs `MBL: ipv6 ...` and never touches IPv4 lists, rules or files.
- Final message: `MBL: installed (script 3.3.0, variant <v>, ipv6 <yes|no>)`; with IPv6 on and a
  failed first IPv6 load it ends in `ipv6 yes - first ipv6 load failed, see log)`.
- End-to-end cases `test_14k` to `test_14o`.

### Upgrade from 3.2.0
Routers on 3.2.0 will log the newer-script notice and can reinstall with the install command. They
keep their variant and stay IPv4-only unless `:global mblIpv6 true; ` is set first. The global
persists until reboot, so set it explicitly (or to `""`) when reinstalling in the same session.

## [3.2.0] - 2026-10-21

### Added
- Two variants of the list: `full` (default, 38,573 entries at release) and `lite` (4,308 entries),
  built from Spamhaus DROP, DShield, Emerging Threats compromised, ThreatFox and ThreatCluster.
  Each source in `sources.toml` declares `variants` (default `["full"]`); every `lite` source must
  also be in `full`.
- New published files: `routeros-lite.txt` and `blocklist-lite.txt` (headers carry `variant=lite`).
  `routeros-full.txt`, `blocklist.txt` and the legacy `blocklist.rsc` (full only) are unchanged.
  `stats.json` gains `variants` (`{"full": {"total": N}, "lite": {"total": M}}`); the top-level
  `total` stays equal to `full`.
- Per-variant safety checks: `full` 5,000 to 150,000 entries, `lite` 1,000 to 20,000; in both, no
  drop over 50% and no growth over 2x (`--allow-growth` applies to both) against that variant's
  previous total. If either variant fails, nothing is published. A drop no larger than the previous
  entries of optional sources that failed this run skips only the drop check, with a warning.
- Installer: `:global mblVariant "lite"; ` before the one-liner installs `lite`; `"full"` switches
  back. With the global empty, the installer keeps the installed variant (or uses `full`). Any other
  value stops with `:error` before changing anything.
- `mbl-update` downloads `routeros-<variant>.txt`, requires a file of at least 100,000 bytes (`full`)
  or 20,000 (`lite`), and rejects a header whose `variant=` differs from the installed one (`bad header`).
- Final message: `MBL: installed (script 3.2.0, variant <v>)`.
- End-to-end cases `test_14f` to `test_14j` (install lite, reinstall keeps lite, invalid value,
  variant mismatch, back to full).

### Upgrade from 3.1.0
Routers on 3.1.0 will log the newer-script notice and can reinstall with the install command. They
stay on `full` unless `:global mblVariant "lite"; ` is set first. The global persists until reboot,
so set it explicitly (or to `""`) when reinstalling in the same session.

## [3.1.0] - 2026-10-21

### Added
- Update notice: after a successful load, `mbl-update` compares the installed script version with the
  one published in the list header and logs a single `warning`
  (`MBL: script <published> available (installed <installed>) - reinstall: ...`) when the published
  one is newer. The comparison is numeric per `MAJOR.MINOR.PATCH` (`3.10.0` is newer than `3.9.0`).
- A published version that is not `MAJOR.MINOR.PATCH` is ignored: no notice and no error. The notice
  never makes a load fail and nothing read from the file is executed.
- The router never updates itself; reinstalling stays a manual step.
- CI check that `VERSION` and the installer version stay in sync.
- End-to-end cases `test_14b` to `test_14e` for the notice.
- The installer's final message now includes the installed script version (`MBL: installed (script 3.1.0)`).

### Upgrade from 3.0.0
Routers installed before 3.1.0 (v2 and 3.0.0) do not contain the version check, so they never show the
newer-script notice. Run the install command once more to get it; later releases are then announced.

## [3.0.0] - 2026-10-21

### Changed
- Safe ingestion: `mbl-update` no longer uses `/import` on the downloaded list. It reads the file in
  chunks and validates every line as an IPv4 address or prefix before adding it, so nothing in the file
  is ever executed. It also checks the header/footer, the entry count and the share of invalid lines
  (rejected above 1 %); a rejected download keeps the active list.
- The list is published as plain text, `lists/routeros-full.txt`; its header carries the script version (`script=`, from the new `VERSION` file).
- The backup of the last good list is `mbl-last.txt` (v2 used `mbl-last.rsc`).

### Added
- End-to-end cases for the new reader: v2 to v3 upgrade, injected lines, count mismatch, CRLF and
  invalid ratio; CI fetches tags so the upgrade test can install the `v2.0.0` script.

### Upgrade from v2
Run the install command again. The installer replaces the v2 updater, the first load removes the old
`mbl-*.rsc` files (`MBL: removed v2 files`) and the active list is never left empty.
`blocklist.rsc` keeps being published unchanged for v2 routers until at least 2027-04-30.

## [2.0.0] - 2026-10-19 (tagged 2026-10-21)

The rewrite landed on 2026-10-19; the `v2.0.0` tag was created on 2026-10-21 and points at the
phase-1 merge (`111382f`), so the items below up to that merge belong to this release.

### Added
- Rewrite of the generator and the RouterOS installer: daily build of an IPv4 blocklist published on the
  `lists` branch, raw-firewall rules on two alternating lists (`mbl-a`/`mbl-b`), schedulers
  `mbl-daily` and `mbl-boot`, an allowlist and an uninstaller. Validated on a CHR with RouterOS 7.24.5
  (minimum supported: 7.21).
- The build refuses to publish when the total more than doubles (2026-10-19).
- Stale-list warning in `mbl-update` (`MBL: list is N days old`), the `--allow-growth` override
  (also a manual `allow_growth` workflow input) and CI on Node 24 actions (PR #1, 2026-10-19).
- Source study applied to `sources.toml` (PR #2, 2026-10-20; see `docs/source-study-2026-10-20.md`).
- RouterOS end-to-end tests on a disposable CHR (QEMU/KVM) in CI, the `routeros-e2e` workflow, and an
  optional `mblUrlBase` global to point the installer at a test server (phase 1, PR #3, 2026-10-21).
