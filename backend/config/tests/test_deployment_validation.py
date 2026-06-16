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
            ("FORGEKEY_FIRMWARE_SIGNING_KEY", "not-a-pem", "FORGEKEY_FIRMWARE_SIGNING_KEY"),
            ("SENTRY_DSN", "http://abc@o0.ingest.sentry.io/123", "SENTRY_DSN"),
            ("REACT_APP_SENTRY_DSN", "http://abc@o0.ingest.sentry.io/123", "REACT_APP_SENTRY_DSN"),
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

    @pytest.mark.parametrize(
        "key",
        [
            "SENTRY_DSN",
            "REACT_APP_SENTRY_DSN",
        ],
    )
    def test_sentry_https_value_allowed(self, tmp_path, key):
        env = _write_env(
            tmp_path,
            overrides={key: "https://abc@o0.ingest.sentry.io/123"},
        )
        result = _run_validator(env)
        assert result.returncode == 0, result.stdout + result.stderr

    @pytest.mark.parametrize(
        "key",
        [
            "SENTRY_DSN",
            "REACT_APP_SENTRY_DSN",
        ],
    )
    def test_empty_sentry_value_allowed(self, tmp_path, key):
        env = _write_env(tmp_path, overrides={key: ""})
        result = _run_validator(env)
        assert result.returncode == 0, result.stdout + result.stderr

    def test_observability_warning_when_sentry_unset(self, tmp_path):
        env = _write_env(tmp_path, overrides={"SENTRY_DSN": ""})
        result = _run_validator(env)
        assert result.returncode == 0, result.stdout
        assert "SENTRY_DSN" in result.stdout

    def test_observability_warning_absent_when_sentry_present(self, tmp_path):
        env = _write_env(
            tmp_path,
            overrides={"SENTRY_DSN": "https://abc@o0.ingest.sentry.io/123"},
        )
        result = _run_validator(env)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "no error reporting configured" not in result.stdout.lower()

    @pytest.mark.parametrize("value", ["False", "false", "0", "off", "no", ""])
    @pytest.mark.parametrize("key", ["SESSION_COOKIE_SECURE", "CSRF_COOKIE_SECURE"])
    def test_cookie_secure_falsy_override_warns(self, tmp_path, key, value):
        env = _write_env(tmp_path, overrides={key: value})
        result = _run_validator(env)
        assert result.returncode == 0, result.stdout
        assert key in result.stdout

    @pytest.mark.parametrize("key", ["SESSION_COOKIE_SECURE", "CSRF_COOKIE_SECURE"])
    def test_cookie_secure_unset_has_no_warning(self, tmp_path, key):
        env = _write_env(tmp_path)
        result = _run_validator(env)
        assert result.returncode == 0, result.stdout + result.stderr
        assert key not in result.stdout


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
            "scripts/backup-media.sh",
            "scripts/restore-media.sh",
            "scripts/backup-config.sh",
            "scripts/smoke.sh",
            "scripts/restore-drill.sh",
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

    def test_prod_compose_bundles_mqtt_consumer(self):
        """The mqtt_consumer service must be present in
        docker-compose.prod.yml so device telemetry actually reaches the
        Django process. Without it, ``ESP32Device.last_seen`` stalls at
        the last register/photo-upload time and command ACKs never
        resolve. The failure mode that motivated this check.
        """
        text = (REPO_ROOT / "docker-compose.prod.yml").read_text()
        assert re.search(r"^\s{2}mqtt_consumer:", text, re.MULTILINE), (
            "docker-compose.prod.yml must define an `mqtt_consumer` service. "
            "If you renamed it, update this test and "
            "deploy/COMPOSE_RUNBOOK.md to match."
        )
        assert (
            "python manage.py mqtt_consumer" in text
        ), "mqtt_consumer service command must invoke `python manage.py mqtt_consumer`."

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

    def test_prod_compose_bundles_celery_beat(self):
        """AC-29/AC-32: docker-compose.prod.yml must define a celery_beat
        service so CELERY_BEAT_SCHEDULE entries actually fire in production.

        Without this, the worker is up but no scheduled task ever ticks —
        the failure mode that motivated github #335 (oms-iuw).
        """
        text = (REPO_ROOT / "docker-compose.prod.yml").read_text()
        assert re.search(r"^\s{2}celery_beat:", text, re.MULTILINE), (
            "docker-compose.prod.yml must define a `celery_beat` service. "
            "If you renamed it, update this test and deploy/COMPOSE_RUNBOOK.md "
            "and deploy/SMOKE_TESTS.md to match."
        )
        assert (
            "celery -A config beat" in text
        ), "celery_beat service command must invoke `celery -A config beat`."
        assert "celery_beat_state:" in text, (
            "celery_beat must mount a persistent `celery_beat_state` volume "
            "so last_run_at survives container restarts; otherwise the "
            "90-day donor task can drift indefinitely on weekly redeploys."
        )

    def test_prod_dockerfile_backups_dir_owned_by_appuser(self):
        """backend/Dockerfile.prod must create /var/backups/oms and chown it to
        the uid-1000 appuser *before* `USER appuser` (oms-3ap7g).

        docker-compose.prod.yml mounts the `backups_volume` named volume at
        /var/backups/oms on the celery + backend services. Docker seeds a
        freshly-created named volume from the image mountpoint, copying its
        ownership — so if the path is absent or root-owned in the image, a new
        volume comes up root:root and the non-root worker gets EACCES writing
        pg_dump archives. The daily backup then silently never runs (the
        BACKEND-A failure mode behind oms-3ap7g). The chown must precede
        `USER appuser`, because once dropped to appuser the build can no longer
        chown a root-owned path.
        """
        text = (REPO_ROOT / "backend" / "Dockerfile.prod").read_text()

        # Look only at instruction lines (drop `#` comments) so prose that
        # mentions `USER appuser` / chown can neither satisfy nor truncate
        # these structural checks.
        instructions = "\n".join(
            line for line in text.splitlines() if not line.lstrip().startswith("#")
        )
        user_match = re.search(r"^USER\s+appuser\b", instructions, re.MULTILINE)
        assert user_match, "Dockerfile.prod must drop privileges with `USER appuser`."
        before_user = instructions[: user_match.start()]

        assert "mkdir -p /var/backups/oms" in before_user, (
            "backend/Dockerfile.prod must `mkdir -p /var/backups/oms` before "
            "`USER appuser` so the backups_volume mountpoint exists in the image "
            "and a freshly-initialized named volume inherits it. See "
            "docker-compose.prod.yml `backups_volume` and backups/tasks.py."
        )
        assert re.search(r"chown\b[^\n]*appuser[^\n]*/var/backups", before_user), (
            "backend/Dockerfile.prod must chown /var/backups to appuser before "
            "`USER appuser`; otherwise the freshly-initialized backups_volume "
            "comes up root:root and backups.daily_postgres_backup hits EACCES "
            "writing the daily pg_dump. See oms-3ap7g."
        )

    def test_k8s_backend_uses_livez_readyz_probes(self):
        """AC-11/AC-12/AC-33: the raw k8s manifests must probe livez (live)
        and readyz (ready), not the legacy /api/dashboard/health/ path.

        livez is dep-free so a slow DB can't cause restart loops; readyz
        503s on dependency failure so kubelet sheds traffic without
        flapping the pod. Drift back to /api/dashboard/health/ recreates
        the failure mode behind github #329 / #375.
        """
        text = (REPO_ROOT / "deploy" / "k8s" / "base" / "backend-deployment.yaml").read_text()
        assert "/api/dashboard/health/" not in text, (
            "deploy/k8s/base/backend-deployment.yaml must not probe "
            "/api/dashboard/health/ — that path executes a DB query and "
            "causes restart loops on transient DB blips. Use livez/readyz."
        )
        assert "/api/health/livez/" in text, (
            "Backend liveness probe must hit /api/health/livez/ " "(dep-free up-check)."
        )
        assert "/api/health/readyz/" in text, (
            "Backend readiness probe must hit /api/health/readyz/ "
            "(checks DB + cache + broker; 503s on failure)."
        )

    def test_helm_backend_uses_livez_readyz_probes(self):
        """AC-11/AC-12/AC-33: Helm chart defaults must match the k8s + compose
        liveness/readiness contract (livez for liveness, readyz for readiness).

        Operators can override per-environment in values, but the shipped
        defaults must not drift back to /api/dashboard/health/.
        """
        text = (REPO_ROOT / "deploy" / "helm" / "openmakersuite" / "values.yaml").read_text()
        assert "/api/dashboard/health/" not in text, (
            "deploy/helm/openmakersuite/values.yaml must not default backend "
            "probes to /api/dashboard/health/ — see test_k8s_backend_uses_"
            "livez_readyz_probes for the rationale."
        )
        assert (
            "/api/health/livez/" in text
        ), "Helm backend liveness default must be /api/health/livez/."
        assert (
            "/api/health/readyz/" in text
        ), "Helm backend readiness default must be /api/health/readyz/."


class TestProductionSafetyCoverageDoc:
    """gh-455 / R-01 + R-02: the production safety coverage table lives at
    docs/PRODUCTION_SAFETY_COVERAGE.md. It is the first-PR artifact for the
    iterative compliance plan on bead oms-zu26, and it indexes
    docs/RISK_REGISTER.md rows R-01 and R-02 to per-class and per-endpoint
    evidence in code. These tests guard against three failure modes:
      1. The doc gets accidentally deleted (the index of safety classes
         disappears).
      2. The doc drifts from the risk register (R-01/R-02 stop pointing here).
      3. The file paths the doc cites get renamed or removed without the
         doc being updated (broken evidence trail).
    """

    DOC_REL = "docs/PRODUCTION_SAFETY_COVERAGE.md"

    REQUIRED_SECTIONS = [
        "## R-01 — Unsafe production defaults",
        "## R-02 — Public unauthenticated write abuse controls",
        "### Configuration class coverage",
        "### Integration of the validator",
        "### Public write endpoints",
        "### Cross-cutting controls",
        "### R-02 gaps and follow-up scope",
        "## Maintenance contract",
    ]

    PATH_PATTERN = re.compile(
        r"`("
        r"(?:backend|scripts|docs|deploy|frontend|\.github)"
        r"/[A-Za-z0-9_./\-]+"
        r")"
        r"(?::\d+(?:-\d+)?)?"
        r"`"
    )

    def test_coverage_doc_exists(self):
        assert (
            REPO_ROOT / self.DOC_REL
        ).is_file(), f"{self.DOC_REL} is the safety-coverage index for gh-455; do not delete it."

    def test_coverage_doc_has_required_sections(self):
        text = (REPO_ROOT / self.DOC_REL).read_text()
        missing = [s for s in self.REQUIRED_SECTIONS if s not in text]
        assert not missing, (
            f"{self.DOC_REL} is missing required section headers: {missing}. "
            "These sections are the contract between R-01/R-02 and per-endpoint "
            "evidence; renaming one requires updating this test in the same PR."
        )

    def test_risk_register_links_to_coverage_doc(self):
        """R-01 and R-02 rows must reference the coverage doc so future
        maintainers can navigate from risk → evidence in one hop."""
        text = (REPO_ROOT / "docs" / "RISK_REGISTER.md").read_text()
        rel = "docs/PRODUCTION_SAFETY_COVERAGE.md"
        # Both rows are on single Markdown table lines; the simple check is
        # that the doc is referenced inside each row's Notes column.
        for row_id in ("R-01", "R-02"):
            row_match = re.search(rf"^\|\s*{re.escape(row_id)}\s*\|.*$", text, re.MULTILINE)
            assert row_match, f"RISK_REGISTER.md is missing row {row_id}."
            row = row_match.group(0)
            assert rel in row, (
                f"RISK_REGISTER.md row {row_id} must reference {rel} in its "
                "Notes column so the evidence trail is one click away."
            )

    def test_coverage_doc_references_existing_paths(self):
        """Every `path/to/file` (with optional :line or :line-line) inside a
        code span must exist on disk. Trips on a rename or accidental delete."""
        text = (REPO_ROOT / self.DOC_REL).read_text()
        cited = set()
        for match in self.PATH_PATTERN.finditer(text):
            cited.add(match.group(1))
        # n/a, …, and matrix tokens are filtered out by the regex shape.
        assert cited, (
            f"{self.DOC_REL} cites no concrete file paths; the doc has lost its " "evidence trail."
        )
        missing = sorted(p for p in cited if not (REPO_ROOT / p).exists())
        assert not missing, (
            f"{self.DOC_REL} references paths that no longer exist on disk: "
            f"{missing}. Update the doc in the same PR that renames or removes "
            "the underlying files."
        )


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
