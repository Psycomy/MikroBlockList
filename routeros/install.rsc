# MikroBlockList v3 - RouterOS v7 installer
# https://github.com/Psycomy/MikroBlockList
# For home / non-commercial use only (see source licenses in README)
#
# Usage:
#   /tool fetch url="https://raw.githubusercontent.com/Psycomy/MikroBlockList/main/routeros/install.rsc" check-certificate=yes; /import install.rsc
#
# Idempotent: re-running it removes and recreates everything named mbl-* / MBL:*.
# The old MBL: raw rules keep dropping until the new ones are in place (they
# are renamed MBL-OLD: and removed only after the new rules are added).
# It never touches anything else, except enabling the RouterOS built-in CA
# store for /tool fetch (needed for check-certificate=yes).
#
# Everything runs inside one block so that local variables survive and any
# :error aborts the whole installer.
# It refuses to run while an mbl-update is running, and holds the same lock
# (:global mblRunning) from step 2 through the first load in step 8, so no
# other update runs in between.
{
# ---------------------------------------------------------------- settings
# Blocklist URL base. Forks: change this line only.
:local mblBase "https://raw.githubusercontent.com/Psycomy/MikroBlockList/lists"
# Test/dev only: set :global mblUrlBase before /import to use another server.
:global mblUrlBase
:if ([:len $mblUrlBase] > 0) do={
  :set mblBase $mblUrlBase
  :put "MBL: using test URL base $mblUrlBase"
  :log warning "MBL: using test URL base $mblUrlBase"
}
# List variant: "full" (default) or "lite" (a shorter list for small
# routers). Set :global mblVariant "lite" before /import to choose it; when
# unset, a reinstall keeps the variant already installed. See step 1.
:global mblVariant
# IPv6 lists and rules: off by default. Set :global mblIpv6 true before
# /import to add them (false removes them); when unset, a reinstall keeps the
# choice already installed. See step 1.
:global mblIpv6
# Mode: "block" (default) drops, "log" only counts and logs what would be
# dropped (to try the list first). Set :global mblMode "log" before /import;
# when unset, a reinstall keeps the mode already installed. See step 1.
:global mblMode
# Minimum RouterOS version (major.minor). 7.21 is the first release with the
# per-service built-in CA store (/certificate settings builtin-trust-store,
# 7.21 changelog). Tested on CHR 7.24.5.
:local mblMinVersion "7.21"
# Version of this installer and of the mbl-update it installs (= VERSION file).
# mbl-update logs a warning when the published list says a newer one exists.
:local mblScriptVersion "3.5.1"

# "7.21.3 (stable)" -> 7021 ; "7.19rc2" -> 7019
:local mblVerNum do={
  :local d [:find $1 "."]
  :local maj [:tonum [:pick $1 0 $d]]
  :local rest [:pick $1 ($d + 1) [:len $1]]
  :local e 0
  :while ($e < [:len $rest] && [:pick $rest $e] ~ "[0-9]") do={ :set e ($e + 1) }
  :return ($maj * 1000 + [:tonum [:pick $rest 0 $e]])
}
# 7 -> "07"
:local mblPad do={
  :if ($1 < 10) do={ :return ("0" . $1) }
  :return [:tostr $1]
}

# ------------------------------------------------- 1. pre-checks (no changes)
:local rosVer [/system resource get version]
:local rosNum [$mblVerNum $rosVer]
:if ($rosNum < [$mblVerNum $mblMinVersion] || $rosNum >= 8000) do={
  :error "MBL: RouterOS $rosVer not supported, need v7 >= $mblMinVersion. Nothing was changed."
}
:if ([:len [/interface list find where name="WAN"]] = 0) do={
  :error "MBL: interface list WAN not found (add your Internet interface to /interface list WAN). Nothing was changed."
}
:if ([:len [/interface list find where name="LAN"]] = 0) do={
  :error "MBL: interface list LAN not found (add your LAN interfaces to /interface list LAN). Nothing was changed."
}
# mbl-update's lock (taken just before step 2): a reinstall while an update
# loads a list could leave the rules on a list that update empties next.
:global mblRunning
# Variant: :global mblVariant if set, else the one in the installed
# mbl-update (its ':local variant "<v>"' line, read before step 3 removes
# it), else full. Only "full" or "lite" is ever used.
:local mblVar ""
:local want [:tostr $mblVariant]
:if ([:len $want] > 0) do={
  :if ($want != "full" && $want != "lite") do={
    :error "MBL: invalid mblVariant \"$want\" (use \"full\" or \"lite\"). Nothing was changed."
  }
  :set mblVar $want
}
# IPv6: :global mblIpv6 (true/false, boolean or string) if set, else the
# choice in the installed mbl-update (its ':local ipv6 "<yes|no>"' line), else
# off. Only "yes" or "no" is ever used.
:local mblV6 ""
:local want6 [:tostr $mblIpv6]
:if ([:len $want6] > 0) do={
  :if ($want6 != "true" && $want6 != "false") do={
    :error "MBL: invalid mblIpv6 \"$want6\" (use true or false). Nothing was changed."
  }
  :set mblV6 "no"
  :if ($want6 = "true") do={ :set mblV6 "yes" }
}
# Mode: :global mblMode if set, else the one in the installed mbl-update (its
# ':local mode "<block|log>"' line), else block. Only "block" or "log" is
# ever used.
:local mblMd ""
:local wantM [:tostr $mblMode]
:if ([:len $wantM] > 0) do={
  :if ($wantM != "block" && $wantM != "log") do={
    :error "MBL: invalid mblMode \"$wantM\" (use \"block\" or \"log\"). Nothing was changed."
  }
  :set mblMd $wantM
}
:local prev [/system script find where name="mbl-update"]
:if ([:len $prev] > 0) do={
  :local prevSrc [/system script get ($prev->0) source]
  :if ($mblVar = "") do={
    :local key ":local variant \""
    :local q [:find $prevSrc $key]
    :if ([:typeof $q] = "num") do={
      :local v [:pick $prevSrc ($q + [:len $key]) ($q + [:len $key] + 5)]
      :if ($v = "full\"") do={ :set mblVar "full" }
      :if ($v = "lite\"") do={ :set mblVar "lite" }
    }
  }
  :if ($mblV6 = "") do={
    :local key ":local ipv6 \""
    :local q [:find $prevSrc $key]
    :if ([:typeof $q] = "num") do={
      :local v [:pick $prevSrc ($q + [:len $key]) ($q + [:len $key] + 4)]
      :if ($v = "yes\"") do={ :set mblV6 "yes" }
      :if ([:pick $v 0 3] = "no\"") do={ :set mblV6 "no" }
    }
  }
  :if ($mblMd = "") do={
    :local key ":local mode \""
    :local q [:find $prevSrc $key]
    :if ([:typeof $q] = "num") do={
      :local v [:pick $prevSrc ($q + [:len $key]) ($q + [:len $key] + 6)]
      :if ($v = "block\"") do={ :set mblMd "block" }
      :if ([:pick $v 0 4] = "log\"") do={ :set mblMd "log" }
    }
  }
}
:if ($mblVar = "") do={ :set mblVar "full" }
:if ($mblV6 = "") do={ :set mblV6 "no" }
:if ($mblMd = "") do={ :set mblMd "block" }
# The full list on a small router: a warning only, the choice stays the
# user's. Measured on an RB2011UiAS (MIPS 600 MHz, 128 MiB): a daily full
# update kept the CPU at 100 % for about 18 minutes; lite took 41 seconds.
:local mblSlow ""
:if ($mblVar = "full") do={
  :local arch [/system resource get architecture-name]
  :local mem [/system resource get total-memory]
  :if (($arch ~ "^(mips|mmips|smips|ppc|tile)") || ($mem < (200 * 1024 * 1024))) do={
    :set mblSlow ("MBL: variant full on a small router (" . [/system resource get board-name] . ", " . $arch . ", " . ($mem / 1048576) . " MiB): each daily update may keep the CPU busy for many minutes. Consider lite: :global mblVariant \"lite\" and install again.")
    :put $mblSlow
    :log warning $mblSlow
  }
}
# The router downloads a plain list (one address per line) and validates
# every line as data; nothing downloaded is ever executed.
:local mblUrl ($mblBase . "/routeros-" . $mblVar . ".txt")
:local mblUrl6 ($mblBase . "/routeros6-" . $mblVar . ".txt")

# Take the update lock in one check-and-set statement, just before the first
# change. From here on, an mbl-update started by the scheduler or by hand is
# skipped ("update already running"); only the installer's own first load
# (step 8) runs. Any error until the end of step 7 releases the lock and
# aborts the installer with that error; old rules, renamed MBL-OLD:, keep
# dropping until the next install removes them.
# (:onerror needs RouterOS 7.13 or later; the supported floor is 7.21.)
:if ($mblRunning = true) do={
  :error "MBL: an update is running (mblRunning is set); try again in a minute, or clear it with :global mblRunning false. Nothing was changed."
} else={ :set mblRunning true }
:onerror mblErr in={
# ------------------------------------------- 2. HTTPS: built-in CA for fetch
# builtin-trust-store is "all", "untrusted", "default" (= current-defaults)
# or a list of components. Add "fetch" to it unless already covered.
# Wrapped in :parse so that the script still parses (and the version check
# above reports cleanly) on releases without this property.
:local getTs [:parse ":return [/certificate settings get builtin-trust-store]"]
:local getDefs [:parse ":return [/certificate settings get current-defaults]"]
:local ts [$getTs]
:if ($ts = "default") do={ :set ts [$getDefs] }
:if ($ts != "all" && [:typeof [:find [:tostr $ts] "fetch"]] = "nil") do={
  :local comps "fetch"
  :if ($ts != "untrusted") do={
    :foreach c in=$ts do={ :if ([:len $c] > 0) do={ :set comps ($comps . "," . $c) } }
  }
  :local setTs [:parse "/certificate settings set builtin-trust-store=$comps"]
  $setTs
  :log info "MBL: built-in CA store enabled for fetch ($comps)"
}

# ------------------------------------------ 3. remove a previous installation
# The address lists are kept so a reinstall never leaves the router
# unprotected: the new rules point at the list that already has entries.
# The raw rules are not removed here either: each "MBL: x" rule is renamed
# "MBL-OLD: MBL: x" and keeps dropping until step 7 has added the new rules,
# then every ^MBL-OLD: rule goes (also those left by an aborted install).
# Files are kept too: the boot copy mbl-last.txt is replaced by the next load.
# prevList: the list the in rule points to; after an aborted install only its
# renamed copy "MBL-OLD: MBL: in" may be left.
:local prevList ""
:local prevIn [/ip firewall raw find where comment="MBL: in"]
:if ([:len $prevIn] = 0) do={ :set prevIn [/ip firewall raw find where comment="MBL-OLD: MBL: in"] }
:if ([:len $prevIn] > 0) do={ :set prevList [/ip firewall raw get ($prevIn->0) src-address-list] }
/system scheduler remove [find where name~"^mbl-"]
/system script remove [find where name~"^mbl-"]
:foreach r in=[/ip firewall raw find where comment~"^MBL:"] do={
  /ip firewall raw set $r comment=("MBL-OLD: " . [/ip firewall raw get $r comment])
}
:local na [:len [/ip firewall address-list find where list="mbl-a"]]
:local nb [:len [/ip firewall address-list find where list="mbl-b"]]
# active = the list with entries (the previous one if both have entries).
# On a clean install both are empty: active = mbl-b, so the first load goes
# into mbl-a and the next update into mbl-b.
:local active "mbl-b"
:if ($na > 0 && ($nb = 0 || $prevList != "mbl-b")) do={ :set active "mbl-a" }
# mbl-allow is kept as is (user entries and previous "MBL: admin" entries).
# IPv6, the same way with mbl6-a / mbl6-b. With IPv6 off, every MBL IPv6
# object of an earlier installation goes: rules (MBL: and MBL-OLD:), lists,
# "MBL: admin" entries in mbl6-allow (your own entries there are kept) and
# mbl6-* files.
:local prevList6 ""
:local prevIn6 [/ipv6 firewall raw find where comment="MBL: in"]
:if ([:len $prevIn6] = 0) do={ :set prevIn6 [/ipv6 firewall raw find where comment="MBL-OLD: MBL: in"] }
:if ([:len $prevIn6] > 0) do={ :set prevList6 [/ipv6 firewall raw get ($prevIn6->0) src-address-list] }
:local active6 "mbl6-b"
:if ($mblV6 = "yes") do={
  :foreach r in=[/ipv6 firewall raw find where comment~"^MBL:"] do={
    /ipv6 firewall raw set $r comment=("MBL-OLD: " . [/ipv6 firewall raw get $r comment])
  }
  :local na6 [:len [/ipv6 firewall address-list find where list="mbl6-a"]]
  :local nb6 [:len [/ipv6 firewall address-list find where list="mbl6-b"]]
  :if ($na6 > 0 && ($nb6 = 0 || $prevList6 != "mbl6-b")) do={ :set active6 "mbl6-a" }
} else={
  /ipv6 firewall raw remove [find where comment~"^MBL(-OLD)?:"]
  /ipv6 firewall address-list remove [find where list="mbl6-a" or list="mbl6-b"]
  /ipv6 firewall address-list remove [find where list="mbl6-allow" and comment="MBL: admin"]
  /file remove [find where name~"^(flash/)?mbl6-.*\\.txt\$"]
}

# --------------------------------------------------- 4. admin allowlist
# Current management sessions, so a remote installer never locks itself out.
:foreach s in=[/user active find] do={
  :local a [:tostr [/user active get $s address]]
  :if ([:len $a] > 0) do={
    :if ([:typeof [:find $a ":"]] = "num") do={
      # an IPv6 session: mbl6-allow, only with IPv6 on
      :if ($mblV6 = "yes") do={
        :do {
          /ipv6 firewall address-list add list=mbl6-allow address=$a comment="MBL: admin"
        } on-error={}
      }
    } else={
      :do {
        /ip firewall address-list add list=mbl-allow address=$a comment="MBL: admin"
      } on-error={}
    }
  }
}

# --------------------------------------------------- 5. mbl-update script
# The body is stored verbatim; @MBL_URL@, @MBL_URL6@, @MBL_VERSION@,
# @MBL_VARIANT@, @MBL_IPV6@ and @MBL_MODE@ are replaced with $mblUrl,
# $mblUrl6, $mblScriptVersion, $mblVar, $mblV6 and $mblMd below.
/system script add name=mbl-update policy=ftp,read,write,policy,test \
  dont-require-permissions=no comment="MBL: blocklist updater" source={
# MikroBlockList v3 updater - installed by install.rsc, do not edit.
# Normal run: download the plain list, read it in chunks and add each valid
# line to the inactive list (mbl-a or mbl-b), switch the MBL rules to it,
# empty the old list.
# Boot run (:global mblBoot true): same, but from mbl-last.txt, no download.
# Other runs end by logging the packets the MBL rules dropped (or, in log
# mode, would have dropped) since the previous report, and reset the counters.
# With IPv6 on, the IPv6 list then goes the same way into mbl6-a / mbl6-b
# (files mbl6-*.txt, rules in /ipv6 firewall raw). An IPv6 failure never
# touches anything IPv4: each family has its own lists, rules and files.
# Nothing read from the file is ever executed: each line is only a value,
# checked against the address form, :toip / :toip6 and the prefix range.
:local url "@MBL_URL@"
:local url6 "@MBL_URL6@"
:local scriptVersion "@MBL_VERSION@"
# the list header must name this variant
:local variant "@MBL_VARIANT@"
# "yes": also load the IPv6 list
:local ipv6 "@MBL_IPV6@"
# "block": the rules drop; "log": they only count and log (set by install.rsc)
:local mode "@MBL_MODE@"
# /file read returns at most 32768 bytes per call (RouterOS 7.24)
:local chunkSize 32768
# a longer line cannot be an address; it is dropped as one invalid line
:local maxLine 255
# warn when the loaded list was generated more than this many days ago
:local maxAgeDays 3
:global mblRunning
:global mblBoot
# set by install.rsc for its first load, which runs under the installer's lock
:global mblInstallRun
:global mblLastOk
:global mblLastOk6
# start of the current counter period ("YYYY-MM-DD HH:MM:SS"); unset after a reboot
:global mblStatsSince
# Rename file $1 to $2 (/file set name=), falling back to copy + remove.
# Run through :parse so a release lacking a command fails at run time,
# not when the whole script is parsed. Returns true on success.
:local mblMove do={
  :do {
    :local f [:parse ("/file set [find where name=\"" . $1 . "\"] name=\"" . $2 . "\"")]
    $f
  } on-error={
    :do {
      :local f [:parse ("/file copy [find where name=\"" . $1 . "\"] name=\"" . $2 . "\"")]
      $f
      /file remove [find where name=$1]
    } on-error={}
  }
  :delay 1s
  :return ([:len [/file find where name=$2]] > 0)
}
# Copy file $1 to $2 (e.g. RAM -> flash/). Returns true only if $2 exists
# with the same size as $1; a short copy (flash full) is removed.
:local mblCopy do={
  :do {
    :local f [:parse ("/file copy [find where name=\"" . $1 . "\"] name=\"" . $2 . "\"")]
    $f
  } on-error={}
  :delay 1s
  :local ok false
  :do {
    :if ([/file get [find where name=$2] size] = [/file get [find where name=$1] size]) do={ :set ok true }
  } on-error={}
  :if (!$ok) do={ /file remove [find where name=$2] }
  :return $ok
}
# "2026-10-19" -> days since 0000-03-01 (proleptic Gregorian), so that the
# difference of two dates is the number of days between them. Leading zeros
# are stripped before :tonum.
:local mblDays do={
  :local num do={
    :local s $1
    :while ([:len $s] > 1 && [:pick $s 0] = "0") do={ :set s [:pick $s 1 [:len $s]] }
    :return [:tonum $s]
  }
  :local y [$num [:pick $1 0 4]]
  :local m [$num [:pick $1 5 7]]
  :local d [$num [:pick $1 8 10]]
  :if ($m <= 2) do={ :set y ($y - 1); :set m ($m + 12) }
  :return ($y * 365 + $y / 4 - $y / 100 + $y / 400 + (153 * ($m - 3) + 2) / 5 + $d - 1)
}
# Value of "key=" in header line $1 (fields separated by spaces), "" if absent.
:local mblField do={
  :local h (" " . $1 . " ")
  :local p [:find $h (" " . $2 . "=")]
  :if ([:typeof $p] != "num") do={ :return "" }
  :local v [:pick $h ($p + [:len $2] + 2) [:len $h]]
  :return [:pick $v 0 [:find $v " "]]
}
# true if version $1 is newer than version $2, compared numerically by
# MAJOR.MINOR.PATCH component (3.10.0 > 3.9.0). false when either one is not
# of that form. Nothing is parsed or executed: only :tonum per component.
:local mblVerGt do={
  :local re "^[0-9]+\\.[0-9]+\\.[0-9]+\$"
  :if (!($1 ~ $re) || !($2 ~ $re)) do={ :return false }
  :local num do={
    :local s $1
    :while ([:len $s] > 1 && [:pick $s 0] = "0") do={ :set s [:pick $s 1 [:len $s]] }
    :return [:tonum $s]
  }
  :local a ($1 . ".")
  :local b ($2 . ".")
  :local pa -1
  :local pb -1
  :for i from=1 to=3 do={
    :local ea [:find $a "." $pa]
    :local eb [:find $b "." $pb]
    :local x [$num [:pick $a ($pa + 1) $ea]]
    :local y [$num [:pick $b ($pb + 1) $eb]]
    :if ($x > $y) do={ :return true }
    :if ($x < $y) do={ :return false }
    :set pa $ea
    :set pb $eb
  }
  :return false
}
:local boot ($mblBoot = true)
:set mblBoot false
:local owned ($mblInstallRun = true && $mblRunning = true)
:set mblInstallRun false
:if ($mblRunning = true && !$owned) do={
  :log warning "MBL: update already running, skipped"
} else={
  :set mblRunning true
  :set mblLastOk false
  :set mblLastOk6 false
  # IPv4 first, then IPv6 when it is on. Each family runs in its own :do, so
  # an IPv6 failure can never change the IPv4 result (mblLastOk) or objects.
  :foreach fam in={"ipv4";"ipv6"} do={
    :local v6 ($fam = "ipv6")
    # "" for IPv4 (its messages are unchanged), "ipv6 " for IPv6
    :local fl ""
    :if ($v6) do={ :set fl "ipv6 " }
    # The /ip and /ipv6 commands below are spelled out in two branches on
    # $v6: a menu path cannot come from a variable without :parse, which
    # would turn every command into an unchecked string.
    :local run (!$v6 || ($ipv6 = "yes"))
    # Without its "MBL: in" rule (e.g. a reinstall is renaming the rules) the
    # family is skipped and its lists are left as they are.
    :if ($run) do={
      :local nIn 0
      :if ($v6) do={
        :set nIn [:len [/ipv6 firewall raw find where comment="MBL: in"]]
      } else={
        :set nIn [:len [/ip firewall raw find where comment="MBL: in"]]
      }
      :if ($nIn = 0) do={
        :set run false
        :log warning "MBL: $fam rules missing, update skipped"
      }
    }
    :if ($run) do={
      # Every failure path logs, cleans up and raises :error, caught below so
      # that the lock is always released.
      :do {
        # per-family settings
        :local furl $url
        :local fp "mbl-"
        :local la "mbl-a"
        :local lb "mbl-b"
        # minimum file size in bytes; the lite list is much shorter
        :local minSize 100000
        :if ($variant = "lite") do={ :set minSize 20000 }
        :local minEntries 1000
        :local lineRe "^[0-9]{1,3}\\.[0-9]{1,3}\\.[0-9]{1,3}\\.[0-9]{1,3}(/[0-9]{1,2})?\$"
        :local minPlen 8
        :local maxPlen 32
        :if ($v6) do={
          :set furl $url6
          :set fp "mbl6-"
          :set la "mbl6-a"
          :set lb "mbl6-b"
          :set minSize 200
          :set minEntries 50
          :if ($variant = "lite") do={ :set minEntries 20 }
          # hex groups and colons only (at least two colons): never an IPv4 line
          :set lineRe "^[0-9a-fA-F]{0,4}(:[0-9a-fA-F]{0,4}){2,7}(/[0-9]{1,3})?\$"
          :set minPlen 19
          :set maxPlen 128
        }
        # Devices with a flash/ directory only keep flash/ across reboots. There
        # the download goes to the RAM root (no flash wear, no flash space used by
        # a file that may be rejected); only a validated file is copied to flash/.
        :local dir ""
        :if ([:len [/file find where name="flash"]] > 0) do={ :set dir "flash/" }
        :local newFile ($fp . "new.txt")
        :local lastFile ($dir . $fp . "last.txt")
        :local tmpFile ($dir . $fp . "tmp.txt")
        # ---- source file
        :local fname $newFile
        :if ($boot) do={
          :set fname $lastFile
          # an interrupted boot-copy update may have left only the temp copy
          :if ([:len [/file find where name=$fname]] = 0 && [:len [/file find where name=$tmpFile]] > 0) do={
            :set fname $tmpFile
          }
          :if ([:len [/file find where name=$fname]] = 0) do={
            :log warning ("MBL: " . $fl . "boot: no $lastFile yet, nothing to load")
            :error "MBL: abort"
          }
        } else={
          /file remove [find where name=$fname]
          :local fetched true
          :do {
            /tool fetch url=$furl dst-path=$fname check-certificate=yes
          } on-error={ :set fetched false }
          :if (!$fetched) do={
            /file remove [find where name=$fname]
            :log error ("MBL: " . $fl . "download failed ($furl), " . $fl . "list not changed")
            :error "MBL: abort"
          }
          # let the file system catch up before reading the size
          :delay 2s
        }

        # ---- size
        :local fsize 0
        :do { :set fsize [/file get [find where name=$fname] size] } on-error={}
        :if ($fsize < $minSize) do={
          :if (!$boot) do={ /file remove [find where name=$fname] }
          :log error ("MBL: " . $fl . "$fname too small ($fsize bytes), " . $fl . "list not changed")
          :error "MBL: abort"
        }

        # ---- load into the list the rules are NOT using
        :local target $la
        :local cur ""
        :if ($v6) do={
          :local inRule [/ipv6 firewall raw find where comment="MBL: in"]
          :if ([:len $inRule] > 0) do={ :set cur [/ipv6 firewall raw get ($inRule->0) src-address-list] }
        } else={
          :local inRule [/ip firewall raw find where comment="MBL: in"]
          :if ([:len $inRule] > 0) do={ :set cur [/ip firewall raw get ($inRule->0) src-address-list] }
        }
        # the rules vanished since the check at the start (a reinstall renamed
        # them): without $cur the target could be the list still in use, so
        # stop before any list is touched
        :if ($cur = "") do={
          :if (!$boot) do={ /file remove [find where name=$fname] }
          :log warning "MBL: $fam rules missing, update skipped"
          :error "MBL: abort"
        }
        :if ($cur = $la) do={ :set target $lb }
        :local old $la
        :if ($target = $la) do={ :set old $lb }
        :if ($v6) do={
          /ipv6 firewall address-list remove [find where list=$target]
        } else={
          /ip firewall address-list remove [find where list=$target]
        }

        # Read the file in chunks. A line cut at the end of a chunk is carried
        # over to the next one. $reason is set on the first fatal problem.
        :local reason ""
        :local gen "unknown"
        # script version named in the list header; only compared and logged
        :local pubScript ""
        :local entries -1
        :local footer -1
        :local first true
        :local skip false
        :local added 0
        :local bad 0
        :local off 0
        :local carry ""
        # any unexpected error while reading goes through the rejection below
        :do {
          :while ($off < $fsize && $reason = "") do={
            :local d ""
            :do {
              :set d ([/file read file=$fname offset=$off chunk-size=$chunkSize as-value]->"data")
            } on-error={}
            :if ([:len $d] = 0) do={
              # read error: stop here, the missing footer rejects the file
              :set off $fsize
            } else={
              :set off ($off + [:len $d])
            }
            :local buf ($carry . $d)
            # the last line may lack its "\n"
            :if ($off >= $fsize && [:len $buf] > 0 && [:pick $buf ([:len $buf] - 1)] != "\n") do={
              :set buf ($buf . "\n")
            }
            :local s 0
            :local e [:find $buf "\n" -1]
            :while ([:typeof $e] = "num" && $reason = "") do={
              :local ln [:pick $buf $s $e]
              :set s ($e + 1)
              :set e [:find $buf "\n" ($s - 1)]
              :if ($skip) do={
                # rest of an over-long line, already counted
                :set skip false
              } else={
                # strip trailing "\r" and spaces
                :local n [:len $ln]
                :while ($n > 0 && ([:pick $ln ($n - 1)] = "\r" || [:pick $ln ($n - 1)] = " ")) do={ :set n ($n - 1) }
                :if ($n < [:len $ln]) do={ :set ln [:pick $ln 0 $n] }
                :if ($first) do={
                  :set first false
                  :if ([:pick $ln 0 17] != "# MikroBlockList ") do={
                    :set reason "no header"
                  } else={
                    :local en [$mblField $ln "entries"]
                    :if ([$mblField $ln "format"] != "1" || [$mblField $ln "family"] != $fam || [$mblField $ln "variant"] != $variant || !($en ~ "^[0-9]+\$")) do={
                      :set reason "bad header"
                    } else={
                      :set entries [:tonum $en]
                      :local g [$mblField $ln "generated"]
                      # only a date-like value is logged or used for the stale check
                      :if ($g ~ "^[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T") do={ :set gen $g }
                      :set pubScript [$mblField $ln "script"]
                    }
                  }
                } else={
                  :if ($footer < 0 && [:pick $ln 0 18] = "# MBL-END entries=" && [:pick $ln 18 [:len $ln]] ~ "^[0-9]+\$") do={
                    :set footer [:tonum [:pick $ln 18 [:len $ln]]]
                  } else={
                    :local ok false
                    :if ($footer < 0 && $ln ~ $lineRe) do={
                      :local sl [:find $ln "/"]
                      :local ip $ln
                      :local plen $maxPlen
                      :if ([:typeof $sl] = "num") do={
                        :set ip [:pick $ln 0 $sl]
                        :set plen [:tonum [:pick $ln ($sl + 1) [:len $ln]]]
                      }
                      :if ($plen >= $minPlen && $plen <= $maxPlen) do={
                        :if ($v6) do={
                          :if ([:typeof [:toip6 $ip]] = "ip6") do={
                            :do {
                              /ipv6 firewall address-list add list=$target address=$ln timeout=7d
                              :set added ($added + 1)
                              :set ok true
                            } on-error={}
                          }
                        } else={
                          :if ([:typeof [:toip $ip]] = "ip") do={
                            :do {
                              /ip firewall address-list add list=$target address=$ln timeout=7d
                              :set added ($added + 1)
                              :set ok true
                            } on-error={}
                          }
                        }
                      }
                    }
                    :if (!$ok) do={ :set bad ($bad + 1) }
                  }
                }
              }
            }
            :set carry [:pick $buf $s [:len $buf]]
            :if ([:len $carry] > $maxLine) do={
              # an over-long first line cannot be the header
              :if ($first) do={ :set reason "no header" }
              :if (!$skip) do={ :set bad ($bad + 1) }
              :set carry ""
              :set skip true
            }
          }
        } on-error={ :set reason "read error" }
        :if ($reason = "") do={
          :if ($footer < 0) do={
            :set reason "no footer"
          } else={
            :if ($footer != $entries) do={
              :set reason "header/footer mismatch"
            } else={
              :if (($bad * 100) > $entries) do={
                :set reason "too many invalid lines ($bad)"
              } else={
                :if ($added != $entries) do={
                  :set reason "count mismatch"
                } else={
                  :if ($added < $minEntries) do={ :set reason "too few entries ($added)" }
                }
              }
            }
          }
        }
        :if ($reason != "") do={
          :if ($v6) do={
            /ipv6 firewall address-list remove [find where list=$target]
          } else={
            /ip firewall address-list remove [find where list=$target]
          }
          :if (!$boot) do={ /file remove [find where name=$fname] }
          :log error ("MBL: $fname rejected ($reason), active " . $fl . "list kept")
          :error "MBL: abort"
        }
        :local n $added

        # ---- the in rule must still exist and point where it did when this
        # update started ($cur; $old for every rule the installer writes): a
        # reinstall may have changed the rules meanwhile. Then this load is
        # dropped and the old list kept.
        :local curNow ""
        :local nNow 0
        :if ($v6) do={
          :local inRule [/ipv6 firewall raw find where comment="MBL: in"]
          :set nNow [:len $inRule]
          :if ($nNow > 0) do={ :set curNow [/ipv6 firewall raw get ($inRule->0) src-address-list] }
        } else={
          :local inRule [/ip firewall raw find where comment="MBL: in"]
          :set nNow [:len $inRule]
          :if ($nNow > 0) do={ :set curNow [/ip firewall raw get ($inRule->0) src-address-list] }
        }
        :if ($nNow = 0 || $curNow != $cur) do={
          :if ($v6) do={
            /ipv6 firewall address-list remove [find where list=$target]
          } else={
            /ip firewall address-list remove [find where list=$target]
          }
          :if (!$boot) do={ /file remove [find where name=$fname] }
          :log warning "MBL: $fam rules changed during update, aborted"
          :error "MBL: abort"
        }

        # ---- switch rules, then empty the old list
        :if ($v6) do={
          /ipv6 firewall raw set [find where comment="MBL: in"] src-address-list=$target
          /ipv6 firewall raw set [find where comment="MBL: out"] dst-address-list=$target
          /ipv6 firewall raw set [find where comment="MBL: router-out"] dst-address-list=$target
        } else={
          /ip firewall raw set [find where comment="MBL: in"] src-address-list=$target
          /ip firewall raw set [find where comment="MBL: out"] dst-address-list=$target
          /ip firewall raw set [find where comment="MBL: router-out"] dst-address-list=$target
        }
        # the old list is emptied only once the in rule really points at the
        # new one (the rules could still change between the check and the switch)
        :local curSet ""
        :if ($v6) do={
          :local inRule [/ipv6 firewall raw find where comment="MBL: in"]
          :if ([:len $inRule] > 0) do={ :set curSet [/ipv6 firewall raw get ($inRule->0) src-address-list] }
        } else={
          :local inRule [/ip firewall raw find where comment="MBL: in"]
          :if ([:len $inRule] > 0) do={ :set curSet [/ip firewall raw get ($inRule->0) src-address-list] }
        }
        :if ($curSet != $target) do={
          :log warning "MBL: $fam rules changed during update, aborted"
          :error "MBL: abort"
        }
        :if ($v6) do={
          /ipv6 firewall address-list remove [find where list=$old]
        } else={
          /ip firewall address-list remove [find where list=$old]
        }

        # ---- keep the new file as the boot copy
        # new -> tmp first; the old boot copy is only removed once tmp exists,
        # so a valid boot copy (last or tmp) is never lost.
        :if (!$boot) do={
          # recover a temp copy left by an earlier failed rename
          :if ([:len [/file find where name=$lastFile]] = 0 && [:len [/file find where name=$tmpFile]] > 0) do={
            :local rec [$mblMove $tmpFile $lastFile]
          }
          # flash/: RAM -> flash copy, only with room for it (2 x the file size)
          :local room true
          :if ($dir != "") do={
            :local free 0
            :do { :set free [/system resource get free-hdd-space] } on-error={}
            :if ($free <= ($fsize * 2)) do={
              :set room false
              /file remove [find where name=$newFile]
              :log warning ("MBL: " . $fl . "not enough flash space for the boot copy ($free bytes free, need more than 2 x $fsize), keeping the previous $lastFile")
            }
          }
          :if ($room) do={
            /file remove [find where name=$tmpFile]
            :local saved false
            :if ($dir = "") do={
              :set saved [$mblMove $newFile $tmpFile]
            } else={
              :set saved [$mblCopy $newFile $tmpFile]
              /file remove [find where name=$newFile]
            }
            :if ($saved) do={
              /file remove [find where name=$lastFile]
              :if (![$mblMove $tmpFile $lastFile]) do={
                :log warning ("MBL: " . $fl . "could not rename $tmpFile to $lastFile, boot will use $tmpFile")
              }
            } else={
              /file remove [find where name=$newFile]
              :log warning ("MBL: " . $fl . "could not save the boot copy, keeping the previous $lastFile")
            }
          }
        }

        :if ($v6) do={ :set mblLastOk6 true } else={ :set mblLastOk true }
        :log info ("MBL: loaded $n " . $fl . "entries into $target (list generated $gen)")

        # The notices below are IPv4 only: the IPv6 list comes from the same build.
        :if (!$v6) do={
          # ---- stale list: the list is loaded either way; this only warns, e.g.
          # when the GitHub Action has stopped. Errors here are ignored so they can
          # never fail an update that already succeeded.
          :do {
            :if ($gen != "unknown") do={
              :local age ([$mblDays [/system clock get date]] - [$mblDays [:pick $gen 0 10]])
              :if ($age > $maxAgeDays) do={ :log warning "MBL: list is $age days old" }
            }
          } on-error={}

          # ---- newer script: only a notice, the router never updates itself.
          # mblVerGt rejects any value that is not MAJOR.MINOR.PATCH, so only such
          # a value is logged. Errors are ignored, as for the stale list warning.
          :do {
            :if ([$mblVerGt $pubScript $scriptVersion] = true) do={
              :log warning ("MBL: script $pubScript available (installed $scriptVersion) - reinstall: /tool fetch url=\"https://raw.githubusercontent.com/Psycomy/MikroBlockList/main/routeros/install.rsc\" check-certificate=yes; /import install.rsc")
            }
          } on-error={}
        }
      } on-error={ :log warning ("MBL: " . $fl . "update aborted") }
    }
  }
  # ---- drop counters: packets of the in / out / router-out rules since the
  # previous report, then reset. Not at boot (the counters restart at zero
  # with the router) nor on the installer's first load (new rules). Only
  # numbers read from the rules are logged; errors are ignored so the report
  # can never fail an update.
  :if (!$boot && !$owned) do={
    :do {
      :local now ([/system clock get date] . " " . [/system clock get time])
      :local since [:tostr $mblStatsSince]
      :if ([:len $since] = 0) do={ :set since "boot" }
      :local c4 ""
      :local c6 ""
      :local ri [/ip firewall raw find where comment="MBL: in"]
      :local ro [/ip firewall raw find where comment="MBL: out"]
      :local rr [/ip firewall raw find where comment="MBL: router-out"]
      :if ([:len $ri] > 0 && [:len $ro] > 0 && [:len $rr] > 0) do={
        :local pIn [/ip firewall raw get ($ri->0) packets]
        /ip firewall raw reset-counters ($ri->0)
        :local pOut [/ip firewall raw get ($ro->0) packets]
        /ip firewall raw reset-counters ($ro->0)
        :local pRtr [/ip firewall raw get ($rr->0) packets]
        /ip firewall raw reset-counters ($rr->0)
        :set c4 ("in $pIn, out $pOut, router-out $pRtr")
      }
      :if ($ipv6 = "yes") do={
        :local ri6 [/ipv6 firewall raw find where comment="MBL: in"]
        :local ro6 [/ipv6 firewall raw find where comment="MBL: out"]
        :local rr6 [/ipv6 firewall raw find where comment="MBL: router-out"]
        :if ([:len $ri6] > 0 && [:len $ro6] > 0 && [:len $rr6] > 0) do={
          :local pIn [/ipv6 firewall raw get ($ri6->0) packets]
          /ipv6 firewall raw reset-counters ($ri6->0)
          :local pOut [/ipv6 firewall raw get ($ro6->0) packets]
          /ipv6 firewall raw reset-counters ($ro6->0)
          :local pRtr [/ipv6 firewall raw get ($rr6->0) packets]
          /ipv6 firewall raw reset-counters ($rr6->0)
          :set c6 ("; ipv6 in $pIn, out $pOut, router-out $pRtr")
        }
      }
      # without the IPv4 rules (e.g. a reinstall is renaming them) there is
      # nothing to report; the period goes on
      :if ($c4 != "") do={
        :local verb "dropped"
        :if ($mode = "log") do={ :set verb "would have dropped" }
        :log info ("MBL: " . $verb . " since " . $since . ": " . $c4 . " packets" . $c6)
        :set mblStatsSince $now
      }
    } on-error={}
  }
  :if (!$boot && $mode = "log") do={
    :log warning "MBL: log-only mode, nothing is blocked (reinstall with :global mblMode \"block\" to block)"
  }
  # the installer's first load leaves the lock to the installer
  :if (!$owned) do={ :set mblRunning false }
}
}

# Inject the settings into the stored script: in source $1, replace
# placeholder $2 with $3. The source is edited as a string and written back
# once, because RouterOS logs every change of a script's source in full.
:local mblInject do={
  :local p [:find $1 $2]
  :if ([:typeof $p] = "nil") do={ :error "MBL: internal error, placeholder $2 not found in mbl-update" }
  :return ([:pick $1 0 $p] . $3 . [:pick $1 ($p + [:len $2]) [:len $1]])
}
:local mblSrc [/system script get [find where name="mbl-update"] source]
:set mblSrc [$mblInject $mblSrc "@MBL_URL@" $mblUrl]
:set mblSrc [$mblInject $mblSrc "@MBL_URL6@" $mblUrl6]
:set mblSrc [$mblInject $mblSrc "@MBL_VERSION@" $mblScriptVersion]
:set mblSrc [$mblInject $mblSrc "@MBL_VARIANT@" $mblVar]
:set mblSrc [$mblInject $mblSrc "@MBL_IPV6@" $mblV6]
:set mblSrc [$mblInject $mblSrc "@MBL_MODE@" $mblMd]
/system script set [find where name="mbl-update"] source=$mblSrc

# --------------------------------------------------------- 6. schedulers
# Daily at a random time so routers do not all hit GitHub at once.
:local r [:rndnum from=0 to=86399]
:local startTime ([$mblPad ($r / 3600)] . ":" . [$mblPad (($r % 3600) / 60)] . ":" . [$mblPad ($r % 60)])
/system scheduler add name=mbl-daily interval=1d start-time=$startTime \
  policy=ftp,read,write,policy,test comment="MBL: daily update" \
  on-event="/system script run mbl-update"
# At boot: reload the last good copy at once (timeout entries do not survive
# a reboot), then try a fresh download once the network is likely up.
/system scheduler add name=mbl-boot start-time=startup \
  policy=ftp,read,write,policy,test comment="MBL: load at boot" \
  on-event={:global mblBoot true; /system script run mbl-update; :delay 60s; /system script run mbl-update}

# ------------------------------------------------- 7. raw rules (at the top)
# Drop rules point at $active / $active6 (see step 3); mbl-update switches them later.
# Log mode changes only the action of the three drop rules (comments and order
# are the same): in only counts (passthrough: inbound scanners would flood the
# log), out and router-out log; nothing is dropped.
:local aIn "drop"
:local aOut "drop"
:local logOut "yes"
:local aRtr "drop"
:local preRtr ""
:if ($mblMd = "log") do={
  :set aIn "passthrough"
  :set aOut "log"
  :set logOut "no"
  :set aRtr "log"
  :set preRtr "MBL-ROUTER"
}
# The anchor is the first raw rule, whatever it is: on a reinstall usually the
# first MBL-OLD: rule, so the new rules go where the old ones were (at the top
# unless you moved them) and the user's own rules keep their order below.
:local existing [/ip firewall raw find]
:if ([:len $existing] = 0) do={
  /ip firewall raw add chain=prerouting src-address-list=mbl-allow action=accept comment="MBL: allow-in"
  /ip firewall raw add chain=prerouting dst-address-list=mbl-allow action=accept comment="MBL: allow-out"
  /ip firewall raw add chain=prerouting in-interface-list=WAN src-address-list=$active action=$aIn comment="MBL: in"
  /ip firewall raw add chain=prerouting in-interface-list=LAN dst-address-list=$active action=$aOut log=$logOut log-prefix="MBL-OUT" comment="MBL: out"
  /ip firewall raw add chain=output dst-address-list=mbl-allow action=accept comment="MBL: allow-router-out"
  /ip firewall raw add chain=output dst-address-list=$active action=$aRtr log-prefix=$preRtr comment="MBL: router-out"
} else={
  # Each rule goes just before the anchor (the rule that was first), so ours
  # keep this order above it.
  :local anchor ($existing->0)
  /ip firewall raw add chain=prerouting src-address-list=mbl-allow action=accept comment="MBL: allow-in" place-before=$anchor
  /ip firewall raw add chain=prerouting dst-address-list=mbl-allow action=accept comment="MBL: allow-out" place-before=$anchor
  /ip firewall raw add chain=prerouting in-interface-list=WAN src-address-list=$active action=$aIn comment="MBL: in" place-before=$anchor
  /ip firewall raw add chain=prerouting in-interface-list=LAN dst-address-list=$active action=$aOut log=$logOut log-prefix="MBL-OUT" comment="MBL: out" place-before=$anchor
  /ip firewall raw add chain=output dst-address-list=mbl-allow action=accept comment="MBL: allow-router-out" place-before=$anchor
  /ip firewall raw add chain=output dst-address-list=$active action=$aRtr log-prefix=$preRtr comment="MBL: router-out" place-before=$anchor
}
# The same 6 rules in /ipv6 firewall raw, with IPv6 on only.
:if ($mblV6 = "yes") do={
  :local existing6 [/ipv6 firewall raw find]
  :if ([:len $existing6] = 0) do={
    /ipv6 firewall raw add chain=prerouting src-address-list=mbl6-allow action=accept comment="MBL: allow-in"
    /ipv6 firewall raw add chain=prerouting dst-address-list=mbl6-allow action=accept comment="MBL: allow-out"
    /ipv6 firewall raw add chain=prerouting in-interface-list=WAN src-address-list=$active6 action=$aIn comment="MBL: in"
    /ipv6 firewall raw add chain=prerouting in-interface-list=LAN dst-address-list=$active6 action=$aOut log=$logOut log-prefix="MBL-OUT" comment="MBL: out"
    /ipv6 firewall raw add chain=output dst-address-list=mbl6-allow action=accept comment="MBL: allow-router-out"
    /ipv6 firewall raw add chain=output dst-address-list=$active6 action=$aRtr log-prefix=$preRtr comment="MBL: router-out"
  } else={
    :local anchor6 ($existing6->0)
    /ipv6 firewall raw add chain=prerouting src-address-list=mbl6-allow action=accept comment="MBL: allow-in" place-before=$anchor6
    /ipv6 firewall raw add chain=prerouting dst-address-list=mbl6-allow action=accept comment="MBL: allow-out" place-before=$anchor6
    /ipv6 firewall raw add chain=prerouting in-interface-list=WAN src-address-list=$active6 action=$aIn comment="MBL: in" place-before=$anchor6
    /ipv6 firewall raw add chain=prerouting in-interface-list=LAN dst-address-list=$active6 action=$aOut log=$logOut log-prefix="MBL-OUT" comment="MBL: out" place-before=$anchor6
    /ipv6 firewall raw add chain=output dst-address-list=mbl6-allow action=accept comment="MBL: allow-router-out" place-before=$anchor6
    /ipv6 firewall raw add chain=output dst-address-list=$active6 action=$aRtr log-prefix=$preRtr comment="MBL: router-out" place-before=$anchor6
  }
}
# The new rules are in place: only now do the old ones go.
/ip firewall raw remove [find where comment~"^MBL-OLD:"]
/ipv6 firewall raw remove [find where comment~"^MBL-OLD:"]
} do={
  # an error in steps 2-7: release the lock, then abort with that error
  :set mblRunning false
  :error $mblErr
}

# -------------------------------------------------------- 8. first load
# Still under the installer's lock: :global mblInstallRun tells this one
# mbl-update run to go ahead despite the lock (it clears the flag at once and
# leaves the lock alone). The installer releases the lock afterwards, in
# every case.
# The new rules count from zero: the next mbl-update reports from now on.
:global mblStatsSince
:set mblStatsSince ([/system clock get date] . " " . [/system clock get time])
:global mblInstallRun
:global mblLastOk
:global mblLastOk6
:set mblLastOk false
:set mblLastOk6 false
:set mblInstallRun true
:do { /system script run mbl-update } on-error={}
:set mblInstallRun false
:set mblRunning false
/file remove [find where name="install.rsc" or name="flash/install.rsc"]
:if ($mblLastOk = true) do={
  # IPv6 is best effort: IPv4 is installed even when the first IPv6 load failed.
  :local note ""
  :if (($mblV6 = "yes") && ($mblLastOk6 != true)) do={ :set note " - first ipv6 load failed, see log" }
  :put ("MBL: installed (script $mblScriptVersion, variant $mblVar, ipv6 $mblV6, mode " . $mblMd . $note . ")")
  :log info ("MBL: installed (script $mblScriptVersion, variant $mblVar, ipv6 $mblV6, mode " . $mblMd . $note . ")")
  :if ([:len $mblSlow] > 0) do={ :put "MBL: small router: the lite variant is much lighter (see the warning above)." }
} else={
  :put "MBL: WARNING installed (script $mblScriptVersion, variant $mblVar, ipv6 $mblV6, mode $mblMd), but the first load failed or was skipped. See /log print where message~\"MBL\""
  :log warning "MBL: installed (script $mblScriptVersion, variant $mblVar, ipv6 $mblV6, mode $mblMd), but the first load failed or was skipped"
}
}
