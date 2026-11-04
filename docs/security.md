# Security guide

Resumen en español al final / Spanish summary at the end.

## Threat model

- The router never runs anything it downloads. `mbl-update` fetches `routeros-<variant>.txt` over
  HTTPS with `check-certificate=yes`, reads it as plain data, validates every line (IPv4/IPv6 or
  CIDR with an allowed prefix) and only adds addresses to an address list. It never imports or
  executes the file.
- What a compromised publisher account could do: publish a fake list. The worst outcome is
  blocking addresses (for example your DNS resolver or a service you use) or dropping the list
  to nothing. It cannot run code on your router.
- Built-in limits on that: entry-count bounds, no drop above 50 % and no growth above 2x between
  builds, a minimum entry count at load time, a cap on how much of the list the allowlist may
  remove, and a router-side allowlist (`mbl-allow`) that always wins. The GitHub download range
  and public DNS are never blocked.
- Residual risk: a malicious but well-formed list inside those limits. Keep `mbl-allow` entries
  for what you cannot lose.

## What you should do on GitHub (maintainers and forks)

1. **Two-factor authentication** on every account with write access:
   <https://github.com/settings/security>.
2. **Protect `main`** with a ruleset (or branch protection): pull request required, and the status
   check `test` required:
   <https://github.com/Psycomy/MikroBlockList/settings/rules>.
   The `e2e` workflow only runs when `routeros/`, `mbl/`, `tests/e2e/` or its own file change, so
   a required `e2e` check leaves a docs-only PR waiting forever. Require only `test`, or require
   `e2e` through a ruleset or workflow that treats a skipped check as passing.
3. **Actions permissions**: default workflow token `read`. Only the `publish` job writes code (the
   `lists` branch); the `health` and `report` jobs may write issues. Keep "Allow GitHub Actions to
   create and approve pull requests" off: no workflow needs it.
   Also limit which workflows may run and who can trigger them:
   <https://github.com/Psycomy/MikroBlockList/settings/actions>.
4. **Review tokens**: revoke personal access tokens you no longer use:
   <https://github.com/settings/tokens>.
5. **Review deploy keys**: remove any you do not recognise:
   <https://github.com/Psycomy/MikroBlockList/settings/keys>.

6. **Issue labels**: the issue forms apply the labels `false-positive` and `bug`; they must exist
   in the repository (create them once under *Issues → Labels*), or the issue is opened without one.
7. **Private vulnerability reporting** must be enabled by the repository owner (*Settings →
   Security → Private vulnerability reporting*), or the link below does not work.

## When the allowlist cap stops the build

The build fails (IPv4) or withholds IPv6 when the allowlist, static plus CDN, removes more than
10 % of the addresses or touches more than 10 % of the entries of the `full` list; the error gives
both numbers. There is no override flag, on purpose. Find the cause in the run's source table
(log and step summary): usually a feed has started listing CDN or cloud ranges wholesale (or a
CDN list grew into ranges a feed lists). Remove or narrow that source in `sources.toml` through a
pull request and re-run the workflow. Adding the range to `allowlist.txt` does not help: the cap
counts the static allowlist and the CDN ranges together, so a larger allowlist only removes more.
Until it is fixed nothing new is published for IPv4 (for IPv6, no IPv6 files) and routers keep
their last good list.

## Reporting a vulnerability

Use GitHub private vulnerability reporting (the owner must have enabled it, see above):
<https://github.com/Psycomy/MikroBlockList/security/advisories/new>.
Please do not open a public issue for security problems.

## Resumen en español

- El router no ejecuta nada de lo que descarga: solo lee datos, valida cada línea y añade
  direcciones a una lista. Una cuenta comprometida solo podría publicar listas falsas, es decir,
  bloquear IPs (o vaciar la lista), nunca ejecutar código en tu router. Los límites de tamaño, la
  caída del 50 %, el crecimiento de 2x, el tope de la allowlist y tu `mbl-allow` reducen ese riesgo.
- En GitHub: activa 2FA, protege `main` (PR obligatorio y estado `test` requerido; `e2e` solo corre
  si cambian `routeros/`, `mbl/` o `tests/e2e/`, así que exigirlo deja pendientes los PR de solo
  documentación, salvo con un *ruleset* que acepte una comprobación omitida), deja los permisos de
  Actions en solo lectura (salvo `publish` y las incidencias automáticas;
  sin permitir que Actions cree PR), restringe quién puede lanzar workflows y revisa tokens
  y *deploy keys* (enlaces arriba). Crea las etiquetas `false-positive` y `bug` que usan las
  plantillas de incidencias.
- Si el tope de la allowlist (10 % de direcciones o de entradas) detiene el build, no hay opción para
  saltárselo: quita o acota, mediante PR, la fuente que lista rangos de CDN enteros. Añadir el rango a
  `allowlist.txt` no ayuda (cuenta en el tope). Mientras, siguen publicadas las últimas listas.
- Para informar de una vulnerabilidad, usa el informe privado de GitHub (enlace arriba; el
  propietario debe tenerlo activado), no una incidencia pública.
