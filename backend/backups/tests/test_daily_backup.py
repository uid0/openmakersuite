"""Tests for the daily Postgres backup Celery task."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from backups.tasks import (
    _is_postgres,
    _prune_old_backups,
    _resolve_dump_path,
    _run_pg_dump,
    daily_postgres_backup,
)

pytestmark = pytest.mark.unit


class TestIsPostgres:
    def test_postgres_url_recognised(self):
        assert _is_postgres("postgres://u:p@h/db") is True
        assert _is_postgres("postgresql://u:p@h/db") is True

    def test_sqlite_url_not_postgres(self):
        assert _is_postgres("sqlite:///tmp/db.sqlite3") is False


class TestDumpPath:
    def test_filename_per_day(self, tmp_path, settings):
        settings.OMS_BACKUP_DIR = str(tmp_path)
        path = _resolve_dump_path()
        assert path.parent == tmp_path
        # Filename embeds the UTC date so a re-run same-day overwrites.
        today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
        assert path.name == f"db-{today}.dump"


class TestPruneOldBackups:
    def test_prunes_outside_retention(self, tmp_path, settings):
        settings.OMS_BACKUP_DIR = str(tmp_path)
        settings.OMS_BACKUP_RETENTION_DAYS = 7
        now = datetime.now(tz=timezone.utc)
        old = tmp_path / "db-old.dump"
        old.write_bytes(b"ancient backup")
        # Force the file's mtime back beyond the retention horizon.
        old_ts = (now - timedelta(days=14)).timestamp()
        os.utime(old, (old_ts, old_ts))
        fresh = tmp_path / "db-fresh.dump"
        fresh.write_bytes(b"recent backup")

        deleted = _prune_old_backups(now=now)
        assert deleted == ["db-old.dump"]
        assert not old.exists()
        assert fresh.exists()

    def test_no_backup_dir_returns_empty(self, tmp_path, settings):
        # Point at a path that doesn't exist yet — the prune step
        # should not crash when the volume is first mounted.
        settings.OMS_BACKUP_DIR = str(tmp_path / "missing")
        assert _prune_old_backups() == []


class TestRunPgDump:
    @patch("backups.tasks.subprocess.run")
    @patch("backups.tasks.shutil.which", return_value="/usr/bin/pg_dump")
    def test_invokes_pg_dump_with_custom_format(self, mock_which, mock_run, tmp_path):
        dump_path = tmp_path / "db-2026-05-29.dump"
        dump_path.write_bytes(b"x" * 42)  # simulate the dump side effect
        size = _run_pg_dump("postgresql://u:p@h/db", dump_path)
        assert size == 42
        mock_run.assert_called_once()
        cmd = mock_run.call_args.args[0]
        assert cmd[0] == "pg_dump"
        assert "-Fc" in cmd
        assert "--no-owner" in cmd
        assert "--no-acl" in cmd
        assert "-d" in cmd
        assert "postgresql://u:p@h/db" in cmd
        assert "-f" in cmd
        assert str(dump_path) in cmd

    @patch("backups.tasks.shutil.which", return_value=None)
    def test_missing_pg_dump_raises(self, mock_which, tmp_path):
        with pytest.raises(RuntimeError, match="pg_dump binary not found"):
            _run_pg_dump("postgresql://u:p@h/db", tmp_path / "out.dump")


class TestDailyPostgresBackup:
    """End-to-end task with subprocess mocked. The task is decorated
    with @shared_task + @crons.monitor — invoke the underlying
    callable via `.run` (Celery binds tasks to a Task instance which
    exposes the original via `.run`)."""

    @patch.dict(os.environ, {"DATABASE_URL": "sqlite:///tmp/dev.sqlite3"}, clear=False)
    def test_non_postgres_short_circuits(self):
        # The dev/test environment is on SQLite; the task should
        # log + skip rather than fail.
        result = daily_postgres_backup.run.__wrapped__(MagicMock())
        assert result["skipped"] is True

    @patch("backups.tasks._run_pg_dump")
    @patch("backups.tasks._prune_old_backups")
    @patch.dict(os.environ, {"DATABASE_URL": "postgresql://u:p@db/oms"}, clear=False)
    def test_postgres_path_calls_pg_dump_and_prune(
        self, mock_prune, mock_run_dump, tmp_path, settings
    ):
        settings.OMS_BACKUP_DIR = str(tmp_path)
        mock_run_dump.return_value = 12345
        mock_prune.return_value = ["db-old.dump"]
        result = daily_postgres_backup.run.__wrapped__(MagicMock())
        assert result["skipped"] is False
        assert result["size_bytes"] == 12345
        assert result["pruned"] == ["db-old.dump"]
        assert Path(result["path"]).parent == tmp_path
        mock_run_dump.assert_called_once()
        mock_prune.assert_called_once()

    @patch.dict(os.environ, {}, clear=True)
    def test_missing_database_url_raises(self):
        with pytest.raises(RuntimeError, match="DATABASE_URL is not set"):
            daily_postgres_backup.run.__wrapped__(MagicMock())
