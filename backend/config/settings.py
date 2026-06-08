"""
Django settings for makerspace inventory management system.
"""

import logging
import sys
import warnings
from datetime import timedelta
from pathlib import Path

import dj_database_url
import sentry_sdk
from celery.schedules import crontab
from decouple import config
from sentry_sdk.integrations.celery import CeleryIntegration
from sentry_sdk.integrations.django import DjangoIntegration
from sentry_sdk.integrations.logging import LoggingIntegration

try:
    import passkeys

    PASSKEYS_TEMPLATE_DIR = passkeys.template_directory
except ImportError:
    PASSKEYS_TEMPLATE_DIR = None

# Django 6.0.5's django.contrib.auth.decorators emits a DeprecationWarning
# from `asyncio.iscoroutinefunction` on every login_required call. The
# upstream fix is queued for the next Django patch — until then, suppress
# this single deprecation so dev consoles and `manage.py check` aren't
# noisy. Scoped to the exact module so unrelated asyncio deprecations
# still surface. Placed after the import block to satisfy flake8 E402.
warnings.filterwarnings(
    "ignore",
    message=r"'asyncio\.iscoroutinefunction' is deprecated.*",
    category=DeprecationWarning,
    module=r"django\.contrib\.auth\.decorators",
)

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = config("SECRET_KEY", default="django-insecure-dev-key-change-in-production")

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = config("DEBUG", default=True, cast=bool)

# Development mode - allows unauthenticated API access for easier development
# ⚠️ NEVER enable this in production!
DEVELOPMENT_MODE = config("DEVELOPMENT_MODE", default=False, cast=bool)

ALLOWED_HOSTS = config("ALLOWED_HOSTS", default="localhost,127.0.0.1").split(",")
# Loopback addresses are always permitted: container healthchecks and other
# host-local probes hit http://localhost:8000/ and would otherwise trigger
# DisallowedHost log spam when env-supplied ALLOWED_HOSTS omits them.
ALLOWED_HOSTS = list({h.strip() for h in ALLOWED_HOSTS if h.strip()} | {"localhost", "127.0.0.1"})

# CSRF Settings for production deployment
# Required when Django is behind a reverse proxy (nginx) with HTTPS
CSRF_TRUSTED_ORIGINS = config(
    "CSRF_TRUSTED_ORIGINS", default="http://localhost:3000,http://127.0.0.1:3000"
).split(",")

# Proxy settings - required when behind nginx/reverse proxy
# Trust X-Forwarded-Proto header from proxy to determine if request is HTTPS
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# Spool uploaded files larger than this to /tmp instead of holding them in
# the gunicorn worker heap. Django's default (2.5 MiB) is fine for form posts
# but lets a 3MP ESP32 device photo (5–10 MB) sit on the heap until the
# request completes — a per-request RSS spike that contributed to the
# backend OOM in oms-9t2. 1 MiB keeps small admin uploads in memory while
# routing all device photo uploads through TemporaryFileUploadHandler.
FILE_UPLOAD_MAX_MEMORY_SIZE = config(
    "FILE_UPLOAD_MAX_MEMORY_SIZE", default=1 * 1024 * 1024, cast=int
)

# Application definition
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Third party apps
    "rest_framework",
    "rest_framework_simplejwt",
    "corsheaders",
    "imagekit",
    "drf_spectacular",
    "django_celery_results",
    "passkeys",
    "anymail",
    # Local apps
    "config.apps.ConfigConfig",
    "membership",
    "inventory",
    "reorder_queue",
    "index_cards",
    "dashboard",
    "forgekey",
    "customization",
    "location_checkins",
    "checklists",
    "donations",
    "search",
    "notifications",
    "screens",
    "maker_boxes",
    "vendors",
    "maintenance_orders",
    "electrical_circuits",
    "devices",
    "loto",
    "lockers",
    "analytics",
    "climate",
    "project_storage",
    "scanner",
    "preventive_maintenance",
    "backups",
    "resilience",
    "bms",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "config.middleware.PermissionsPolicyMiddleware",
    # Must sit after AuthenticationMiddleware so request.user is set.
    "config.middleware.SentryUserScopeMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [PASSKEYS_TEMPLATE_DIR] if PASSKEYS_TEMPLATE_DIR else [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# Database
DATABASES = {
    "default": dj_database_url.config(
        default=config("DATABASE_URL", default=f'sqlite:///{BASE_DIR / "db.sqlite3"}'),
        conn_max_age=600,
    )
}

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

# Internationalization
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# Static files (CSS, JavaScript, Images)
# Using /django-static/ to avoid conflict with React's /static/ files
STATIC_URL = "/django-static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# Media files
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

# Default primary key field type
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Custom User Model
AUTH_USER_MODEL = "membership.User"

# Authentication backends
# django-passkey-auth integrates with Django's default authentication
# No custom backend needed - it extends the existing authentication system

# REST Framework
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "config.authentication.CSRFExemptJWTAuthentication",
        "config.authentication.CSRFExemptSessionAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": (
        ("rest_framework.permissions.AllowAny",)
        if DEVELOPMENT_MODE
        else ("rest_framework.permissions.IsAuthenticatedOrReadOnly",)
    ),
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 50,
    # AC-7: route every DRF exception through the standardized error envelope
    # so frontend / integration clients can switch on ``error.code``. See
    # docs/API_ERROR_CONTRACT.md for the documented shape.
    "EXCEPTION_HANDLER": "config.api_errors.standardized_exception_handler",
    # gh-713 / AC-5, AC-6 — per-IP rate limits on unauthenticated public
    # write endpoints. No DEFAULT_THROTTLE_CLASSES so AllowAny *read*
    # paths (scan dispatch resolve, QR resolve, kiosk reads) stay open
    # as the makerspace expects; views that need throttling opt in by
    # setting ``throttle_classes = [ScopedRateThrottle]`` + a
    # ``throttle_scope`` matching a key below. Extend the matrix when
    # adding a new public-write endpoint and document the rate's
    # rationale in the view's docstring.
    "DEFAULT_THROTTLE_RATES": {
        # Scanner kiosk dispatch — a busy kiosk fires many scans/min,
        # so the cap is generous, but a single IP shouldn't hammer it.
        "scan_dispatch": "60/min",
        # Project-storage self-service kiosk start. A real member
        # starts one stint per month; an abuse loop should top out fast.
        "project_storage_start": "5/hour",
        # Pi-side print daemon (mark-printed, print-queue, label).
        # Legitimate traffic is once-per-10-seconds; loose cap because
        # one IP may host multiple printers behind NAT.
        "pi_daemon": "120/min",
    },
}

# JWT Token Configuration
# Note: Token lifetimes are set dynamically in CustomRefreshToken based on user type
# Default values here are fallbacks (not used when CustomRefreshToken is used)
SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(days=7),  # Default for regular users
    "REFRESH_TOKEN_LIFETIME": timedelta(days=30),  # Default for regular users
    "ROTATE_REFRESH_TOKENS": False,
    "BLACKLIST_AFTER_ROTATION": False,
    "UPDATE_LAST_LOGIN": False,
    "ALGORITHM": "HS256",
    "SIGNING_KEY": SECRET_KEY,
    "AUTH_HEADER_TYPES": ("Bearer",),
    "AUTH_HEADER_NAME": "HTTP_AUTHORIZATION",
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
    "AUTH_TOKEN_CLASSES": ("config.tokens.CustomAccessToken",),
    "TOKEN_TYPE_CLAIM": "token_type",
    "JTI_CLAIM": "jti",
}

# CORS settings
CORS_ALLOWED_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]

# Allow credentials for session authentication
CORS_ALLOW_CREDENTIALS = True

# Allow all standard HTTP methods
CORS_ALLOW_METHODS = [
    "DELETE",
    "GET",
    "OPTIONS",
    "PATCH",
    "POST",
    "PUT",
]

# Allow all common headers
CORS_ALLOW_HEADERS = [
    "accept",
    "accept-encoding",
    "authorization",
    "content-type",
    "dnt",
    "origin",
    "user-agent",
    "x-csrftoken",
    "x-requested-with",
]

# In development mode, allow all origins for easier testing
if DEVELOPMENT_MODE:
    CORS_ALLOW_ALL_ORIGINS = True
    CORS_ALLOW_CREDENTIALS = True

FRONTEND_URL = config("FRONTEND_URL", default="http://192.168.1.36:3000")

# Shared weather widget URL surfaced to all kiosk screens. All SIGs share the
# same physical location, so a single global URL avoids per-screen duplication.
# Legacy: kept while the iframe-based SharedWeatherBlock is still on screens
# that haven't switched to the OpenWeather-backed block.
WEATHER_URL = config(
    "WEATHER_URL",
    default="https://www.wunderground.com/weather/us/tx/carrollton/",
)

# OpenWeather server-side proxy (screens.weather_views.current_weather).
# Replaces the iframe-embedded widget that CSP blocks on most TVs. Set
# OPENWEATHER_API_KEY + either OPENWEATHER_LAT/LON or OPENWEATHER_ZIP for
# the kiosk weather block to light up.
OPENWEATHER_API_KEY = config("OPENWEATHER_API_KEY", default="")
OPENWEATHER_LAT = config("OPENWEATHER_LAT", default=None)
OPENWEATHER_LON = config("OPENWEATHER_LON", default=None)
OPENWEATHER_ZIP = config("OPENWEATHER_ZIP", default="")  # e.g. "75001,us"
OPENWEATHER_UNITS = config("OPENWEATHER_UNITS", default="imperial")
OPENWEATHER_CACHE_SECONDS = config("OPENWEATHER_CACHE_SECONDS", default=600, cast=int)

# Passkey / WebAuthn configuration
# django-passkey-auth derives the Relying Party ID from request.get_host(). When
# the site is served behind TLS, browsers will only surface the save-passkey
# prompt if the RP ID matches the current host. PASSKEY_SITE_NAME is the
# human-readable name shown to the user in the browser dialog.
PASSKEY_SITE_NAME = config("PASSKEY_SITE_NAME", default="Makerspace Inventory")

# Make session cookies safe to ship over the cross-origin pairing between the
# SPA and the API. Defaults are conservative for local dev; production overrides
# via env vars.
SESSION_COOKIE_SAMESITE = config("SESSION_COOKIE_SAMESITE", default="Lax")
SESSION_COOKIE_SECURE = config("SESSION_COOKIE_SECURE", default=not DEBUG, cast=bool)
CSRF_COOKIE_SAMESITE = config("CSRF_COOKIE_SAMESITE", default="Lax")
CSRF_COOKIE_SECURE = config("CSRF_COOKIE_SECURE", default=not DEBUG, cast=bool)

# Outbound email via Postmark (django-anymail). The console backend is a safer
# default for development — production overrides EMAIL_BACKEND via env to route
# real mail through Postmark.
EMAIL_BACKEND = config(
    "EMAIL_BACKEND",
    default="django.core.mail.backends.console.EmailBackend",
)
ANYMAIL = {
    "POSTMARK_SERVER_TOKEN": config("POSTMARK_SERVER_TOKEN", default=""),
}
DEFAULT_FROM_EMAIL = config(
    "DEFAULT_FROM_EMAIL",
    default="noreply@openmakersuite.example",
)

# Logistics distribution-list address that receives high/urgent LocationProblem
# alerts (oms-0yz). Empty disables email alerts.
LOGISTICS_ALERT_EMAIL = config(
    "LOGISTICS_ALERT_EMAIL",
    default="",
)

# WHMCS API credentials for the maker box (personal storage bin) verification
# flow. All four are sourced from environment variables; if URL/identifier/
# secret are missing the maker_boxes service degrades to a 503 response from
# the scan endpoint rather than failing requests in unexpected ways.
WHMCS_API_URL = config("WHMCS_API_URL", default="")
WHMCS_API_IDENTIFIER = config("WHMCS_API_IDENTIFIER", default="")
WHMCS_API_SECRET = config("WHMCS_API_SECRET", default="")
WHMCS_API_ACCESSKEY = config("WHMCS_API_ACCESSKEY", default="")

# Postmark inbound webhook shared secret. The inbound work-order endpoint is
# unauthenticated (Postmark does not ship a request signature), so this token
# must match the `?token=` query param or the `X-Postmark-Webhook-Token` header
# that Postmark is configured to send. If empty, the endpoint returns 503.
POSTMARK_INBOUND_TOKEN = config("POSTMARK_INBOUND_TOKEN", default="")

# Shared secret used by IoT devices (ForgeKey, ESP32, etc.) to authenticate
# anonymous traffic-count pings to /api/location-checkins/webhook/. If empty,
# the endpoint returns 503.
LOCATION_PING_TOKEN = config("LOCATION_PING_TOKEN", default="")

# Shared kiosk resources. SIGs at the same physical site share a single
# traffic feed; the kiosk renders this URL inside any 'shared_traffic'
# content block, so updating the env var changes every screen at once.
TRAFFIC_URL = config(
    "TRAFFIC_URL",
    default="",
)

# Redis configuration
REDIS_URL = config("REDIS_URL", default="redis://192.168.1.36:6379/0")

# Circuit breakers (resilience app): fail fast when an external dependency
# (MQTT broker, vendor HTTP API, ...) is unhealthy instead of hammering it
# from every web/worker process. State is shared via Redis so one trip
# protects the whole fleet. Disabled under tests so each test stays hermetic —
# the resilience suite re-enables them explicitly.
_RUNNING_TESTS = "pytest" in sys.modules or (len(sys.argv) > 1 and sys.argv[1] == "test")
CIRCUIT_BREAKERS_ENABLED = config("CIRCUIT_BREAKERS_ENABLED", default=not _RUNNING_TESTS, cast=bool)
CIRCUIT_BREAKER_USE_REDIS = config(
    "CIRCUIT_BREAKER_USE_REDIS", default=not _RUNNING_TESTS, cast=bool
)

# Cache configuration
CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": REDIS_URL,
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
        },
    }
}

# Celery Configuration
# Check if we're running tests
TESTING = len(sys.argv) > 1 and sys.argv[1] == "test"

if TESTING:
    # Run tasks synchronously during tests (no Redis required)
    CELERY_TASK_ALWAYS_EAGER = True
    CELERY_TASK_EAGER_PROPAGATES = True
    CELERY_BROKER_URL = "memory://"
    CELERY_RESULT_BACKEND = "cache"
    CELERY_CACHE_BACKEND = "memory"
else:
    CELERY_BROKER_URL = config("CELERY_BROKER_URL", default="redis://192.168.1.36:6379/0")
    # gh #378: route through the redacting backend so traceback + result
    # payloads are scrubbed before they reach TaskResult. Falls back to the
    # standard django-db backend for tests (CELERY_RESULT_BACKEND="cache"
    # under TESTING).
    CELERY_RESULT_BACKEND = "config.celery_result_backend.RedactingDatabaseBackend"
    # Use Django cache for intermediate results
    CELERY_CACHE_BACKEND = "django-cache"

CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_TRACK_STARTED = True  # Track when tasks start
CELERY_TASK_TIME_LIMIT = 30 * 60  # 30 minutes hard timeout
CELERY_RESULT_EXTENDED = True  # Store additional task metadata

# Route the heavyweight firmware build to a dedicated "builds" queue so it runs
# on the self-hosted firmware-builder worker (git + PlatformIO), not the app
# worker pool. The app image ships no toolchain; only that worker consumes it.
CELERY_TASK_ROUTES = {
    "forgekey.tasks.build_firmware": {"queue": "builds"},
}

# Git URL the firmware-builder worker clones to build ForgeKey firmware.
# Default is HTTPS — the PAT-based auth path is the recommended one (see
# FORGEKEY_BUILDER_GITHUB_TOKEN below). The legacy SSH URL
# (git@github.com:...) still works when FORGEKEY_DEPLOY_KEY is mounted
# into the worker container, but PAT is simpler to rotate and never
# touches ssh-agent.
FORGEKEY_FIRMWARE_REPO_URL = config(
    "FORGEKEY_FIRMWARE_REPO_URL", default="https://github.com/uid0/ForgeKey.git"
)

# GitHub Personal Access Token (or fine-grained token) the firmware-
# builder injects into the HTTPS clone URL. Read-only by design — the
# worker only ever clones. A fine-grained PAT scoped to the ForgeKey
# repo with `Contents: Read` is the minimum-privilege option. When this
# is empty, the worker falls back to whatever ssh key the container has
# at /root/.ssh/id_ed25519 (the legacy FORGEKEY_DEPLOY_KEY mount).
FORGEKEY_BUILDER_GITHUB_TOKEN = config("FORGEKEY_BUILDER_GITHUB_TOKEN", default="")

# Celery Beat Schedule for periodic tasks
CELERY_BEAT_SCHEDULE = {
    "send-quarterly-donor-updates": {
        "task": "donations.tasks.send_quarterly_donor_updates",
        "schedule": 7776000.0,  # 90 days in seconds (quarterly)
        # Note: For more precise quarterly scheduling (Jan 1, Apr 1, Jul 1, Oct 1),
        # use crontab instead:
        # "schedule": crontab(day_of_month=1, month_of_year="1,4,7,10"),
    },
    "flag-expiring-vendor-compliance": {
        "task": "vendors.flag_expiring_compliance",
        "schedule": 86400.0,  # daily — emails Logistics a TDLR/COI digest
    },
    "forgekey-mark-stale-devices-offline": {
        "task": "forgekey.tasks.mark_stale_devices_offline",
        # Every 30 min — combined with the threshold below this gives a worst-
        # case time-to-offline of (threshold + 30min). At threshold=5h that
        # lands inside the 4-6h SLA on gh #349.
        "schedule": 1800.0,
        "kwargs": {
            "threshold_hours": config(
                "FORGEKEY_DEVICE_OFFLINE_THRESHOLD_HOURS", default=5, cast=int
            ),
        },
    },
    "forgekey-prune-device-photos": {
        "task": "forgekey.tasks.prune_device_photos",
        "schedule": 86400.0,  # daily — drops ESP32DevicePhoto rows past retention
        "kwargs": {
            "retention_days": config("FORGEKEY_PHOTO_RETENTION_DAYS", default=30, cast=int),
        },
    },
    "forgekey-advance-firmware-rollouts": {
        "task": "forgekey.tasks.advance_firmware_rollouts",
        # Every 5 min. Each active rollout still self-limits to its own
        # interval_minutes, so this only dispatches a wave when one is due.
        "schedule": 300.0,
    },
    "forgekey-advance-epaper-firmware-rollouts": {
        "task": "forgekey.tasks.advance_epaper_firmware_rollouts",
        # Same cadence as the MQTT rollout advance task — each rollout
        # self-limits to its own interval_minutes.
        "schedule": 300.0,
    },
    # Monthly board / staff pulse email — covers the prior calendar month.
    # Uses crontab so we land at 09:00 on the 1st regardless of how the
    # worker scheduler restarted during the month.
    "analytics-send-monthly-pulse": {
        "task": "analytics.send_monthly_pulse_email",
        "schedule": crontab(minute=0, hour=9, day_of_month=1),
    },
    # Every 5 minutes — pushes a fixed list of business-counter gauges
    # to Sentry Logs (Sentry retired the experimental Metrics SDK in
    # 2.x, so Logs with a numeric `value` attribute is the supported
    # replacement). See analytics.tasks.METRIC_SNAPSHOT_NAMES for the
    # canonical list. Cost is ~10 log entries per emit per environment.
    "analytics-emit-metric-snapshot": {
        "task": "analytics.emit_metric_snapshot",
        "schedule": 300.0,
    },
    # Daily PostgreSQL backup at 02:00 UTC. Pairs with the existing
    # scripts/restore-drill.sh which CI runs on every deploy-touching
    # PR — this is the production scheduled write half of R-03. See
    # backups/tasks.py for the dump format and retention policy
    # (OMS_BACKUP_RETENTION_DAYS, default 14 days).
    "backups-daily-postgres": {
        "task": "backups.daily_postgres_backup",
        "schedule": crontab(minute=0, hour=2),
    },
}

# Backups (R-03). Output volume and retention window for the
# `backups.daily_postgres_backup` Celery task. The directory must be
# a writable named volume on the celery container — see the `backups_volume`
# mount in docker-compose.prod.yml. The retention default lines up with
# `scripts/restore-drill.sh`'s assumption that the freshest dump is the
# one to restore from.
OMS_BACKUP_DIR = config("OMS_BACKUP_DIR", default="/var/backups/oms")
OMS_BACKUP_RETENTION_DAYS = config("OMS_BACKUP_RETENTION_DAYS", default=14, cast=int)

# MQTT Configuration for ForgeKey
MQTT_BROKER_HOST = config("MQTT_BROKER_HOST", default="localhost")
MQTT_BROKER_PORT = config("MQTT_BROKER_PORT", default=1883, cast=int)
MQTT_BROKER_USERNAME = config("MQTT_BROKER_USERNAME", default="")
MQTT_BROKER_PASSWORD = config("MQTT_BROKER_PASSWORD", default="")
MQTT_TOPIC_PREFIX = config("MQTT_TOPIC_PREFIX", default="forgekey")
MQTT_CLIENT_ID = config("MQTT_CLIENT_ID", default="forgekey-server")
MQTT_KEEPALIVE = config("MQTT_KEEPALIVE", default=60, cast=int)
MQTT_BROKER_TLS = config("MQTT_BROKER_TLS", default=False, cast=bool)

# Public-facing MQTT broker coordinates returned to ForgeKey devices in the
# registration response. Distinct from MQTT_BROKER_HOST above, which is the
# internal hostname (e.g. "emqx") the BACKEND uses to publish commands;
# devices on the public internet can't resolve that. PUBLIC_MQTT_BROKER_HOST
# is the externally-resolvable hostname devices persist to NVS so the broker
# can be re-pointed without a firmware rebuild + reflash. Falls back to
# MQTT_BROKER_HOST in the registration view when unset for dev convenience.
PUBLIC_MQTT_BROKER_HOST = config("PUBLIC_MQTT_BROKER_HOST", default="")
PUBLIC_MQTT_BROKER_PORT = config("PUBLIC_MQTT_BROKER_PORT", default=1883, cast=int)
PUBLIC_MQTT_BROKER_USE_TLS = config("PUBLIC_MQTT_BROKER_USE_TLS", default=False, cast=bool)

# Cooldown after a failed MQTT connect before we try again. Without this, a
# broker outage causes every send_mqtt_command() call to instantiate a fresh
# paho.Client + fail, leaking sockets/buffers — see oms-9t2.
MQTT_CONNECT_RETRY_COOLDOWN_SECONDS = config(
    "MQTT_CONNECT_RETRY_COOLDOWN_SECONDS", default=30, cast=int
)

# EMQX broker — dashboard password is rendered into the bootstrap_users_file
# by deploy.sh on every deploy; the API key/secret pair is generated in the
# dashboard after first deploy and used by the backend to call EMQX REST API.
EMQX_API_URL = config("EMQX_API_URL", default="http://emqx:18083/api/v5")
EMQX_DASHBOARD_PASSWORD = config("EMQX_DASHBOARD_PASSWORD", default="")
EMQX_API_KEY = config("EMQX_API_KEY", default="")
EMQX_API_SECRET = config("EMQX_API_SECRET", default="")

# ForgeKey JWT Configuration
#
# Device JWTs are signed with ES256 (ECDSA P-256) so EMQX's JWT authenticator
# can verify them by fetching the matching public key from /api/forgekey/jwks/.
# The PEM may include literal "\n" newlines (env-friendly). FORGEKEY_SHARED_SECRET
# is retained for legacy callers but unused by the JWT path.
FORGEKEY_SHARED_SECRET = config("FORGEKEY_SHARED_SECRET", default="change-me-in-production")

# Low-battery threshold for XIAO ePaper PM-display panels — telemetry
# reports below this percent emit a Sentry warning so ops can prep a
# charged swap before the panel goes dark. 20% leaves enough headroom
# for a multi-day swap window.
FORGEKEY_EPAPER_LOW_BATTERY_PERCENT = config(
    "FORGEKEY_EPAPER_LOW_BATTERY_PERCENT", default=20, cast=int
)

# How often (minutes) an ePaper panel wakes from deep sleep to pull
# its image. Served verbatim from /api/forgekey/epaper/<id>/desired.json;
# the firmware programs its next esp_sleep_enable_timer_wakeup from
# this. Increase for longer battery life, decrease for snappier
# response to PM events. 60 is the default panel cadence in firmware
# too, so this value matters only when operators want to override.
FORGEKEY_EPAPER_DEFAULT_WAKE_MIN = config("FORGEKEY_EPAPER_DEFAULT_WAKE_MIN", default=60, cast=int)

FORGEKEY_JWT_ALGORITHM = "ES256"
FORGEKEY_JWT_EXPIRATION_SECONDS = config(
    "FORGEKEY_JWT_EXPIRATION_SECONDS", default=2592000, cast=int  # 30 days
)
FORGEKEY_JWT_ISSUER = config("FORGEKEY_JWT_ISSUER", default="openmakersuite")
FORGEKEY_JWT_AUDIENCE = config("FORGEKEY_JWT_AUDIENCE", default="forgekey")
FORGEKEY_JWT_KEY_ID = config("FORGEKEY_JWT_KEY_ID", default="forgekey-jwt-1")
FORGEKEY_JWT_SIGNING_KEY = config("FORGEKEY_JWT_SIGNING_KEY", default="").replace("\\n", "\n")

# Server-JWT (oms-y5p): the backend authenticates to EMQX as 'oms-backend'
# using a self-issued ES256 JWT signed with the same key as device JWTs.
# The token's exp is intentionally long (1 year) because rotation is driven by
# FORGEKEY_JWT_KEY_ID changes, not by token churn. Within a process the JWT is
# cached for FORGEKEY_SERVER_JWT_CACHE_SECONDS so we don't re-sign on every
# MQTT connect.
FORGEKEY_SERVER_JWT_EXPIRATION_SECONDS = config(
    "FORGEKEY_SERVER_JWT_EXPIRATION_SECONDS", default=60 * 60 * 24 * 365, cast=int
)
FORGEKEY_SERVER_JWT_CACHE_SECONDS = config(
    "FORGEKEY_SERVER_JWT_CACHE_SECONDS", default=60 * 60 * 24, cast=int  # 24h
)

# ForgeKey provisioning token — devices send this in
# X-ForgeKey-Provisioning-Token to call the registration endpoint.
FORGEKEY_PROVISIONING_TOKEN = config("FORGEKEY_PROVISIONING_TOKEN", default="")

# ECDSA(P-256) private key (PEM, may include literal "\n" newlines) used to
# sign firmware binaries before MQTT dispatch. The matching public key is
# baked into device firmware and used to verify signatures on receipt.
# Generate with scripts/build/gen-firmware-signing-key.sh in the forgekey repo.
# Leave empty in dev; production deployments must set this to a real key.
FORGEKEY_FIRMWARE_SIGNING_KEY = config("FORGEKEY_FIRMWARE_SIGNING_KEY", default="").replace(
    "\\n", "\n"
)

# ForgeKey periodic-photo retention (days). Photos older than this are pruned
# by the prune_device_photos celery task. The same env var is read in
# CELERY_BEAT_SCHEDULE above to pass the value as a kwarg to the scheduled
# run, so changing it here changes both the per-call default and what beat
# fires daily.
FORGEKEY_PHOTO_RETENTION_DAYS = config("FORGEKEY_PHOTO_RETENTION_DAYS", default=30, cast=int)

# Shared secret EMQX must send in the X-ForgeKey-Webhook-Secret header on
# WebHook bridge POSTs to /api/forgekey/mqtt-webhook/. Empty disables the
# endpoint (returns 503) so a misconfigured deployment fails closed instead
# of silently accepting unauthenticated traffic.
FORGEKEY_WEBHOOK_SECRET = config("FORGEKEY_WEBHOOK_SECRET", default="")

# Optional comma-separated IP allowlist for the MQTT webhook. When set, the
# request's REMOTE_ADDR must match one of these entries or the request is
# rejected before the secret check runs. Leave empty to skip IP filtering.
FORGEKEY_WEBHOOK_ALLOWED_IPS = [
    entry.strip()
    for entry in config("FORGEKEY_WEBHOOK_ALLOWED_IPS", default="").split(",")
    if entry.strip()
]

# Device-identity trust foundation (oms-d2axqu / forgekey-trust-refactor).
#
# FORGEKEY_CA_KEY_ENCRYPTION_KEY: base64-encoded 32-byte KEK used to wrap the
# internal CA's private key with Fernet at rest. Required to bootstrap or
# decrypt the CA. Generate with:
#     python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# Empty in dev/test by default; the management command and signing service
# fail loud on missing/invalid keys rather than silently falling back to
# plaintext.
FORGEKEY_CA_KEY_ENCRYPTION_KEY = config("FORGEKEY_CA_KEY_ENCRYPTION_KEY", default="")

# How long an unfinished DeviceEnrollment session is valid before the
# enrollment endpoint considers it expired. 10 minutes by default.
FORGEKEY_ENROLLMENT_SESSION_TTL_SECONDS = config(
    "FORGEKEY_ENROLLMENT_SESSION_TTL_SECONDS", default=600, cast=int
)

# Client-certificate validity for /enroll/-issued certs (in days). 365 by
# default. Re-enrollment issues a fresh cert and revokes the prior one.
FORGEKEY_CLIENT_CERT_VALIDITY_DAYS = config(
    "FORGEKEY_CLIENT_CERT_VALIDITY_DAYS", default=365, cast=int
)

# Building Management System (BMS) integration. The first concrete adapter
# is Resideo / Honeywell Home Pro (developer.resideo.com). The app-level
# OAuth client_id + client_secret live here so per-user tokens (in
# bms.BmsConfig) carry no credential beyond the access/refresh pair.
#
# Resideo's OAuth: authorization_code grant against
# https://api.honeywell.com/oauth2/{authorize,token}. Access tokens TTL
# ~30 min; refresh tokens are long-lived but can be revoked from the
# Resideo dashboard, in which case bms_resideo_auth must be re-run.
RESIDEO_CLIENT_ID = config("RESIDEO_CLIENT_ID", default="")
RESIDEO_CLIENT_SECRET = config("RESIDEO_CLIENT_SECRET", default="")
RESIDEO_REDIRECT_URI = config("RESIDEO_REDIRECT_URI", default="https://localhost/bms/callback")
RESIDEO_API_BASE = config("RESIDEO_API_BASE", default="https://api.honeywell.com")

# Spectacular settings for API documentation
SPECTACULAR_SETTINGS = {
    "TITLE": "Makerspace Inventory Management API",
    "DESCRIPTION": """
    Open source inventory management system for makerspaces.

    ## Features

    - **Inventory Management**: Track items, categories, suppliers, and locations
    - **QR Code Integration**: Generate and scan QR codes for easy item identification
    - **Index Card Generation**: Create printable 3x5" or 5x3" index cards with item details
    - **Reorder Management**: Automated reorder requests and supplier integration
    - **Usage Tracking**: Log item usage and calculate reorder timing

    ## Authentication

    This API supports both JWT and session authentication:
    - Use `DEVELOPMENT_MODE=1` for development (no auth required)
    - Use JWT tokens for production authentication
    - Session authentication via Django admin login

    ## Getting Started

    1. Browse available endpoints in the interactive documentation
    2. Click on any UUID to navigate to related objects
    3. Generate index cards and QR codes for your inventory
    4. Set up automated reorder workflows

    ## Support

    For questions or issues, please refer to the project documentation.
    """,
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
    "SWAGGER_UI_SETTINGS": {
        "deepLinking": True,
        "displayRequestDuration": True,
        "docExpansion": "list",
        "filter": True,
        "showExtensions": True,
        "showCommonExtensions": True,
        "url": "/api/schema/",
    },
}

# Readiness probe — which checks gate HTTP 200 vs 503 (oms-aam).
# Comma-separated list. Defaults to db/cache/broker (the historical
# contract). Optional checks (celery_worker, emqx, object_store, telemetry)
# always report status but only flap traffic when explicitly listed here.
READYZ_REQUIRED_CHECKS = config(
    "READYZ_REQUIRED_CHECKS",
    default="database,cache,broker",
)

# Sentry error reporting. No-op when SENTRY_DSN is unset; in production the
# DSN is set in `.env` and `scripts/validate-prod-env.sh` warns if it's
# empty. `_check_telemetry` in `config/health.py` verifies reachability.
#
# - DjangoIntegration: captures unhandled view exceptions, 5xx, slow ORM.
# - CeleryIntegration: captures task failures + propagates trace IDs across
#   the Redis broker so a request → task chain shows up as one transaction.
# - LoggingIntegration: ERROR → Sentry events (alertable). INFO+ → Sentry
#   Logs (searchable, no alerting) once enable_logs is on, so debug-shaped
#   signal lands in the Logs UI without paging anyone.
SENTRY_DSN = config("SENTRY_DSN", default="")
SENTRY_ENVIRONMENT = config(
    "SENTRY_ENVIRONMENT", default="production" if not DEBUG else "development"
)
SENTRY_RELEASE = config("GIT_HASH", default="") or None
# 0.5 = 50% of requests / tasks emit a full transaction. Makerspace
# traffic is well under any rate that'd strain Sentry storage at this
# sample, and the higher rate gives uid0 real coverage for finding
# slow views + N+1 queries. Override via env if storage pressure climbs.
SENTRY_TRACES_SAMPLE_RATE = config("SENTRY_TRACES_SAMPLE_RATE", default=0.5, cast=float)
# Continuous profiling (sentry-sdk 2.x). `profile_session_sample_rate`
# samples at the *session* (i.e. process) level rather than per-trace —
# combined with `profile_lifecycle="trace"` the sampler only runs while
# a transaction is active, so idle workers don't burn CPU on samples
# that nothing will reference. 1.0 = every gunicorn/celery process that
# emits traces also emits a profile; revisit if Sentry storage grows.
SENTRY_PROFILE_SESSION_SAMPLE_RATE = config(
    "SENTRY_PROFILE_SESSION_SAMPLE_RATE", default=1.0, cast=float
)
# Legacy transaction-bound profiling (deprecated in sentry-sdk 2.x but
# still honoured). Left as an escape hatch for the operator — set this
# instead of the session knob if the new continuous mode misbehaves on
# a specific worker class.
SENTRY_PROFILES_SAMPLE_RATE = config("SENTRY_PROFILES_SAMPLE_RATE", default=0.0, cast=float)

if SENTRY_DSN:
    sentry_sdk.init(
        dsn=SENTRY_DSN,
        environment=SENTRY_ENVIRONMENT,
        release=SENTRY_RELEASE,
        integrations=[
            DjangoIntegration(transaction_style="url"),
            CeleryIntegration(monitor_beat_tasks=True),
            LoggingIntegration(
                event_level=logging.ERROR,
                sentry_logs_level=logging.INFO,
            ),
        ],
        traces_sample_rate=SENTRY_TRACES_SAMPLE_RATE,
        profiles_sample_rate=SENTRY_PROFILES_SAMPLE_RATE,
        profile_session_sample_rate=SENTRY_PROFILE_SESSION_SAMPLE_RATE,
        profile_lifecycle="trace",
        enable_logs=True,
        # Don't ship request bodies / user PII by default; the redactor in
        # config.observability_redaction handles fine-grained scrubbing for
        # our own log/audit surfaces.
        send_default_pii=False,
    )
    # Drop noisy framework warnings; let LoggingIntegration carry the real
    # signal (logger.error / .exception in app code).
    sentry_sdk.integrations.logging.ignore_logger("django.security.DisallowedHost")

# Logging configuration
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "{levelname} {asctime} {module} {process:d} {thread:d} {message}",
            "style": "{",
        },
        "simple": {
            "format": "{levelname} {message}",
            "style": "{",
        },
    },
    "filters": {
        "require_debug_false": {
            "()": "django.utils.log.RequireDebugFalse",
        },
        "require_debug_true": {
            "()": "django.utils.log.RequireDebugTrue",
        },
    },
    "handlers": {
        "console": {
            "level": "INFO",
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
        "console_debug": {
            "level": "DEBUG",
            "filters": ["require_debug_true"],
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
        "django.request": {
            "handlers": ["console"],
            "level": "ERROR",
            "propagate": False,
        },
        "django.server": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
        "django.db.backends": {
            "handlers": ["console_debug"],
            "level": "WARNING",
            "propagate": False,
        },
        "inventory": {
            "handlers": ["console"],
            "level": "DEBUG" if DEBUG else "INFO",
            "propagate": False,
        },
        "reorder_queue": {
            "handlers": ["console"],
            "level": "DEBUG" if DEBUG else "INFO",
            "propagate": False,
        },
        "celery": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
}
