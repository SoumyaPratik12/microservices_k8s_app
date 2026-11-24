#!/bin/sh
set -eu

if [ "$#" -ne 1 ]; then
    printf 'Usage: %s <backup-file>\n' "$0" >&2
    exit 2
fi

: "${PGHOST:?Set PGHOST}"
: "${PGDATABASE:?Set PGDATABASE}"
: "${PGUSER:?Set PGUSER}"
: "${PGPASSWORD:?Set PGPASSWORD through a protected environment, never a command argument}"
: "${RESTORE_CONFIRM_DATABASE:?Set RESTORE_CONFIRM_DATABASE to the exact target database name}"

backup_file=$1
if [ "$RESTORE_CONFIRM_DATABASE" != "$PGDATABASE" ]; then
    printf 'Restore refused: confirmation does not match the target database.\n' >&2
    exit 1
fi
if [ ! -r "$backup_file" ] || [ ! -r "${backup_file}.sha256" ]; then
    printf 'Restore refused: backup or checksum file is missing.\n' >&2
    exit 1
fi

(cd "$(dirname "$backup_file")" && sha256sum --check "$(basename "${backup_file}.sha256")")
pg_restore --exit-on-error --clean --if-exists --no-owner --dbname="$PGDATABASE" "$backup_file"
printf 'Restore completed to the explicitly confirmed database: %s\n' "$PGDATABASE"