#!/usr/bin/env bash
# Prepare the vendor Mlaser/NexCut tool under Wine for session H (desk work, no machine).
#
# Session H answers the questions of docs/STATUS.md §3.6 - the M2 sort reference, the M3
# "Mlaser loads port-written files" gate, X7 and the crafts/lead/compensation diffs - by
# driving the *vendor* program.  This script only PREPARES the prefix; docs/WINE-SESSION-H.md
# lists what the owner then clicks and what to save where.
#
# Recipe and its evidence: docs/analysis/10-wine-mlaser-prior-work.md (32-bit prefix,
# winetricks mfc42 for Dxf2Grp -> MFC42.DLL, run from C: so \Technology\{Fiber,CO2} can be
# created at the drive root), docs/M1-BENCH-SESSION.md §8 (EnableLog=1), 09 §3.3 / 04 §4.6
# (the nesting dongle, USB HID 3689:8762).
#
# Rules this script keeps:
#   * The package under --src is READ-ONLY.  Everything is done to a working copy; no write
#     path may resolve inside --src (guarded, not just intended).
#   * Idempotent: every step checks first and reports "already done" instead of redoing it.
#   * Each step is verified after it runs; a failed verification is a hard error.
#   * The udev rule needs root, so the script writes the rule file and PRINTS the pkexec
#     command for the owner to run.  It never elevates by itself.
#
# Usage: tools/wine_session_h.sh [--dry-run] [--prefix DIR] [--src DIR] [--work DIR] [--help]

set -euo pipefail

SRC_DEFAULT="$HOME/Documents/CF1390-250715-1084-0973/Mlaser-v0.0.0.52"
PREFIX_DEFAULT="$HOME/.wine-mlaser"
WORK_DEFAULT="$HOME/mlaser-wine"

DRY_RUN=0
SRC="${NEXCUT_SRC:-$SRC_DEFAULT}"
PREFIX="${WINEPREFIX:-$PREFIX_DEFAULT}"
WORK="$WORK_DEFAULT"

UDEV_RULE_NAME="99-mlaser-dongle.rules"
UDEV_RULE_PATH="/etc/udev/rules.d/$UDEV_RULE_NAME"
DONGLE_VID="3689"
DONGLE_PID="8762"

ok=0
fail=0

# $USER is not exported by every shell (cron, a container, `env -u USER`), and `set -u`
# turns a bare reference into a hard abort halfway through the run.
WINE_USER="${USER:-${LOGNAME:-$(id -un 2>/dev/null || echo user)}}"

usage() {
    awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$0"
    cat <<EOF

Options:
  --dry-run        print the plan, change nothing, exit 0 (safe without a Wine prefix)
  --prefix DIR     Wine prefix to prepare   (default: $PREFIX_DEFAULT, or \$WINEPREFIX)
  --src DIR        read-only vendor package (default: $SRC_DEFAULT, or \$NEXCUT_SRC)
  --work DIR       where the working copy and the generated files go (default: $WORK_DEFAULT)
  -h, --help       this text
EOF
}

say()  { printf '%s\n' "$*"; }
step() { printf '\n== %s\n' "$*"; }
good() { ok=$((ok + 1)); printf '   ok       %s\n' "$*"; }
skip() { ok=$((ok + 1)); printf '   already  %s\n' "$*"; }
plan() { printf '   DRY-RUN  %s\n' "$*"; }
warn() { printf '   note     %s\n' "$*"; }
die()  { fail=$((fail + 1)); printf '   FAILED   %s\n' "$*" >&2; exit 1; }

abspath() { # no realpath -e: the path need not exist yet
    python3 -c 'import os,sys; print(os.path.realpath(os.path.expanduser(sys.argv[1])))' "$1"
}

# Every write goes through this: refuse anything that would land inside the vendor package.
guard_write() {
    local target abs_src
    target="$(abspath "$1")"
    abs_src="$(abspath "$SRC")"
    case "$target" in
        "$abs_src" | "$abs_src"/*)
            die "refusing to write inside the read-only vendor package: $target"
            ;;
    esac
}

run() { # run a command, or print it under --dry-run
    if [ "$DRY_RUN" = 1 ]; then
        plan "would run: $*"
    else
        "$@"
    fi
}

have() { command -v "$1" >/dev/null 2>&1; }

require_tool() {
    if have "$1"; then
        good "$1 found ($(command -v "$1"))"
    elif [ "$DRY_RUN" = 1 ]; then
        plan "MISSING: $1 - install it before the real run ($2)"
    else
        die "$1 is not installed ($2)"
    fi
}

while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) DRY_RUN=1 ;;
        --prefix) PREFIX="${2:?--prefix needs a directory}"; shift ;;
        --src) SRC="${2:?--src needs a directory}"; shift ;;
        --work) WORK="${2:?--work needs a directory}"; shift ;;
        -h|--help) usage; exit 0 ;;
        *) printf 'wine_session_h.sh: unknown argument %s\n\n' "$1" >&2; usage >&2; exit 2 ;;
    esac
    shift
done

SRC="$(abspath "$SRC")"
PREFIX="$(abspath "$PREFIX")"
WORK="$(abspath "$WORK")"
COPY="$WORK/Mlaser"
DRIVE_C="$PREFIX/drive_c"
LINK="$DRIVE_C/Mlaser"
RULE_FILE="$WORK/$UDEV_RULE_NAME"

say "Wine session H preparation$( [ "$DRY_RUN" = 1 ] && printf ' (DRY RUN - nothing is changed)')"
say "  vendor package (read-only) : $SRC"
say "  Wine prefix                : $PREFIX   (WINEARCH=win32)"
say "  working copy               : $COPY  ->  C:\\Mlaser"

# ---------------------------------------------------------------------------- 0. preflight
step "0. Preflight"
case "$COPY/" in
    "$SRC"/*) die "--work must not be inside the vendor package ($SRC)" ;;
esac
case "$PREFIX/" in
    "$SRC"/*) die "--prefix must not be inside the vendor package ($SRC)" ;;
esac
case "$SRC/" in
    "$WORK"/*) die "--src must not be inside --work ($WORK)" ;;
esac
good "no write target resolves inside the vendor package"

require_tool wine "the 32-bit Wine runtime; on Debian/Ubuntu also install wine32:i386"
require_tool winetricks "winetricks -q mfc42 (docs/analysis/10 fix 2)"
require_tool python3 "path handling in this script"

if [ -d "$SRC" ] && [ -f "$SRC/MainApp.exe" ]; then
    good "vendor package found ($(du -sh "$SRC" 2>/dev/null | cut -f1)), MainApp.exe present"
elif [ "$DRY_RUN" = 1 ]; then
    plan "MISSING: vendor package at $SRC (pass --src, or set NEXCUT_SRC)"
else
    die "no MainApp.exe under $SRC - pass --src, or set NEXCUT_SRC"
fi

# ------------------------------------------------------------------- 1. the working copy
step "1. Working copy of the package (the original is never touched)"
guard_write "$COPY"
PART="$COPY.part"
guard_write "$PART"
if [ -f "$COPY/MainApp.exe" ]; then
    skip "working copy exists at $COPY (kept - it holds the session's edits)"
elif [ "$DRY_RUN" = 1 ]; then
    plan "would create $WORK and copy $SRC -> $COPY (cp -a, ~103 MB)"
    if [ -e "$PART" ]; then plan "would first discard the interrupted copy at $PART"; fi
    if [ -e "$COPY" ]; then plan "would first discard the incomplete working copy at $COPY"; fi
else
    mkdir -p "$WORK"
    # An interrupted run leaves $PART behind, and `cp -a SRC DEST` copies *into* an
    # existing DEST - which would nest the package one level down and wedge every later
    # run. Both leftovers are discarded first; both are under --work, never under --src
    # (guard_write above), and neither can hold anything but a partial copy.
    if [ -e "$PART" ]; then
        rm -rf -- "$PART"
        warn "discarded the interrupted copy at $PART"
    fi
    if [ -e "$COPY" ]; then
        rm -rf -- "$COPY"
        warn "discarded the incomplete working copy at $COPY (no MainApp.exe)"
    fi
    cp -a "$SRC" "$PART"
    [ -f "$PART/MainApp.exe" ] || die "copy incomplete: no MainApp.exe in $PART"
    mv "$PART" "$COPY"
    [ -f "$COPY/MainApp.exe" ] || die "copy incomplete: no MainApp.exe in $COPY"
    good "copied the package to $COPY"
fi

# ------------------------------------------------------------------------ 2. the prefix
step "2. 32-bit Wine prefix (docs/analysis/10 fix 1)"
guard_write "$PREFIX"
if [ -d "$DRIVE_C/windows" ]; then
    arch="$(sed -n 's/^#arch=//p' "$PREFIX/system.reg" 2>/dev/null | head -1)"
    if [ "$arch" = "win32" ]; then
        skip "prefix exists and is win32 ($PREFIX)"
    elif [ -n "$arch" ]; then
        die "prefix $PREFIX is $arch, not win32 - delete it or pass another --prefix"
    else
        warn "prefix $PREFIX exists but its arch could not be read from system.reg"
    fi
elif [ "$DRY_RUN" = 1 ]; then
    plan "would run: WINEPREFIX=$PREFIX WINEARCH=win32 wineboot -u"
    plan "would verify: $DRIVE_C/windows exists and system.reg says #arch=win32"
else
    mkdir -p "$PREFIX"
    WINEPREFIX="$PREFIX" WINEARCH=win32 wineboot -u
    [ -d "$DRIVE_C/windows" ] || die "wineboot did not create $DRIVE_C/windows"
    [ "$(sed -n 's/^#arch=//p' "$PREFIX/system.reg" | head -1)" = "win32" ] \
        || die "the new prefix is not win32 - remove $PREFIX and re-run"
    good "created the win32 prefix"
fi

# --------------------------------------------------------------------------- 3. mfc42
step "3. MFC42.DLL (Dxf2Grp.dll is a VC6 build - docs/analysis/10 fix 2)"
if [ -f "$DRIVE_C/windows/system32/mfc42.dll" ]; then
    skip "mfc42.dll present in the prefix"
elif [ "$DRY_RUN" = 1 ]; then
    plan "would run: WINEPREFIX=$PREFIX winetricks -q mfc42"
    plan "would verify: $DRIVE_C/windows/system32/mfc42.dll exists"
else
    WINEPREFIX="$PREFIX" winetricks -q mfc42
    [ -f "$DRIVE_C/windows/system32/mfc42.dll" ] || die "winetricks did not install mfc42.dll"
    good "installed mfc42"
fi

# ------------------------------------------------ 4. the working copy reachable as C:\Mlaser
step "4. C:\\Mlaser -> the working copy (run from C:, never from Z: - docs/analysis/10 fix 3)"
guard_write "$LINK"
if [ -L "$LINK" ]; then
    current="$(abspath "$(readlink "$LINK")")"
    if [ "$current" = "$COPY" ]; then
        skip "C:\\Mlaser already points at the working copy"
    else
        die "C:\\Mlaser points at $current, not at $COPY - remove it by hand if that is stale"
    fi
elif [ -e "$LINK" ]; then
    die "$LINK exists and is not a symlink - move it aside"
elif [ "$DRY_RUN" = 1 ]; then
    plan "would symlink $LINK -> $COPY (the working copy, never $SRC)"
else
    ln -s "$COPY" "$LINK"
    [ -f "$LINK/MainApp.exe" ] || die "the symlink does not resolve to the package"
    good "linked C:\\Mlaser -> $COPY"
fi

# ------------------------------------------------------------------- 5. Technology folders
step "5. C:\\Technology\\{Fiber,CO2} (the app creates them at the drive root)"
for sub in Fiber CO2; do
    dir="$DRIVE_C/Technology/$sub"
    guard_write "$dir"
    if [ -d "$dir" ]; then
        skip "$dir"
    elif [ "$DRY_RUN" = 1 ]; then
        plan "would create $dir"
    else
        mkdir -p "$dir"
        [ -d "$dir" ] || die "could not create $dir"
        good "created $dir"
    fi
done

# --------------------------------------------------------------------------- 6. EnableLog
step "6. EnableLog=1 in the WORKING COPY's File/ipAdd.ini [Soft] (04 §9)"
INI="$COPY/File/ipAdd.ini"
guard_write "$INI"
if [ "$DRY_RUN" = 1 ]; then
    plan "would set EnableLog=1 in [Soft] of $INI (CRLF kept; the original stays EnableLog=0)"
    plan "would verify: the [Soft] section then reads EnableLog=1"
elif [ ! -f "$INI" ]; then
    die "no $INI - is the working copy complete?"
elif tr -d '\r' < "$INI" | grep -qx 'EnableLog=1'; then
    skip "EnableLog is already 1"
else
    python3 - "$INI" <<'PY'
import sys
from pathlib import Path

# ipAdd.ini is CRLF, cp936-ish; edit bytes so nothing else in the file can change.
path = Path(sys.argv[1])
raw = path.read_bytes()
out, section, changed = [], b"", 0
for line in raw.split(b"\n"):
    stripped = line.rstrip(b"\r")
    if stripped.startswith(b"[") and stripped.endswith(b"]"):
        section = stripped
    if section == b"[Soft]" and stripped.startswith(b"EnableLog="):
        line = b"EnableLog=1" + (b"\r" if line.endswith(b"\r") else b"")
        changed += 1
    out.append(line)
if changed != 1:
    raise SystemExit(f"expected exactly one EnableLog= line in [Soft], found {changed}")
path.write_bytes(b"\n".join(out))
PY
    tr -d '\r' < "$INI" | grep -qx 'EnableLog=1' || die "EnableLog=1 did not take in $INI"
    good "set EnableLog=1 (logs land in $DRIVE_C/users/$WINE_USER/AppData/Local/NexCut/Log/)"
fi

# ------------------------------------------------------------------------- 7. udev rule
step "7. udev rule for the nesting dongle USB HID $DONGLE_VID:$DONGLE_PID (09 §3.3)"
guard_write "$RULE_FILE"
read -r -d '' RULE <<EOF || true
# Mlaser/NexCut nesting dongle ("USBKey", PWDKeyCo) - docs/analysis/09 §3.3, 04 §4.6.
# Wine reaches an HID device through /dev/hidraw*, which is root-only by default.
# uaccess gives it to the user logged in at the seat; the group fallback covers systems
# without logind.  It gates auto-nesting only - jog, home and file work need no dongle.
SUBSYSTEM=="hidraw", ATTRS{idVendor}=="$DONGLE_VID", ATTRS{idProduct}=="$DONGLE_PID", TAG+="uaccess", MODE="0660", GROUP="plugdev"
SUBSYSTEM=="usb", ATTRS{idVendor}=="$DONGLE_VID", ATTRS{idProduct}=="$DONGLE_PID", TAG+="uaccess", MODE="0660", GROUP="plugdev"
EOF

if [ "$DRY_RUN" = 1 ]; then
    plan "would write the rule to $RULE_FILE"
    plan "would NOT install it: that needs root, so the pkexec command is printed instead"
else
    mkdir -p "$WORK"
    printf '%s\n' "$RULE" > "$RULE_FILE"
    good "wrote $RULE_FILE"
fi

if [ -f "$UDEV_RULE_PATH" ] && [ "$DRY_RUN" = 0 ] && cmp -s "$RULE_FILE" "$UDEV_RULE_PATH"; then
    skip "the rule is already installed at $UDEV_RULE_PATH"
else
    say ""
    say "   The rule needs root.  This script does not elevate; run this yourself:"
    say ""
    say "       pkexec install -m 0644 -o root -g root '$RULE_FILE' '$UDEV_RULE_PATH'"
    say "       pkexec udevadm control --reload-rules"
    say "       pkexec udevadm trigger --subsystem-match=hidraw --subsystem-match=usb"
    say ""
    say "   Then re-plug the dongle and check:  ls -l /dev/hidraw*"
    say "   (a password dialog appears in the graphical session, not in this terminal)"
fi

# ------------------------------------------------------------------------------- summary
step "Summary"
say "   $ok step(s) ok, $fail failed."
if [ "$DRY_RUN" = 1 ]; then
    say "   Dry run: nothing was created.  Re-run without --dry-run to prepare the prefix."
else
    say "   Start the vendor tool with:"
    say ""
    say "       cd '$LINK' && WINEPREFIX='$PREFIX' wine MainApp.exe"
    say ""
fi
say "   Then follow docs/WINE-SESSION-H.md: it lists, per question, what to click, what to"
say "   save where, and which port test consumes it."
