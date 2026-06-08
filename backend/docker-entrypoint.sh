#!/bin/sh
set -e

if [ "${SKIP_DB_MIGRATIONS}" != "1" ]; then
  python manage.py ensure_membership_initial_migration
  python manage.py migrate --noinput
fi

# Production safety baseline (gh-710 of #455). Validates the LIVE
# Django settings against the registered category checks before the
# application starts serving requests. In dev (DEBUG=True) this is a
# no-op; in prod a placeholder SECRET_KEY, wildcard ALLOWED_HOSTS,
# etc. will fail the container before it can accept traffic. Set
# SKIP_PRODUCTION_VALIDATION=1 for one-off containers (e.g. a CLI
# shell) where settings hardening doesn't apply.
if [ "${SKIP_PRODUCTION_VALIDATION}" != "1" ]; then
  python manage.py validate_production --quiet
fi

exec "$@"
