# Maintenance guide

Resumen en español al final / Spanish summary at the end.

The project runs on its own: a daily build publishes the lists, and routers fetch them. Problems
show up as **issues opened by the workflows**, so you get a GitHub e-mail instead of having to
check anything. Each issue is updated when the problems change and closed automatically when a
later run is healthy.

| Issue label | Opened by | When |
|---|---|---|
| `build-health` | `build` workflow, daily schedule | a source failed or came back empty, a CDN list fell back to yesterday's copy or the committed snapshot, IPv6 was withheld, the lists are stale or the run did not publish |
| `routeros-e2e` | `routeros-e2e` workflow, weekly | the installer fails its end-to-end tests on the **latest stable** RouterOS |

You can run the same health check locally at any time:

```bash
python3 -m mbl health --stats https://raw.githubusercontent.com/Psycomy/MikroBlockList/lists/stats.json
```

It prints one line per problem (nothing when healthy) and exits with 3 when there is a problem.

## Lookup page

The "is this IP listed?" page lives in `site/` and is served at
<https://psycomy.github.io/MikroBlockList/>. It reads `lookup.json` from the `lists` branch (written
by the daily `build`) and is deployed by the `pages` workflow on pushes that touch `site/**`.

**One-time setup:** in the repository go to **Settings → Pages → Build and deployment → Source:
GitHub Actions**. Until that is set, the first `pages` run fails; rerun it afterwards.

## `build-health` lines

- **`source X failed: ...`**: the download failed. One bad day is normal (the source is optional
  and the build tolerates it). If it lasts about a week, open the source URL from `sources.toml`:
  - moved: update `url` through a PR;
  - gone or no longer free for home use: set `enabled = false` with a comment saying why (as was
    done for ThreatFox) and, if you want a replacement, check its license first.
  A *required* source that fails stops the whole build instead (see "did not publish" below).
- **`source X returned no entries`**: the download worked but nothing was parsed, which usually
  means the format changed. Compare the file with its `format` in `sources.toml`; fix the parser in
  `mbl/parsers.py` with an updated fixture in `tests/fixtures/`, or disable the source.
- **CDN allowlist lines.** Each build publishes the CDN ranges it used as `cdn-snapshot.txt` on
  the `lists` branch. When a provider's live list is rejected, the build falls back to yesterday's
  published copy, then to the committed `allowlist-cdn.snapshot.txt`, then goes on without it.
  - **`CDN allowlist X: live list rejected, used yesterday's published copy`**: one bad day is
    normal. If it lasts, the provider changed its URL or format (see `PROVIDERS` in
    `mbl/cdn.py`); fix the provider entry. The fallback block keeps its date, and after 90 days the
    warning says how old it is.
  - **`... used the committed snapshot`**: yesterday's copy was not usable either (first build, or
    the `lists` branch could not be read). Fix the provider if needed, and refresh the committed
    copy: `python3 -m mbl cdn-snapshot` and open a PR with the updated
    `allowlist-cdn.snapshot.txt`.
  - **`... and no snapshot, built without it`**: no fallback at all: that provider's ranges are not
    allowlisted until it is fixed, so fix it soon.
- **`IPv6 lists were withheld`**: IPv4 was published, IPv6 kept the previous files. The run log has
  the reason (`warning: IPv6 not published, ...`), usually an IPv6 count outside its limits.
- **`the build did not publish`** or **`the published lists are N days old`**: open the run log.
  - A safety check stopped it (drop over 50 %, growth over 2x, entry limits). If the change is
    expected, for example after adding a source, run the `build` workflow by hand with
    **allow_growth** (growth only). A real drop usually means a source broke: see above.
  - The allowlist cap stopped it: see [the security guide](security.md#when-the-allowlist-cap-stops-the-build).
  Routers keep their last good list meanwhile and log `MBL: list is N days old` after a few days.
- **`warning: lookup.json not published (...)`** (in the run log, not an issue): the data for the
  [lookup page](https://psycomy.github.io/MikroBlockList/) was skipped; the lists were still
  published. `lookup.json` is then absent from the `lists` branch until the next good build, and the page shows "list unavailable". The reason is in the parentheses; once the cause is gone, rerun the `build` workflow to fix it.
- **`warning: DB-IP unavailable (...); lookup.json without countries`**: the DB-IP Country Lite
  file (CC BY 4.0, cached monthly in Actions by `python -m mbl dbip-fetch --cache .dbip`) was
  missing or unreadable. The page works but shows no countries. Rerun the `build` workflow; if it
  persists, check the download URL in `mbl/geo.py`.
- **`stats.json unavailable`**: the `lists` branch or raw.githubusercontent.com could not be read;
  check that the branch exists and that the latest `build` run published.

## `routeros-e2e` issue

A new RouterOS release probably broke something. First check which step failed: a setup step
(apt, QEMU, the image download) is a CI problem, not a RouterOS one, and the next weekly run
usually closes the issue. Otherwise download the `routeros-e2e-logs` artifact of the run (console
and router logs), then reproduce locally with that version:

```bash
MBL_E2E=1 MBL_E2E_CHR_VERSION=<version> python3 -m unittest tests.e2e.test_routeros -v
```

Fix `routeros/install.rsc` (bump the script version, see below). Once it passes, you can pin the new
version in `tests/e2e/chr-version.txt` (`<version> <sha256 of chr-<version>.img.zip>`; the run's
summary prints the checksum).

## Dependabot pull requests

Once a month Dependabot proposes updated versions of the GitHub Actions used by the workflows, in
a single PR. Merge it when its checks pass.

## Scheduled workflows and inactivity

GitHub disables scheduled workflows in a repository with no activity for 60 days, and sends an
e-mail before doing so. If you get that e-mail, re-enable the workflow under *Actions* (or merge
any PR, such as the Dependabot one). While the build is disabled no issue can warn you, but routers
log `MBL: list is N days old`.

## Changing the router script

Every change to `routeros/install.rsc` needs:

1. the new version in both `VERSION` and `mblScriptVersion` (a unit test checks they match);
2. a `CHANGELOG.md` entry;
3. a green `e2e` check on the PR.

After the merge the published lists carry the new `script=` value and installed routers log
`MBL: script X available (installed Y)`; reinstalling with the one-liner upgrades them.

## Once a year

- Review the terms of every source (`license_note` in `sources.toml`, the README table): still
  free, still fine for home / non-commercial use.
- Look for new free sources that add coverage without overlapping the current ones.
- Refresh the committed CDN snapshot (the last-resort fallback): `python3 -m mbl cdn-snapshot`,
  then a PR with `allowlist-cdn.snapshot.txt`.

## Resumen en español

- El proyecto funciona solo. Los problemas llegan como **incidencias que abren los workflows**, que
  recibes por correo, y que se cierran solas cuando una ejecución posterior va bien:
  - `build-health`, diaria: fuente caída o vacía, CDN usando la copia de ayer o la del
    repositorio, IPv6 retenida, listas sin actualizar o build sin publicar;
  - `routeros-e2e`, semanal: la última RouterOS estable rompe el instalador.
- Comprobación manual: `python3 -m mbl health --stats <URL de stats.json>`.
- Fuente caída una semana: corrige la URL o desactívala (`enabled = false` con el motivo). Fuente vacía:
  cambió el formato (parser y fixture). Build detenido: mira el log; si el crecimiento es esperado,
  lanza el workflow a mano con **allow_growth**; si es el tope de la allowlist, ver la guía de seguridad.
- CDN: cada build publica en `lists` los rangos usados (`cdn-snapshot.txt`). Si una lista en vivo
  se rechaza, se usa la copia publicada ayer, luego `allowlist-cdn.snapshot.txt` del repositorio y,
  si no, se sigue sin ese proveedor. La copia del repositorio solo se renueva a mano de vez en cuando
  (`python3 -m mbl cdn-snapshot` y PR): cuando `build-health` diga "used the committed snapshot" o
  una vez al año.
- `routeros-e2e`: reproduce en local con `MBL_E2E_CHR_VERSION=<versión>`, corrige el instalador y
  sube la versión del script.
- Dependabot: un PR al mes con las Actions actualizadas; fusiónalo si el CI pasa.
- Si GitHub avisa de que va a desactivar los workflows programados por inactividad (60 días),
  reactívalos en *Actions* o fusiona cualquier PR.
- Cada cambio en el script del router: `VERSION` y `mblScriptVersion`, CHANGELOG y `e2e` en verde.
- Una vez al año: revisa las licencias de las fuentes, busca fuentes nuevas y renueva la copia de
  las CDN del repositorio.
