"""Deployment-artifact tests for AC-23 (env validation), AC-24 (no operator-
specific defaults), and AC-36 (CI validates deployment artifacts).

Each test runs the shipped scripts/files against the repo as-is, so a
regression in the validator, the example env, or the deploy compose file
fails CI before merge.
"""

import os
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest


def _find_repo_root():
    """Walk up from this file until we hit a directory that looks like the
    OpenMakerSuite repo root (has both `.criteria/` and `docker-compose.prod.yml`).

    The CI `backend-tests` job runs from a full repo checkout where
    parents[3] is the repo root. The CI `docker-build` job runs pytest
    inside the backend container where backend/ is mounted at `/app`, so
    parents[3] would be `/` and the deploy artifacts are not on disk at
    all. In that case return None so the deploy-artifact tests skip
    cleanly instead of FileNotFoundError-ing on absolute paths.
    """
    for p in Path(__file__).resolve().parents:
        if (p / ".criteria").is_dir() and (p / "docker-compose.prod.yml").is_file():
            return p
    return None


REPO_ROOT = _find_repo_root()
VALIDATOR = REPO_ROOT / "scripts" / "validate-prod-env.sh" if REPO_ROOT else None
pytestmark = pytest.mark.skipif(
    REPO_ROOT is None,
    reason="deploy artifacts not present (running inside backend container)",
)


def _write_env(tmp_path, overrides=None, base="prod"):
    """Render an env file from a known-good baseline + overrides.

    `base="prod"` returns a fully-valid prod env; tests then override one key
    to exercise a single failure mode.
    """
    good = {
        "DOMAIN": "oms.example.com",
        "LETSENCRYPT_EMAIL": "admin@oms.example.com",
        "LETSENCRYPT_DOMAINS": "oms.example.com www.oms.example.com",
        "DEBUG": "0",
        "SECRET_KEY": "x" * 64,
        "ALLOWED_HOSTS": "oms.example.com,www.oms.example.com",
        "CSRF_TRUSTED_ORIGINS": "https://oms.example.com,https://www.oms.example.com",
        "CORS_ALLOWED_ORIGINS": "https://oms.example.com,https://www.oms.example.com",
        "POSTGRES_DB": "oms",
        "POSTGRES_USER": "oms",
        "POSTGRES_PASSWORD": "S3curePass1234567890",
        "REDIS_URL": "redis://redis:6379/0",
        "CELERY_BROKER_URL": "redis://redis:6379/0",
        "FRONTEND_URL": "https://oms.example.com",
        "EMQX_DASHBOARD_PASSWORD": "Strong1Password",
        "EMAIL_BACKEND": "django.core.mail.backends.smtp.EmailBackend",
        "EMAIL_HOST": "smtp.example.com",
        "EMAIL_HOST_USER": "user",
        "EMAIL_HOST_PASSWORD": "pass",
        "DEFAULT_FROM_EMAIL": "noreply@oms.example.com",
        "POSTMARK_INBOUND_TOKEN": "abcd1234efgh5678",
        "LOCATION_PING_TOKEN": "ijkl9012mnop3456",
    }
    if base == "empty":
        good = {}
    if overrides:
        good = {**good, **overrides}

    path = tmp_path / ".env"
    path.write_text("\n".join(f"{k}={v}" for k, v in good.items()) + "\n")
    return path


def _run_validator(env_path):
    return subprocess.run(
        ["bash", str(VALIDATOR), str(env_path)],
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
class TestProductionEnvValidatorAC23:
    """AC-23: Production env validation rejects unsafe defaults."""

    def test_validator_passes_on_well_formed_env(self, tmp_path):
        env = _write_env(tmp_path)
        result = _run_validator(env)
        assert result.returncode == 0, result.stdout + result.stderr

    def test_missing_env_file_fails(self, tmp_path):
        result = _run_validator(tmp_path / "no-such.env")
        assert result.returncode == 1
        assert "not found" in result.stdout

    @pytest.mark.parametrize(
        "key,value,fragment",
        [
            ("DEBUG", "1", "DEBUG"),
            ("DEBUG", "true", "DEBUG"),
            ("SECRET_KEY", "your-very-secret-production-key-here-change-this", "SECRET_KEY"),
            ("SECRET_KEY", "short", "SECRET_KEY"),
            ("ALLOWED_HOSTS", "localhost", "ALLOWED_HOSTS"),
            ("ALLOWED_HOSTS", "localhost,127.0.0.1", "ALLOWED_HOSTS"),
            ("CSRF_TRUSTED_ORIGINS", "http://oms.example.com", "CSRF_TRUSTED_ORIGINS"),
            ("CORS_ALLOWED_ORIGINS", "http://oms.example.com", "CORS_ALLOWED_ORIGINS"),
            ("POSTGRES_PASSWORD", "your-strong-database-password-here", "POSTGRES_PASSWORD"),
            ("POSTGRES_PASSWORD", "short", "POSTGRES_PASSWORD"),
            ("EMQX_DASHBOARD_PASSWORD", "change-me-on-first-deploy", "EMQX_DASHBOARD_PASSWORD"),
            ("EMQX_DASHBOARD_PASSWORD", "alllowercase1", "uppercase"),
            ("EMQX_DASHBOARD_PASSWORD", "ALLUPPERCASE1", "lowercase"),
            ("EMQX_DASHBOARD_PASSWORD", "NoDigitsHere", "digit"),
            ("EMQX_DASHBOARD_PASSWORD", "Short1", "8 characters"),
            ("DOMAIN", "change-me", "DOMAIN"),
            ("LETSENCRYPT_EMAIL", "", "LETSENCRYPT_EMAIL"),
            ("HIGHLIGHT_OTLP_ENDPOINT", "http://otel.example.org:4317", "HIGHLIGHT_OTLP_ENDPOINT"),
            ("FORGEKEY_FIRMWARE_SIGNING_KEY", "not-a-pem", "FORGEKEY_FIRMWARE_SIGNING_KEY"),
        ],
    )
    def test_unsafe_value_rejected(self, tmp_path, key, value, fragment):
        env = _write_env(tmp_path, overrides={key: value})
        result = _run_validator(env)
        assert result.returncode == 1, f"expected fail for {key}={value!r}"
        assert fragment in result.stdout

    def test_postmark_backend_requires_token(self, tmp_path):
        env = _write_env(
            tmp_path,
            overrides={
                "EMAIL_BACKEND": "anymail.backends.postmark.EmailBackend",
                "POSTMARK_SERVER_TOKEN": "",
            },
        )
        result = _run_validator(env)
        assert result.returncode == 1
        assert "POSTMARK_SERVER_TOKEN" in result.stdout

    def test_warnings_do_not_fail_deploy(self, tmp_path):
        # Empty webhook tokens warn but don't error.
        env = _write_env(
            tmp_path,
            overrides={"POSTMARK_INBOUND_TOKEN": "", "LOCATION_PING_TOKEN": ""},
        )
        result = _run_validator(env)
        assert result.returncode == 0, result.stdout
        assert "Warnings" in result.stdout


class TestNoOperatorSpecificDefaultsAC24:
    """AC-24: Production examples and deployment files must not hardcode
    Dallas/operator-specific values.
    """

    DALLAS_PATTERNS = [
        # Dallas-specific hostnames + addresses.
        re.compile(r"dallas\.openmakersuite\.net", re.IGNORECASE),
        re.compile(r"dallasmakerspace\.org", re.IGNORECASE),
        # Dallas-area Waze coordinates.
        re.compile(r"lat=32\.94"),
        re.compile(r"lon=-?96\.91"),
    ]

    PROD_FILES = [
        ".env.prod.example",
        "docker-compose.prod.yml",
        "deploy.sh",
        "nginx/docker-entrypoint.d/10-letsencrypt.sh",
    ]

    @pytest.mark.parametrize("rel", PROD_FILES)
    def test_no_dallas_specific_strings(self, rel):
        path = REPO_ROOT / rel
        text = path.read_text()
        for pattern in self.DALLAS_PATTERNS:
            assert not pattern.search(text), (
                f"{rel} contains operator-specific value {pattern.pattern!r}; "
                "use a neutral example.com / empty / env-driven default."
            )

    def test_settings_defaults_are_neutral(self):
        """LOGISTICS_ALERT_EMAIL and TRAFFIC_URL must default to empty (the
        documented "disabled" state) rather than a Dallas Makerspace value."""
        from django.conf import settings as dj

        # The defaults are read from env at import time. Just check the
        # source for the literal default strings — env may override them in
        # the test process.
        src = (REPO_ROOT / "backend" / "config" / "settings.py").read_text()
        assert 'default="logistics@dallasmakerspace.org"' not in src
        assert "lat=32.94" not in src
        assert "lon=-96.91" not in src
        # Also confirm runtime settings aren't accidentally Dallas-specific
        # if env happens to be unset.
        if not os.environ.get("LOGISTICS_ALERT_EMAIL"):
            assert dj.LOGISTICS_ALERT_EMAIL == ""
        if not os.environ.get("TRAFFIC_URL"):
            assert dj.TRAFFIC_URL == ""


class TestDeploymentArtifactsAC36:
    """AC-36: CI validates deployment artifacts (subset that runs without
    docker/helm — those run as separate CI jobs)."""

    @pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
    @pytest.mark.parametrize(
        "rel",
        [
            "scripts/validate-prod-env.sh",
            "scripts/backup-db.sh",
            "scripts/restore-db.sh",
            "scripts/check-env.sh",
            "deploy.sh",
        ],
    )
    def test_shell_scripts_have_valid_syntax(self, rel):
        result = subprocess.run(
            ["bash", "-n", str(REPO_ROOT / rel)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr

    def test_env_example_covers_validator_required_keys(self):
        """Every key the validator marks fatal-if-missing must exist in
        .env.prod.example so operators have a slot to fill in."""
        text = (REPO_ROOT / ".env.prod.example").read_text()
        keys = {
            line.split("=", 1)[0].strip()
            for line in text.splitlines()
            if line and not line.startswith("#") and "=" in line
        }
        required = {
            "DOMAIN",
            "LETSENCRYPT_EMAIL",
            "LETSENCRYPT_DOMAINS",
            "DEBUG",
            "SECRET_KEY",
            "ALLOWED_HOSTS",
            "CSRF_TRUSTED_ORIGINS",
            "CORS_ALLOWED_ORIGINS",
            "POSTGRES_DB",
            "POSTGRES_USER",
            "POSTGRES_PASSWORD",
            "REDIS_URL",
            "CELERY_BROKER_URL",
            "EMQX_DASHBOARD_PASSWORD",
            "EMAIL_BACKEND",
            "DEFAULT_FROM_EMAIL",
        }
        missing = required - keys
        assert not missing, f".env.prod.example is missing required keys: {sorted(missing)}"

    def test_deploy_runbooks_present(self):
        """AC-33 requires the runbooks to exist; CI fails if any go missing."""
        for rel in [
            "deploy/COMPOSE_RUNBOOK.md",
            "deploy/SMOKE_TESTS.md",
            "deploy/BACKUP_RESTORE.md",
            "deploy/UPGRADE_ROLLBACK.md",
            "deploy/PREREQUISITES.md",
            "deploy/NETWORK_EXPOSURE.md",
        ]:
            assert (REPO_ROOT / rel).is_file(), f"missing required deploy doc: {rel}"


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
def test_validator_handles_quoted_values(tmp_path):
    """Values quoted with single or double quotes (common when copied from
    secret stores) must be parsed correctly without leaking the quotes into
    length/comparison checks."""
    env = tmp_path / ".env"
    body = textwrap.dedent("""\
        DOMAIN="oms.example.com"
        LETSENCRYPT_EMAIL='admin@oms.example.com'
        LETSENCRYPT_DOMAINS=oms.example.com
        DEBUG=0
        SECRET_KEY="xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
        ALLOWED_HOSTS=oms.example.com
        CSRF_TRUSTED_ORIGINS=https://oms.example.com
        CORS_ALLOWED_ORIGINS=https://oms.example.com
        POSTGRES_DB=oms
        POSTGRES_USER=oms
        POSTGRES_PASSWORD='S3curePassWithQuotes!'
        REDIS_URL=redis://redis:6379/0
        CELERY_BROKER_URL=redis://redis:6379/0
        EMQX_DASHBOARD_PASSWORD=Strong1Password
        EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
        EMAIL_HOST=smtp.example.com
        DEFAULT_FROM_EMAIL=noreply@oms.example.com
        POSTMARK_INBOUND_TOKEN=tok
        LOCATION_PING_TOKEN=tok
        """)
    env.write_text(body)
    result = _run_validator(env)
    assert result.returncode == 0, result.stdout
