"""Daily PostgreSQL backup Celery task.

Runs at 02:00 UTC via Celery Beat, dumps the OMS database with
``pg_dump -Fc`` (custom-format archive — supports parallel restore
and is what ``scripts/restore-db.sh`` expects), writes it to a named
volume mounted on the celery container, and prunes anything older
than ``OMS_BACKUP_RETENTION_DAYS``.

Pairs with the existing host-side scripts under ``scripts/`` which
remain the operator-driven path:

  - ``scripts/backup-db.sh``  — ad-hoc dump from the host
  - ``scripts/restore-db.sh`` — restore from a `-Fc` dump
  - ``scripts/restore-drill.sh`` — backup→restore→smoke round-trip

The Celery task is the *scheduled* path: every 24h the backup
happens whether anyone remembers to run the script or not, and the
Sentry cron monitor pages if it doesn't.

R-03 mitigation surface: combined with the existing restore drill,
this gives both halves of the backup story — verified writes
(scheduled task) and verified restores (CI + scripts).
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess  # nosec B404 — invoked with an argv list (no shell) below
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List
from urllib.parse import urlparse

from django.conf import settings

import sentry_sdk
from celery import shared_task

logger = logging.getLogger(__name__)


def _backup_dir() -> Path:
    """Resolve the on-disk backup destination from settings."""
    return Path(getattr(settings, "OMS_BACKUP_DIR", "/var/backups/oms"))


def _retention_days() -> int:
    return int(getattr(settings, "OMS_BACKUP_RETENTION_DAYS", 14))


def _database_url() -> str:
    """Return the libpq connection URL for the default DB.

    We pull it from `DATABASE_URL` rather than reconstructing it from
    Django's parsed `DATABASES['default']` so the credentials carry
    through unchanged (esp. URL-encoded special chars in the password
    that Django's settings parser may have decoded).
    """
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set in the worker environment; daily backup cannot run"
        )
    return url


def _is_postgres(url: str) -> bool:
    return urlparse(url).scheme.startswith("postgres")


def _resolve_dump_path() -> Path:
    """Pick a per-day filename so a re-run on the same day overwrites
    rather than accumulating duplicate dumps in the retention window."""
    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    return _backup_dir() / f"db-{today}.dump"


def _prune_old_backups(now: datetime | None = None) -> List[str]:
    """Delete `.dump` files older than the retention window.

    Returns the list of deleted file basenames so the caller can
    surface the count to operators via Sentry Logs.
    """
    now = now or datetime.now(tz=timezone.utc)
    cutoff = now - timedelta(days=_retention_days())
    deleted: list[str] = []
    backup_dir = _backup_dir()
    if not backup_dir.exists():
        return deleted
    for entry in backup_dir.glob("db-*.dump"):
        try:
            mtime = datetime.fromtimestamp(entry.stat().st_mtime, tz=timezone.utc)
        except OSError:
            continue
        if mtime < cutoff:
            try:
                entry.unlink()
                deleted.append(entry.name)
            except OSError as exc:
                logger.warning("Could not delete old backup %s: %s", entry, exc)
    return deleted


def _run_pg_dump(database_url: str, dump_path: Path) -> int:
    """Spawn `pg_dump`, return the bytes written.

    Raises ``subprocess.CalledProcessError`` on non-zero exit so the
    Celery task surfaces failure to the cron monitor.
    """
    dump_path.parent.mkdir(parents=True, exist_ok=True)
    if shutil.which("pg_dump") is None:
        raise RuntimeError(
            "pg_dump binary not found in PATH; the worker image must include postgresql-client"
        )
    # `-Fc` = PostgreSQL custom-format archive (matches restore-db.sh).
    # `--no-owner` / `--no-acl` keep the dump portable across roles —
    # the restore path uses the target DB's owner not the source's.
    cmd = [
        "pg_dump",
        "-Fc",
        "--no-owner",
        "--no-acl",
        "-d",
        database_url,
        "-f",
        str(dump_path),
    ]
    started = time.monotonic()
    # Fixed argv list, no shell, only operator-controlled inputs from
    # the env var DATABASE_URL — safe from injection.
    subprocess.run(cmd, check=True, capture_output=True)  # nosec B603
    elapsed = time.monotonic() - started
    logger.info(
        "daily_postgres_backup: wrote %s in %.1fs",
        dump_path,
        elapsed,
    )
    return dump_path.stat().st_size


@shared_task(bind=True, ignore_result=True, name="backups.daily_postgres_backup")
@sentry_sdk.crons.monitor(
    monitor_slug="backups-daily-postgres",
    monitor_config={
        # 02:00 UTC daily. Window matches the Beat schedule below; if
        # the cron doesn't check in for >65min Sentry pages.
        "schedule": {"type": "crontab", "value": "0 2 * * *"},
        "timezone": "UTC",
        "checkin_margin": 5,
        "max_runtime": 30,
        # A missed daily backup is paging-worthy — the operator can
        # rerun scripts/backup-db.sh by hand, but we want the alert.
        "failure_issue_threshold": 1,
        "recovery_threshold": 1,
    },
)
def daily_postgres_backup(self):
    """Take a pg_dump archive of the OMS DB and prune old backups."""
    url = _database_url()
    if not _is_postgres(url):
        # SQLite dev runs / test runners hit this path. Log and exit
        # cleanly so the cron monitor stays green in environments that
        # legitimately don't need a Postgres backup.
        logger.info(
            "daily_postgres_backup: DATABASE_URL is not Postgres (%s); skipping",
            urlparse(url).scheme,
        )
        return {"skipped": True, "reason": "non-postgres database"}

    dump_path = _resolve_dump_path()
    size_bytes = _run_pg_dump(url, dump_path)
    pruned = _prune_old_backups()

    sentry_sdk.logger.info(
        "oms.backup.daily_postgres",
        attributes={
            "value": size_bytes,
            "metric.name": "oms.backup.daily_postgres.size_bytes",
            "metric.kind": "gauge",
            "backup.path": str(dump_path),
            "backup.pruned_count": len(pruned),
            "backup.retention_days": _retention_days(),
        },
    )
    return {
        "skipped": False,
        "path": str(dump_path),
        "size_bytes": size_bytes,
        "pruned": pruned,
    }
