#!/bin/sh
set -e

if [ "${SKIP_DB_MIGRATIONS}" != "1" ]; then
  python manage.py ensure_membership_initial_migration
  python manage.py migrate --noinput
fi

exec "$@"
