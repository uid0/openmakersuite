#!/bin/bash
# Restore the host-side deployment configuration (.env + compose overrides
# + EMQX bootstrap) from an archive produced by backup-config.sh.
#
# The archive is expected to contain RELATIVE paths from the repo root —
# backup-config.sh writes the tarball with that shape, so this script
# extracts back into the current directory. Anything in the archive that
# already exists on disk is moved aside to a timestamped `.bak` neighbor
# first (e.g. `.env` → `.env.20260609T193000Z.bak`) so a botched restore
# doesn't take a real prod env file with it.
#
# Pass --force or set RESTORE_CONFIG_CONFIRM=YES to skip the interactive
# confirmation. This script does NOT restart any containers — config
# files are picked up on the next `docker compose up`; leaving the
# restart policy to the operator keeps a config restore drill from
# bouncing live workers.
#
# Usage:
#   scripts/restore-config.sh oms-config-20260609T053000Z.tar.gz
#   scripts/restore-config.sh --force oms-config-*.tar.gz
set -euo pipefail

FORCE="${RESTORE_CONFIG_CONFIRM:-}"
TARGET_DIR="${TARGET_DIR:-.}"

usage() {
    cat <<EOF
Usage: $0 [--force] [--target-dir=DIR] <config-archive.tar.gz>

Restore deployment config from a backup-config.sh archive. Existing files
are moved aside to <name>.<utc>.bak before being overwritten so a botched
restore can be rolled back by hand.

  --force, -y                  Skip the interactive confirmation prompt
  --target-dir=DIR             Extract into DIR (default: current directory)
  --help, -h                   Show this help

Environment overrides:
  TARGET_DIR                   Same as --target-dir
  RESTORE_CONFIG_CONFIRM=YES   Same effect as --force
EOF
}

ARCHIVE=""
while [ $# -gt 0 ]; do
    case "$1" in
        --force|-y)        FORCE=YES; shift ;;
        --target-dir=*)    TARGET_DIR="${1#--target-dir=}"; shift ;;
        --target-dir)      TARGET_DIR="$2"; shift 2 ;;
        --help|-h)         usage; exit 0 ;;
        --*)               echo "Unknown argument: $1" >&2; usage >&2; exit 1 ;;
        *)
            if [ -z "$ARCHIVE" ]; then
                ARCHIVE="$1"; shift
            else
                echo "Unexpected positional argument: $1" >&2; exit 1
            fi
            ;;
    esac
done

if [ -z "$ARCHIVE" ]; then
    usage >&2
    exit 1
fi

if [ ! -f "$ARCHIVE" ]; then
    echo "File not found: $ARCHIVE" >&2
    exit 1
fi

if [ ! -d "$TARGET_DIR" ]; then
    echo "Target dir does not exist: $TARGET_DIR" >&2
    exit 1
fi

# Gzip integrity check up front — restoring from a corrupt tarball would
# leave the target dir in a half-replaced state.
if ! gunzip -t "$ARCHIVE" 2>/dev/null; then
    echo "ERROR: $ARCHIVE failed gzip integrity check (cowardly refusing to restore)" >&2
    exit 2
fi

# Refuse archives that contain absolute paths or `..` traversal — those
# would land outside TARGET_DIR and could clobber unrelated host files.
if tar -tzf "$ARCHIVE" 2>/dev/null | grep -qE '^/|(^|/)\.\./'; then
    echo "ERROR: $ARCHIVE contains absolute or parent-relative paths; refusing to extract" >&2
    exit 2
fi

if [ "$FORCE" != "YES" ]; then
    echo "Archive contents:"
    tar -tzf "$ARCHIVE" | sed 's/^/  /'
    printf "Restore these into '%s'? Existing files will be moved aside to .bak. Type 'YES' to continue: " "$TARGET_DIR"
    read -r confirm
    if [ "$confirm" != 'YES' ]; then
        echo 'Aborted.'
        exit 1
    fi
fi

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
MOVED=()

# Move existing files aside first so the extract can't half-clobber and
# leave us in a confusing state. Loop is read from a process substitution
# so the MOVED array survives.
while IFS= read -r entry; do
    # Skip directory entries (tar lists them with a trailing slash).
    case "$entry" in
        */) continue ;;
    esac
    target="$TARGET_DIR/$entry"
    if [ -e "$target" ] || [ -L "$target" ]; then
        bak="${target}.${STAMP}.bak"
        mv -f -- "$target" "$bak"
        MOVED+=("$entry → $(basename "$bak")")
    fi
done < <(tar -tzf "$ARCHIVE")

tar -C "$TARGET_DIR" -xzf "$ARCHIVE"

ENTRIES=$(tar -tzf "$ARCHIVE" 2>/dev/null | grep -vE '/$' | wc -l | tr -d ' ')
echo "Config restore complete from $ARCHIVE ($ENTRIES file(s)) into $TARGET_DIR"
if [ "${#MOVED[@]}" -gt 0 ]; then
    echo "Moved aside ${#MOVED[@]} existing file(s) to *.${STAMP}.bak:"
    for line in "${MOVED[@]}"; do
        echo "  - $line"
    done
    echo "Review and delete the .bak files once the restored config is confirmed good."
fi
