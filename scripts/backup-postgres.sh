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

backup_file=$1
umask 077
mkdir -p "$(dirname "$backup_file")"
temporary_file="${backup_file}.tmp.$$"
trap 'rm -f -- "$temporary_file"' EXIT HUP INT TERM
pg_dump --format=custom --no-owner --file="$temporary_file"
mv -- "$temporary_file" "$backup_file"
sha256sum "$backup_file" > "${backup_file}.sha256"
printf 'Created and checksummed PostgreSQL backup: %s\n' "$backup_file"