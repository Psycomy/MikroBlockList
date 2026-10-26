# Validación en CHR v7 (Proxmox)

Guía para montar un RouterOS CHR de pruebas y registrar el resultado de la Tarea 9. Los scripts a validar son `routeros/install.rsc` y `routeros/uninstall.rsc`.

## 1. Crear la VM CHR en Proxmox

1. Descarga la imagen **CHR raw** (`chr-<ver>.img.zip`) de <https://mikrotik.com/download> (sección *Cloud Hosted Router*). Elige la última **7.x stable, >= 7.21**.
2. En el host Proxmox:

```bash
VMID=9100                      # cualquier ID libre
BRIDGE_LAN=vmbrTEST            # bridge aislado de pruebas (sin salida)
unzip chr-*.img.zip            # produce chr-<ver>.img

qm create $VMID --name mbl-chr --memory 256 --cores 1 \
  --net0 virtio,bridge=vmbr0 --net1 virtio,bridge=$BRIDGE_LAN \
  --ostype l26 --serial0 socket
qm importdisk $VMID chr-*.img local-lvm
qm set $VMID --scsi0 local-lvm:vm-$VMID-disk-0 --scsihw virtio-scsi-pci --boot order=scsi0
qm start $VMID
```

- `net0` = **ether1 = WAN**: bridge con salida a Internet (necesario para `/tool fetch` a GitHub).
- `net1` = **ether2 = LAN**: bridge aislado de pruebas. Si quieres un host de prueba, conéctalo a este bridge.
- Si el almacenamiento no es `local-lvm`, ajusta el nombre. Comprueba el nombre exacto del disco con `qm config $VMID` (aparece como `unused0`).
- Para el rendimiento (caso 13) puedes subir a `--memory 512` y repetir.
- Consola: `qm terminal $VMID` (serie) o la consola web. Usuario `admin`, contraseña vacía.

## 2. Configuración mínima en el CHR

Por consola (`qm terminal $VMID`):

```routeros
/user set admin password="CAMBIAME"
/ip dhcp-client add interface=ether1 disabled=no
/ip address add address=192.168.88.1/24 interface=ether2
/interface list add name=WAN
/interface list add name=LAN
/interface list member add list=WAN interface=ether1
/interface list member add list=LAN interface=ether2
/ip service set ssh disabled=no
/system identity set name=mbl-chr
/ip dhcp-client print
/system resource print
```

Anota la IP que obtenga `ether1` (`/ip dhcp-client print`).

### Clave SSH

Copia la clave pública de este equipo (`/home/antonio/.ssh/antonio.pub`) al CHR. Con SSH por contraseña ya activo:

```bash
scp /home/antonio/.ssh/antonio.pub admin@<IP_CHR>:antonio.pub
```

Alternativa sin red (pegar por consola): `/file add name=antonio.pub contents="<contenido de antonio.pub>"`.

Después, en el CHR:

```routeros
/user ssh-keys import public-key-file=antonio.pub user=admin
```

Comprueba desde este equipo: `ssh -i /home/antonio/.ssh/antonio admin@<IP_CHR> /system resource print` (si tu clave privada es la de por defecto, no hace falta `-i`).

### Avisar a Claude

Dile a Claude la **IP del CHR** (la de ether1, alcanzable desde este equipo) y confirma que el SSH con clave funciona. Con eso Claude lanza los casos de prueba por SSH.

## 3. Casos de prueba

> Las secciones 3 a 6 (casos 1-13, `mbl-last.rsc`, `/import`) son el registro histórico de la v2 (2026-10-19). Lo de la v3 está en la sección 7.

Rellena *Estado* con OK / FALLO.

**Resultado del 2026-10-19:**

- **Entorno:** CHR **RouterOS 7.24.5 stable** (256 MiB, 1 vCPU, una sola NIC: `ether1` = WAN; LAN = bridge vacío `bridge-lan`).
- **Lista:** de prueba, con 32242 entradas, de la rama `lists-test`.
- **Scripts:** los de los commits `fix(routeros)` de la Tarea 9.
- **Casos:** 13/13 en OK, dos con límites documentados:
  - **caso 3:** la parte "host LAN" no se puede verificar sin un host LAN; solo se probó el tráfico del propio router y la configuración de la regla;
  - **caso 9:** se simuló quitando la ruta por defecto, no desactivando `ether1`.
- **Cambio sin repetir en CHR:** el aviso `MBL: header check skipped` (solo log, ronda de revisión 1) no se ha vuelto a ejecutar en el CHR.
- **Versión mínima:** **7.21**. Viene de la documentación (`builtin-trust-store` con el componente `fetch`, changelog de 7.21); en CHR solo se ha probado 7.24.5.

| # | Caso | Cómo | Resultado esperado | Estado |
|---|------|------|--------------------|--------|
| 1 | Instalación limpia | Fetch + `/import install.rsc` | 5 reglas raw `MBL:` (+ allow-router-out), 2 schedulers (`mbl-daily`, `mbl-boot`), script `mbl-update`; `mbl-a` con >= 1000 entradas | OK (7.24.5): 6 reglas `MBL:` arriba del todo (antes de las del usuario), 2 schedulers, `mbl-update`; `mbl-a` = 32242 |
| 2 | Bloqueo entrante | Añadir a mano una IP de prueba a la lista activa; generar tráfico desde WAN (`/tool fetch` desde un host) o mirar contadores | `MBL: in` cuenta paquetes | OK: con `MBL: router-out` desactivada un momento y 9.9.9.9 en la lista activa, las respuestas al `/ping` entran por WAN y `MBL: in` cuenta 2 paquetes |
| 3 | Bloqueo saliente | `ping` desde un host LAN a una IP de la lista; `/ping` desde el router | Log `MBL-OUT`; el `/ping` del router se bloquea por `MBL: router-out` | OK (router) / no verificable sin host LAN: `/ping` a 9.9.9.9 y a 1.7.51.205 = 100 % de pérdida, `MBL: router-out` cuenta 5 paquetes. La regla `MBL: out` (LAN, `log=yes`, `MBL-OUT`) se acepta y está bien configurada, pero el CHR no tiene host LAN |
| 4 | Actualización | `/system script run mbl-update` | Las reglas pasan a `mbl-b`; `mbl-a` queda vacía | OK: reglas a `mbl-b`, `mbl-a` = 0, `mbl-last.rsc` renovado; ~19 s en total |
| 5 | Fichero truncado | Servir un `.rsc` sin el pie | Se mantiene la lista anterior; error en el log | OK: descarga cortada sin pie (todas las entradas, `complete=false`), cortada a media línea (16990 entradas) y fichero truncado como `mbl-last.rsc` en modo boot. En los tres casos se mantiene la lista activa y aparece `MBL: import ... failed` en el log. Además, un fichero que no es una lista MBL se rechaza antes del import y uno < 100000 bytes da `too small` |
| 6 | URL caída | Apuntar a una URL inexistente y ejecutar | Se mantiene la lista anterior | OK: con 404 en GitHub y con un host inexistente aparece `MBL: download failed`; lista y reglas sin cambios |
| 7 | Ejecución concurrente | Dos `/system script run mbl-update` seguidos | El segundo sale por el lock | OK: el segundo run registra `MBL: update already running, skipped`; el primero termina bien |
| 8 | Reinicio con red | `/system reboot` con WAN | La lista se carga desde `mbl-last.rsc` en segundos y luego se actualiza | OK: con 8 s de uptime ya hay 32242 entradas cargadas desde `mbl-last.rsc`; ~80 s después del arranque se descarga una lista nueva y se pasa a la otra lista |
| 9 | Arranque sin red | `ether1` desactivada + reinicio | La lista se carga desde `mbl-last.rsc` | OK (simulado): sin ruta por defecto (`add-default-route=no`; rutas estáticas solo hacia el PC de gestión) y reinicio: carga desde `mbl-last.rsc` en segundos; el run de +60 s da `download failed` y se mantiene la lista |
| 10 | Reinstalación | Volver a importar `install.rsc` | Nada duplicado; se conserva `MBL: admin` (y la lista activa) | OK: 2 schedulers y 1 script, sin duplicados; `MBL: admin` sin duplicar (misma creation-time) y la entrada del usuario en `mbl-allow` se conserva. Con las dos listas pobladas, las reglas se recrean sobre la activa previa |
| 11 | IP de gestión listada | Poner la IP del admin en la lista activa | Se mantiene el acceso SSH | OK: con 192.168.100.4 (IP de gestión) en la lista activa, SSH sigue funcionando gracias a `MBL: allow-in` (dead-man de 3 min como red de seguridad) |
| 12 | Desinstalación | `/import uninstall.rsc` | No queda nada `mbl`/`MBL:`; la config del usuario intacta | OK: 0 reglas `MBL:`, 0 schedulers o scripts `mbl`, sin `mbl-a`/`mbl-b`/`MBL: admin`, sin ficheros `mbl-*` ni `uninstall.rsc`, sin globals `mbl*`. Se conservan la regla raw, el script, las address-lists y la global del usuario, y la entrada propia en `mbl-allow`. La desinstalación **no** revierte el componente `fetch` que el instalador pudo añadir a `builtin-trust-store`; se queda como esté |
| 13 | Rendimiento | `/system resource print` antes y después, con la lista real; medir el tiempo de importación | Tiempo y RAM anotados (aprox. 32k entradas) | OK: ver las notas de rendimiento |

Notas de rendimiento (CHR 7.24.5, 1 vCPU, 256 MiB, lista de prueba con 32242 entradas, 1.7 MB):

- **Tiempo:**
  - fetch ~1 s;
  - import ~7 s (CPU al 100 %);
  - borrado de la lista vieja ~8–13 s;
  - `mbl-update` completo ~19 s;
  - instalación limpia completa ~11 s.
- **RAM libre:**
  - recién arrancado y sin lista: 52.8 MiB;
  - con la lista cargada: ~25–31 MiB (tras un reinicio con la lista cargada: 26.7 MiB);
  - mínimo durante una actualización, con las dos listas llenas un momento: 18.9–19.9 MiB, unos 6–11 MiB por debajo del reposo.
- **Coste:** cada lista de unas 32k entradas ocupa unos 6–10 MiB.
- **Deriva:** sin reiniciar, la RAM libre baja poco a poco: de 29.6 a 25.5 MiB en unas 8 actualizaciones. Tras un reinicio y una carga se recupera a 26.7 MiB.
- **Margen:** con unos 6–10 MiB por cada 32k entradas, una lista cerca de `MAX_TOTAL` = 150000 ocuparía unos 28–47 MiB. Durante una actualización conviven las dos listas, así que harían falta unos **56–94 MiB**. En equipos de 256 MiB (unos 50 MiB libres sin la lista) solo caben listas bastante por debajo del tope.

Todo el tiempo de la actualización la protección sigue activa (la lista activa no se toca hasta el cambio de reglas).

**Simulaciones.** Sin segunda NIC ni host LAN:

- **Caso 9:** la red se cortó quitando la ruta por defecto, no desactivando `ether1`.
- **Caso 5 en modo normal:** se descargó un trozo de la lista real con `http-header-field="Accept-Encoding: identity,Range: bytes=0-N"`. Esa línea se añadió temporalmente al `mbl-update` instalado.

## 4. Puntos a verificar del script (Tarea 8)

Probado solo en **7.24.5**. Que algo esté en OK aquí no prueba que funcione en 7.21–7.23.

| # | Punto | Estado |
|---|-------|--------|
| 1 | Bloque multilínea `{ ... }` en `/import`: locales, funciones y continuaciones `\` | OK |
| 2 | `source={...}` guarda el cuerpo literal (`$var`), sin quejas de variables sin declarar; `get source` correcto y el reemplazo de URL con `:find`/`:pick` | OK |
| 3 | Nombre exacto de `builtin-trust-store` en la versión mínima; `get` (string/array); `$ts != "all"`; sintaxis `set builtin-trust-store=fetch,...`; `check-certificate=yes` a raw.githubusercontent.com. Fijar `mblMinVersion` | OK (probado solo en 7.24.5): `get` devuelve str (`default`, `all`, `untrusted` o `dns,www`). `current-defaults` es un array e incluye `fetch` en 7.24.5. Se probaron `dns,www`→`fetch,dns,www`, `untrusted`→`fetch`, `all` y `default` (sin cambios). Sin `fetch` en el almacén, `check-certificate=yes` falla. Mínimo **7.21** según el changelog de 7.21: «added option to configure built-in trust store» |
| 4 | Idioma `:parse` + `$f`: `:return` devuelve valor; errores en ejecución capturados por `:do on-error` | OK |
| 5 | Funciones con `$1` posicional (`mblVerNum`, `mblPad`); `:while` con `[:pick $rest $e] ~ "[0-9]"` | OK (probado solo en 7.24.5) (`7.21.3 (stable)`→7021, `7.19rc2`→7019, `8.0beta1`→8000; hora `HH:MM:SS` con relleno) |
| 6 | `/user active get $s address` con `:tostr`: vacío en consola; IPv6/MAC fallan dentro de `on-error` | OK: la sesión de consola da una dirección vacía; con IPv6, MAC o un duplicado, el error salta dentro de `on-error` |
| 7 | `/tool fetch ... check-certificate=yes` sin `as-value` bloquea hasta terminar y el fallo lo captura `on-error`; ¿hace falta/basta el delay de 2 s? | OK: fetch bloquea hasta terminar; 404, DNS o certificado se capturan. El tamaño ya es correcto sin el delay; se mantiene el de 2 s, que no hace daño |
| 8 | `/file get [find where name=$fname] size` devuelve bytes; `name` sin prefijo `flash/` | OK (num, bytes; sin `flash/` en CHR) |
| 9 | `/import verbose=no` desde scheduler: globals `mblTarget`/`mblImportOk` compartidos; fichero truncado deja `mblImportOk=false`; tiempo con ~32k entradas | OK (probado solo en 7.24.5): globals compartidos en ambos sentidos (desde SSH y desde un scheduler); un fichero truncado deja `mblImportOk=false`; ~7 s para 32k entradas |
| 10 | Globals compartidos entre el scheduler (`:global mblBoot true`) y `/system script run mbl-update` | OK (el `mbl-boot` real tras el reinicio) |
| 11 | `/file set name=` y `/file copy` existen en la versión mínima; cuál se usa | OK (probado solo en 7.24.5): los dos existen en 7.24.5; se usa `/file set name=` y el copy se probó forzando un fallo. **No probado en 7.21** |
| 12 | `remove [find where list=...]` sobre entradas con timeout; los timeouts se pierden al reiniciar (modo boot necesario) | OK: sin `mbl-boot`, tras reiniciar `mbl-a`=0 y `mbl-b`=0, así que el modo boot es necesario. `remove [find]` funciona sobre entradas dinámicas |
| 13 | Reglas raw con listas aún inexistentes (`mbl-allow`, `mbl-a`); `log=yes log-prefix=` aceptado en raw | OK |
| 14 | `place-before=<id interno>` en `/ip firewall raw`; las reglas quedan arriba en orden | OK: quedan arriba en el orden de la tabla, antes de las reglas del usuario |
| 15 | Scheduler `start-time=HH:MM:SS` sin `start-date` corre a diario; la política `ftp,read,write,policy,test` basta (script y scheduler) | OK: `start-date` se rellena sola y `next-run` es mañana. La política basta: el run normal desde `mbl-boot` descarga e importa |
| 16 | Uninstall: `where list="mbl-allow" and comment="MBL: admin"`, regex `^(flash/)?mbl-.*\.rsc$`, `/system script environment remove` | OK |
| 17 | `flash/`: detección con `/file find where name="flash"`, `dst-path`, `/import file-name=flash/...`, persistencia tras reinicio (CHR no lo tiene: solo se prueba el prefijo vacío) | No verificable en CHR (no hay `flash/`); solo se probó el prefijo vacío |
| 18 | `mblMove`: `:parse` de cadena construida dentro de función, retorno en `:if ([$mblMove a b])` y `![...]`, `file remove [find where name=$1]`; probar rutas de recuperación forzando fallo de rename | OK: rename normal, origen inexistente →false, `/file set` roto→copy. En el script: si solo hay `mbl-tmp.rsc`, el boot lo usa y el siguiente run normal lo recupera como `mbl-last.rsc` |
| 19 | Reinstalación con ambas listas pobladas: reglas sobre la lista activa previa y sin ventana con la activa vacía | OK: `mbl-a` activa y `mbl-b` con 5 entradas: las reglas se recrean sobre `mbl-a` y la primera carga va a `mbl-b`. Al reinstalar hay unos ms sin reglas `MBL:`, porque se borran y se recrean; la lista activa nunca se vacía |
| 20 | `mblLastOk`/`mblRunning` visibles al instalador tras `/system script run` (mensaje instalado / primera carga fallida correcto) | OK: `installed`; con URL caída: `installed, but the first load failed`; con lock: `first load skipped` |

## 5. Re-test tras los arreglos finales (2026-10-19)

Cambio probado: en equipos con `flash/`, `mbl-update` descarga e importa desde la raíz (RAM,
`mbl-new.rsc`) y solo copia a `flash/mbl-last.rsc` (vía `flash/mbl-tmp.rsc`) un fichero ya
validado, si `free-hdd-space` > 2 × tamaño. CHR 7.24.5, sin `flash/`, URL `lists-test`.

| Prueba | Resultado |
|---|---|
| `/import install.rsc` (reinstalación sobre `mbl-a` con 32242 entradas) | OK: `MBL: loaded 32242 entries into mbl-b` + `MBL: installed`; reglas en `mbl-b`, `mbl-a`=0, `install.rsc` borrado |
| `/system script run mbl-update` | OK: `loaded 32242 entries into mbl-a`; las tres reglas pasan a `mbl-a`, `mbl-b`=0; `mbl-last.rsc` renovado; sin `mbl-new.rsc`/`mbl-tmp.rsc`; lock liberado |
| Extra: `flash/` simulado (`/file add type=directory name=flash`) | OK: la descarga queda en la raíz, se copia a `flash/mbl-last.rsc` y `mbl-new.rsc` se borra; el modo boot carga desde `flash/mbl-last.rsc`. Solo prueba la lógica del script (en CHR la raíz no es RAM); la rama "sin espacio" no se pudo forzar. Directorio borrado después |

Estado final: instalado con `lists-test`, reglas en `mbl-b` (32242 entradas).

## 6. Aviso de lista antigua (2026-10-19)

Cambio probado: tras una carga correcta, `mbl-update` compara la fecha de `# generated:` con
`/system clock get date` y registra `MBL: list is N days old` si pasan más de 3 días. CHR 7.24.5,
`install.rsc` subido por scp (URL `lists`). Las listas antiguas son copias de `blocklist.rsc` con la
cabecera editada, subidas como `mbl-last.rsc` y cargadas en modo boot (`:global mblBoot true`).

| Prueba | Resultado |
|---|---|
| `/import install.rsc` con la lista actual (generada hoy) | OK: `loaded 32557 entries into mbl-b` + `installed`, sin aviso |
| Lista con `generated: 2026-10-10` | OK: se carga y registra `MBL: list is 9 days old` |
| Lista con `generated: 2026-10-16` (justo 3 días) | OK: se carga, sin aviso |
| Lista con `generated: 2026-09-28` (cruza mes) | OK: `MBL: list is 21 days old` |
| `/system script run mbl-update` normal después | OK: lista actual en `mbl-b`, sin aviso; `mbl-last.rsc` vuelve a la cabecera actual; lock liberado (`mblRunning=false`) |

Estado final: instalado con `lists`, reglas en `mbl-b` (32557 entradas).

## 7. Pruebas automáticas (CI)

El workflow `.github/workflows/routeros-e2e.yml` arranca un CHR desechable con QEMU/KVM en
`ubuntu-latest` y ejecuta `tests/e2e/test_routeros.py` contra el `routeros/install.rsc` sin
modificar. Se lanza en los PR que tocan `routeros/**`, `mbl/render.py`, `tests/e2e/**` o el propio
workflow; cada lunes (cron `41 5 * * 1`) contra la **última estable** (sin checksum: el sha256
calculado queda en el *summary*), y a mano con `workflow_dispatch` (input `chr_version`, vacío =
la fijada). Si falla, se sube el artefacto `routeros-e2e-logs` con `console.log` (consola serie),
`qemu.log` y `router-log.txt` (`/log print`).

Los tests nunca corren en `python -m unittest discover` normal: se saltan salvo con `MBL_E2E=1`.

### En local

Hace falta `qemu-system-x86_64`, `qemu-img`, `ssh`/`scp`, `openssl`, `unzip` y acceso a `/dev/kvm`.

```bash
MBL_E2E=1 python -m unittest tests.e2e.test_routeros -v
```

Variables opcionales: `MBL_E2E_CACHE` (caché de la imagen, por defecto `<tmp>/mbl-e2e-cache`),
`MBL_E2E_CHR_VERSION` / `MBL_E2E_CHR_SHA256` (sustituyen a la versión fijada; SHA vacío = sin
comprobar) y `MBL_E2E_ARTIFACTS=<dir>` (`console.log` y `qemu.log` se escriben ahí mientras corre la
VM; `router-log.txt` al terminar).

### Contra el CHR de Proxmox

Sin arrancar QEMU, por clave SSH (nunca contraseña) y comprobando la clave de host con tu
`known_hosts` (`StrictHostKeyChecking=accept-new`). **Destructivo**: reinstala MBL, reinicia el
router y termina ejecutando `uninstall.rsc`.

```bash
MBL_E2E=1 MBL_E2E_SSH=admin@192.168.33.118 MBL_E2E_DESTRUCTIVE=1 \
  python -m unittest tests.e2e.test_routeros -v     # opcional: MBL_E2E_KEY=~/.ssh/id_ed25519
```

Si la ejecución se interrumpe (Ctrl-C, proceso matado), ejecuta `uninstall.rsc` en el router y vuelve a
instalar MBL con `install.rsc`, porque puede quedar a medio instalar o apuntando al servidor de pruebas.

### Qué cubre cada caso

Los casos comparten un router y corren en orden.

| Caso | Cubre |
|---|---|
| `test_01_clean_install` | Instalación limpia: reglas `MBL:`, schedulers `mbl-daily` y `mbl-boot`, exactamente `LIST_SIZE` (10000) entradas y 0 inválidas (líneas cortadas entre trozos de `/file read`) |
| `test_02_update_alternates` | `mbl-update` alterna entre `mbl-a` y `mbl-b` |
| `test_03_truncated_keeps_active` | Una lista truncada se rechaza y la activa se conserva |
| `test_04_dead_url_keeps_active` | Con la URL caída se conserva la lista activa |
| `test_05_reboot_reloads` | Tras reiniciar, el modo boot recarga la lista |
| `test_06_flash_path` | Equipos con `flash/`: copia de seguridad `flash/mbl-last.txt` |
| `test_07_reinstall_no_duplicates` | Reinstalar no duplica reglas ni schedulers |
| `test_07d_update_skips_family_without_rules` | Sin la regla `MBL: in` (comentario cambiado), `mbl-update` registra `MBL: ipv4 rules missing, update skipped`, no descarga nada y no toca las listas |
| `test_07e_rules_changed_during_update` | `mbl-update` en segundo plano; mientras carga (el propio router espera a la primera entrada), la regla `MBL: in` pasa a `MBL-OLD: MBL: in` (lo que hace una reinstalación): registra `MBL: ipv4 rules changed during update, aborted`, vacía la lista destino y conserva la activa (reglas y entradas) |
| `test_07f_reinstall_during_update` | `:execute` de `mbl-update` e instalador justo después, con un hilo que toma muestras: el instalador se niega (`an update is running`, nada cambia) o termina (la rama que ocurrió se imprime); en ambos casos la lista de `MBL: in` / `MBL-OLD: MBL: in` nunca baja de `MIN_ENTRIES`, al final hay 6 reglas, ningún `MBL-OLD:`, alguna lista con entradas y el lock libre |
| `test_07g_installer_refuses_while_locked` | Con `mblRunning true` el instalador se detiene con su mensaje antes de cambiar nada (ninguna petición, mismas reglas, lista y `mbl-update`) |
| `test_07h_lock_released_after_install_error` | Un instalador que falla en el paso 5 (marcador inexistente) muestra el error, libera el lock y deja las 6 reglas `MBL-OLD:` protegiendo con la lista vieja; una reinstalación la encuentra por `MBL-OLD: MBL: in` y lo limpia todo |
| `test_07i_update_during_reinstall_skipped` | Un `mbl-update` lanzado mientras el instalador tiene el lock (desde el paso 2 hasta su primera carga) registra `MBL: update already running, skipped`; solo hay una carga (la del instalador), 6 reglas en una lista con `LIST_SIZE` entradas y el lock libre al final |
| `test_08_uninstall_clean` | `uninstall.rsc` deja el router limpio |
| `test_09_default_url_without_override` | Sin `mblUrlBase`, el instalador usa la URL de `lists/routeros-full.txt` |
| `test_10_upgrade_from_v2` | Instala el `install.rsc` de la etiqueta `v2.0.0` (con su `mbl-last.rsc`) y reinstala la v3 encima. Un hilo toma muestras de la lista a la que apunta `MBL: in` durante la reinstalación: siempre ≥ `MIN_ENTRIES` (como mucho una muestra aislada sin regla mientras el instalador la recrea). Tras la primera carga v3: `MBL: loaded 10000 entries`, la lista activa cambió, `MBL: removed v2 files`, ningún `mbl-*.rsc` y existe `mbl-last.txt` |
| `test_11_injected_lines_never_run` | Un fichero con 12 líneas hostiles (`/system reboot`, `/ip ...`, `1.2.3.4; /ip ...`, `$(...)`, `[...]`, `:execute`, octetos o prefijos fuera de rango, texto, una línea vacía y una línea de 100 KB que ocupa varios trozos de `/file read`): nada se ejecuta (el marcador `mbl-pwned` no aparece y una variable global sigue ahí, así que el router no se reinició), las líneas cuentan como inválidas (12, por debajo del 1 %) y se cargan exactamente `LIST_SIZE` entradas |
| `test_12_count_mismatch_rejected` | Cabecera y pie coherentes entre sí pero con más entradas de las que hay (fichero cortado con el pie añadido a mano): rechazado con `count mismatch` (entradas añadidas ≠ `entries`; si la cabecera y el pie no coinciden el motivo es `header/footer mismatch`), lista activa intacta |
| `test_13_crlf_accepted` | Fichero con `\r\n` y espacios al final: las líneas se aceptan y se cargan todas |
| `test_14_too_many_invalid_rejected` | Más de un 1 % de líneas inválidas (más de 100 con 10000 entradas; el test añade 200): rechazado con `too many invalid lines`, lista activa intacta |
| `test_14b_newer_script_notice` | Una versión publicada mayor que la instalada (`script=` de la cabecera): `MBL: script <publicada> available (installed <instalada>) ...` con nivel `warning`, exactamente 1 línea tras la marca y después de `MBL: loaded` |
| `test_14c_no_notice_when_not_newer` | Versión publicada igual o menor que la instalada: sin aviso |
| `test_14d_bad_script_version_ignored` | Versión publicada con basura (`3.1.0;/system reboot`, `abc`, vacía): sin aviso, la carga termina bien y nada se ejecuta |
| `test_14e_numeric_comparison` | Comparación numérica y componente patch: publicadas `patch+1`, `10.0.0` (una comparación de cadenas la ordenaría por debajo de `3.1.0`) y `3.10.0` frente a `3.1.0` instalada avisan cada una con exactamente 1 línea |
| `test_14f_install_lite` | `:global mblVariant "lite"` antes del instalador: descarga `/routeros-lite.txt`, la lista activa tiene 2000 entradas, el mensaje final es `MBL: installed (script <v>, variant lite, ipv6 no, mode block)` y `mbl-update` apunta a `routeros-lite.txt` |
| `test_14g_reinstall_keeps_lite` | Reinstalar con `mblVariant ""` conserva `lite`: pide `routeros-lite.txt`, nunca `routeros-full.txt`, y la lista sigue en 2000 entradas |
| `test_14h_invalid_variant_rejected` | `mblVariant "mini"`: el instalador falla con `invalid mblVariant` antes de cambiar nada (6 reglas `MBL:`, misma lista activa y mismas entradas, ninguna petición al servidor, `mbl-update` sigue con `lite`) |
| `test_14i_variant_mismatch_rejected` | El router en `lite` recibe una cabecera `variant=full`: rechazado con `bad header` y la lista activa no cambia |
| `test_14j_back_to_full` | `mblVariant "full"` vuelve a `full`: descarga `/routeros-full.txt`, `LIST_SIZE` entradas y `mbl-update` apunta a `routeros-full.txt` |
| `test_14k_no_ipv6_by_default` | Sin `mblIpv6` no existe ningún objeto IPv6 de MBL (ni reglas, ni entradas, ni ficheros), no se pide ninguna lista IPv6, `mbl-update` lleva `:local ipv6 "no"` y el mensaje final termina en `ipv6 no` |
| `test_14l_install_ipv6` | Primero con `/routeros6-full.txt` en 404: IPv4 se instala y el mensaje final dice `ipv6 yes - first ipv6 load failed, see log`. Luego `mblIpv6 true`: descarga `/routeros6-full.txt`, 6 reglas `MBL:` en `/ipv6 firewall raw`, `LIST6_SIZE` (300) entradas en `mbl6-a`/`mbl6-b`, `MBL: loaded N ipv6 entries into <lista>` y mensaje final `ipv6 yes`; IPv4 intacto; reinstalar con `mblIpv6 ""` conserva IPv6 |
| `test_14m_ipv6_line_validation` | Fichero IPv6 con líneas inválidas (IPv4, `::/0`, prefijo `/129`, basura): no se convierten en entradas y se cargan solo las válidas (`IPV6_VALIDATION_SIZE`, 600) |
| `test_14n_ipv6_failure_never_touches_ipv4` | Lista IPv6 caída (404) o truncada: IPv4 carga y conmuta con normalidad (6 reglas, `LIST_SIZE` entradas), la lista IPv6 activa no cambia, queda un log `MBL: ipv6 ...` con el motivo de cada caso (`download failed` / `rejected`) y el lock se libera |
| `test_14nb_ipv6_log_mode` | `mblMode "log"` con IPv6: las tres reglas de descarte de `/ip` y `/ipv6 firewall raw` pasan a `passthrough` / `log` (`MBL-OUT`, `MBL-ROUTER`), mensaje `ipv6 yes, mode log)` y el informe dice `would have dropped ...; ipv6 in N, ...`; `mblMode "block"` vuelve a `drop` en las dos familias |
| `test_14o_disable_ipv6_cleans_up` | Parte de reglas (también una `MBL-OLD:`), entradas y ficheros IPv6 existentes; `mblIpv6 false` al reinstalar: borra reglas, listas y ficheros IPv6 de MBL, IPv4 intacto, mensaje `ipv6 no`, `mbl-update` con `:local ipv6 "no"` y las actualizaciones siguientes no piden la lista IPv6 |
| `test_14p_install_log_mode` | `mblMode "log"`: mensaje `mode log)`, sin informe de contadores en la primera carga, reglas en modo log, `:local mode "log"`; un ping del router a una entrada de la lista (por una pasarela que no responde a ARP, así no sale de la VM) aparece como `MBL-ROUTER` y en `router-out`; la actualización registra el aviso `log-only mode` (warning) y `would have dropped`; una ejecución de arranque no registra ni aviso ni contadores |
| `test_14q_reinstall_keeps_log_mode` | Reinstalar con `mblMode ""` conserva `log` |
| `test_14r_invalid_mode_rejected` | `mblMode "drop"`: `invalid mblMode` antes de cambiar nada (mismas reglas y acciones, lista, `mbl-update`, ninguna petición) |
| `test_14s_back_to_block` | `mblMode "block"`: reglas `drop`, mensaje `mode block)`, sin aviso y el informe dice `dropped` |
| `test_14t_drop_counters` | Tras 3 pings a una entrada de la lista, el informe va desde el `mblStatsSince` anterior y da `router-out` ≥ 3; el siguiente da `router-out 0` (contadores puestos a cero) |
| `test_14u_boot_run_no_counters` | Una ejecución de arranque no registra contadores; sin `mblStatsSince` (tras un reinicio) el informe dice `since boot` |
| `test_15_final_uninstall` | Siempre el último: instala con IPv6 y desinstala; deja el router limpio en las dos familias |

### Rendimiento de la v3 (2026-10-21)

CHR 7.24.5, 1 vCPU, 256 MiB, KVM; lista de 40000 entradas consecutivas (/32) generada con
`write_outputs` (463 KiB) y servida por HTTPS local.

| Medida | Resultado |
|---|---|
| `mbl-update` completo (descarga, lectura y validación, 40000 altas, cambio de reglas, borrado de la lista vieja, copia de arranque) | **25,2 s** (dos ejecuciones: 25,187 y 25,192 s) |
| Instalación con primera carga | 18,2 s |
| Solo el bucle de lectura, validación y alta de 40000 entradas | ~8 s |
| `test_02` del e2e (10000 entradas, incluye 2 s tras el fetch y 1 s por renombrado) | 13,4 s |
| RAM libre con una lista de 40000 | 36,7 MiB antes; 36,1 MiB después |
| RAM libre mínima durante la actualización (dos listas, ~80000 entradas) | 30,5 MiB |

El objetivo era ≤ 5 min con ~39k entradas: se cumple con mucho margen y no hizo falta optimizar.
`/file read` admite como máximo `chunk-size=32768`; más da error.

### Subir la versión fijada

1. Mira la última estable: `curl -s https://upgrade.mikrotik.com/routeros/NEWESTa7.stable`.
2. Descarga `https://download.mikrotik.com/routeros/<ver>/chr-<ver>.img.zip` y calcula `sha256sum`.
3. Escribe `<ver> <sha256>` en `tests/e2e/chr-version.txt`.
4. Lanza el workflow (`workflow_dispatch`) en la rama y comprueba que queda en verde.

## 8. Hardware real: RB2011UiAS (2026-10-26)

Primer dispositivo físico. **RB2011UiAS**: MIPS 74Kc a 600 MHz (`mipsbe`), 128 MiB de RAM, 128 MiB
de NAND como almacenamiento raíz (no hay carpeta `flash/`), RouterOS **7.25**. Era un router con
configuración real previa (VLANs, 12 reglas de filtro, fasttrack, IPv6 activo); se guardó un
`/export` antes de empezar.

| Prueba | Resultado |
|---|---|
| Instalación `full` desde GitHub (34.384 entradas) | OK en 333 s. Reglas `MBL:` arriba, antes de la regla dinámica del fasttrack. ~15 MiB de RAM |
| Actualización diaria `full` | OK, pero **unos 18 min con la CPU al 100 %**: ~8 min cargando y ~10 min vaciando la lista antigua (un único `remove [find]`). Con las dos listas, ~27 MiB menos de RAM libre |
| Pasar de `full` a `lite` | 331 s (casi todo es vaciar la lista `full`) |
| Actualización diaria `lite` (2.400 entradas) | **41 s** |
| IPv6 activado (`lite`, 87 entradas) | OK: 6 reglas en `/ipv6 firewall raw`, carga en ~7 s |
| Contador | OK: 3 pings del propio router a una IP listada → `router-out 3 packets`; la siguiente actualización, 0 |
| Modo prueba (`mblMode "log"`) | OK en IPv4 e IPv6 (`passthrough` / `log` con `MBL-OUT` y `MBL-ROUTER`), con el aviso en el log; vuelta a `block` sin huecos |
| Reinicio | OK: vuelve en ~50 s; carga la copia local (`mbl-last.txt`, `mbl6-last.txt`) al arrancar y la lista nueva un minuto después; contador `since boot` |
| Aviso de router pequeño (3.5.1) | OK: con `full` avisa al empezar, en el log y al terminar |
| Ruido en el log (3.5.1) | Una instalación deja 2 copias del código del script en `system,info` en lugar de 7 |

Conclusiones:

- En routers de este tipo, **`lite`**. `full` funciona, pero una CPU al 100 % durante 18 minutos al
  día afecta al tráfico que el router tenga que procesar fuera del fasttrack.
- El **reloj** del router iba 5 días atrasado (los servidores NTP configurados no respondían). La
  comprobación de lista antigua y la validación TLS dependen de la hora: conviene tener un NTP que
  funcione (`/system ntp client set servers=pool.ntp.org`).
- No se probó la ruta `flash/` (este modelo no la tiene) ni versiones 7.21-7.23 (el equipo está en
  7.25 y bajar de versión no es una prueba inocua).
- La suite e2e no se pasó contra este equipo: con 10.000 entradas por carga y decenas de cargas,
  serían horas con la CPU al 100 %. Las pruebas de arriba cubren lo propio del hardware.
