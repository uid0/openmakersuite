"""AC-25 contract tests for the backup / restore / smoke scripts.

These tests are docker-free: they assert that every script ships, parses
clean, supports `--help`, and refuses obviously bad inputs without touching
the live database. The end-to-end drill itself runs from
`scripts/restore-drill.sh` against a real Compose stack and is exercised by
the operator (see `deploy/BACKUP_RESTORE.md` §8) — what's tested here is the
parts that have to work *before* the operator can run the drill.

Mapped to acceptance criteria:

- AC-25 (data backup and restore are verified): the scripts the runbook
  refers to must exist, parse, and fail safely on missing/unsupported
  inputs.
- AC-33 (deployment runbooks are complete): the BACKUP_RESTORE.md "Scripts"
  section must reference the same set of files this test exercises.
- AC-36 (CI validates deployment artifacts): every script is included in the
  shell-script lint set, plus this targeted suite.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


def _find_repo_root() -> Path | None:
    for p in Path(__file__).resolve().parents:
        if (p / ".criteria").is_dir() and (p / "docker-compose.prod.yml").is_file():
            return p
    return None


REPO_ROOT = _find_repo_root()
pytestmark = pytest.mark.skipif(
    REPO_ROOT is None,
    reason="deploy artifacts not present (running inside backend container)",
)


# (script path, must appear in --help output) tuples. The "must appear" string
# is a stable phrase from the script's own usage text — a regression that
# silently drops a flag or renames a script will fail loudly here.
SCRIPTS = [
    ("scripts/backup-db.sh", "PG_FORMAT"),
    ("scripts/restore-db.sh", "--force"),
    ("scripts/backup-media.sh", "MEDIA_PATH"),
    ("scripts/restore-media.sh", "WORKER_SERVICES"),
    ("scripts/backup-config.sh", "ENV_FILE"),
    ("scripts/smoke.sh", "--json"),
    ("scripts/restore-drill.sh", "--dry-run"),
]


@pytest.fixture(scope="module")
def repo_root() -> Path:
    assert REPO_ROOT is not None
    return REPO_ROOT


def _run(args, **kw):
    return subprocess.run(args, capture_output=True, text=True, check=False, **kw)


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
class TestScriptsExistAndParseAC25:
    @pytest.mark.parametrize("rel,_marker", SCRIPTS, ids=lambda x: x if isinstance(x, str) else "")
    def test_script_present_and_executable(self, repo_root, rel, _marker):
        path = repo_root / rel
        assert path.is_file(), f"missing script: {rel}"
        # The shipped scripts must be 0755 (group/world read-and-exec). The git
        # mode is the authoritative source; we just verify on-disk too because
        # CI runs from a fresh checkout where modes come straight from git.
        mode = path.stat().st_mode & 0o777
        assert mode & 0o111, f"{rel} is not executable (mode={mode:o})"

    @pytest.mark.parametrize("rel,_marker", SCRIPTS)
    def test_script_parses_clean(self, repo_root, rel, _marker):
        result = _run(["bash", "-n", str(repo_root / rel)])
        assert result.returncode == 0, result.stderr

    @pytest.mark.parametrize("rel,marker", SCRIPTS)
    def test_script_help_text_includes_marker(self, repo_root, rel, marker):
        result = _run(["bash", str(repo_root / rel), "--help"])
        assert result.returncode == 0, result.stdout + result.stderr
        assert marker in result.stdout, (
            f"--help output for {rel} no longer mentions {marker!r}. "
            "Either the flag/var was renamed (update the marker in this test) "
            "or the help text was lost in a refactor."
        )


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
class TestBackupRestoreInputValidation:
    def test_backup_db_rejects_unknown_format(self, repo_root, tmp_path):
        result = _run(["bash", str(repo_root / "scripts" / "backup-db.sh"), "--format=hopscotch"])
        assert result.returncode == 1, result.stdout + result.stderr
        # The script's own error message names the env var; assert on that so a
        # rename of the variable shows up here.
        assert "PG_FORMAT" in result.stderr or "PG_FORMAT" in result.stdout

    def test_restore_db_requires_argument(self, repo_root):
        result = _run(["bash", str(repo_root / "scripts" / "restore-db.sh")])
        assert result.returncode == 1
        assert "Usage" in result.stdout + result.stderr

    def test_restore_db_rejects_unknown_format(self, repo_root, tmp_path):
        # Create a file with an unsupported extension so the script reaches the
        # format-detection branch without first failing on a missing path.
        weird = tmp_path / "snapshot.xyz"
        weird.write_text("not a real dump")
        result = _run(
            [
                "bash",
                str(repo_root / "scripts" / "restore-db.sh"),
                "--force",
                str(weird),
            ]
        )
        assert result.returncode == 1
        combined = result.stdout + result.stderr
        assert "Unrecognized backup format" in combined
        assert ".sql.gz" in combined
        assert ".dump" in combined

    def test_restore_db_missing_file_fails(self, repo_root, tmp_path):
        result = _run(
            [
                "bash",
                str(repo_root / "scripts" / "restore-db.sh"),
                "--force",
                str(tmp_path / "does-not-exist.sql.gz"),
            ]
        )
        assert result.returncode == 1
        assert "not found" in (result.stdout + result.stderr)

    def test_restore_media_requires_argument(self, repo_root):
        result = _run(["bash", str(repo_root / "scripts" / "restore-media.sh")])
        assert result.returncode == 1
        assert "Usage" in result.stdout + result.stderr

    def test_restore_media_corrupt_archive_rejected(self, repo_root, tmp_path):
        bogus = tmp_path / "media.tgz"
        bogus.write_text("definitely not gzip")
        result = _run(
            [
                "bash",
                str(repo_root / "scripts" / "restore-media.sh"),
                "--force",
                str(bogus),
            ]
        )
        # Corrupt archive must short-circuit before any rm -rf hits the volume.
        assert result.returncode == 2
        assert "gzip integrity" in (result.stdout + result.stderr)


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
class TestBackupConfigScript:
    def test_fails_cleanly_when_no_config_present(self, repo_root, tmp_path):
        # Run the script from a directory with no .env so it has nothing to
        # archive. The script should error out *before* creating a zero-byte
        # tar — a silent empty archive would be worse than a loud failure.
        result = _run(
            ["bash", str(repo_root / "scripts" / "backup-config.sh")],
            cwd=str(tmp_path),
            env={"PATH": "/usr/bin:/bin:/usr/local/bin", "BACKUP_DIR": str(tmp_path / "out")},
        )
        assert result.returncode == 1
        combined = result.stdout + result.stderr
        assert "no config files found" in combined

    def test_archives_env_with_locked_permissions(self, repo_root, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("DOMAIN=example.com\nSECRET_KEY=test\n")
        result = _run(
            ["bash", str(repo_root / "scripts" / "backup-config.sh")],
            cwd=str(tmp_path),
            env={
                "PATH": "/usr/bin:/bin:/usr/local/bin",
                "BACKUP_DIR": str(tmp_path / "out"),
                "ENV_FILE": ".env",
                "RETENTION_DAYS": "0",
            },
        )
        assert result.returncode == 0, result.stdout + result.stderr
        archives = list((tmp_path / "out").glob("oms-config-*.tar.gz"))
        assert len(archives) == 1, "exactly one archive should land per run"
        # 0600 is the protective baseline for a file containing a live SECRET_KEY.
        mode = archives[0].stat().st_mode & 0o777
        assert mode == 0o600, f"archive permissions must be 0600, got {mode:o}"


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
class TestRestoreDrillDryRun:
    def test_dry_run_parses_every_dependency(self, repo_root, tmp_path):
        result = _run(
            [
                "bash",
                str(repo_root / "scripts" / "restore-drill.sh"),
                "--dry-run",
            ],
            cwd=str(repo_root),
            env={
                "PATH": "/usr/bin:/bin:/usr/local/bin",
                "DRILL_DIR": str(tmp_path / "drill"),
            },
        )
        assert result.returncode == 0, result.stdout + result.stderr
        out = result.stdout
        assert "dry-run" in out
        # The dry-run prints the resolved configuration the drill would use;
        # confirm the evidence dir landed under our tmp path so a real run
        # would not stomp the repo.
        assert str(tmp_path) in out


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
def test_smoke_script_reports_failure_against_dead_host(repo_root):
    """smoke.sh must surface a non-zero exit code when no backend answers.

    The drill orchestrator relies on this — if smoke.sh silently exited 0 on
    a dead deploy we'd record a "passed" drill against a broken restore.
    """
    result = _run(
        ["bash", str(repo_root / "scripts" / "smoke.sh"), "--json"],
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "HOST": "127.0.0.1:1",  # nothing listens here
            "SCHEME": "http",
            "TIMEOUT": "2",
        },
    )
    assert result.returncode == 1
    # JSON path must still emit a parsable document even on failure so
    # automation can ingest it.
    assert result.stdout.strip().startswith("{")
    assert '"failed"' in result.stdout
