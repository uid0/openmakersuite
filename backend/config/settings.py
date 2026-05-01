"""
Django settings for makerspace inventory management system.
"""

import sys
from datetime import timedelta
from pathlib import Path

import dj_database_url
import sentry_sdk
from decouple import config
from sentry_sdk.integrations.celery import CeleryIntegration
from sentry_sdk.integrations.django import DjangoIntegration
from sentry_sdk.integrations.redis import RedisIntegration

try:
    import passkeys

    PASSKEYS_TEMPLATE_DIR = passkeys.template_directory
except ImportError:
    PASSKEYS_TEMPLATE_DIR = None

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
    "loto",
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
WEATHER_URL = config(
    "WEATHER_URL",
    default="https://www.wunderground.com/weather/us/tx/carrollton/",
)

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
    CELERY_RESULT_BACKEND = "django-db"  # Store results in Django database
    # Use Django cache for intermediate results
    CELERY_CACHE_BACKEND = "django-cache"

CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_TRACK_STARTED = True  # Track when tasks start
CELERY_TASK_TIME_LIMIT = 30 * 60  # 30 minutes hard timeout
CELERY_RESULT_EXTENDED = True  # Store additional task metadata

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
}

# MQTT Configuration for ForgeKey
MQTT_BROKER_HOST = config("MQTT_BROKER_HOST", default="localhost")
MQTT_BROKER_PORT = config("MQTT_BROKER_PORT", default=1883, cast=int)
MQTT_BROKER_USERNAME = config("MQTT_BROKER_USERNAME", default="")
MQTT_BROKER_PASSWORD = config("MQTT_BROKER_PASSWORD", default="")
MQTT_TOPIC_PREFIX = config("MQTT_TOPIC_PREFIX", default="forgekey")
MQTT_CLIENT_ID = config("MQTT_CLIENT_ID", default="forgekey-server")
MQTT_KEEPALIVE = config("MQTT_KEEPALIVE", default=60, cast=int)
MQTT_BROKER_TLS = config("MQTT_BROKER_TLS", default=False, cast=bool)

# EMQX broker — dashboard password is rendered into the bootstrap_users_file
# by deploy.sh on every deploy; the API key/secret pair is generated in the
# dashboard after first deploy and used by the backend to call EMQX REST API.
EMQX_API_URL = config("EMQX_API_URL", default="http://emqx:18083/api/v5")
EMQX_DASHBOARD_PASSWORD = config("EMQX_DASHBOARD_PASSWORD", default="")
EMQX_API_KEY = config("EMQX_API_KEY", default="")
EMQX_API_SECRET = config("EMQX_API_SECRET", default="")

# ForgeKey JWT Configuration
FORGEKEY_SHARED_SECRET = config("FORGEKEY_SHARED_SECRET", default="change-me-in-production")
FORGEKEY_JWT_ALGORITHM = "HS256"
FORGEKEY_JWT_EXPIRATION_SECONDS = 3600  # 1 hour

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
# by the prune_device_photos celery task.
FORGEKEY_PHOTO_RETENTION_DAYS = config("FORGEKEY_PHOTO_RETENTION_DAYS", default=30, cast=int)

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

# Sentry Configuration
SENTRY_DSN = config(
    "SENTRY_DSN",
    default="https://af885209b7663c58d3fe82ace2863941@o4510248461074432.ingest.us.sentry.io/4510248465661952",
)
SENTRY_ENVIRONMENT = config("SENTRY_ENVIRONMENT", default="development")
SENTRY_RELEASE = config("SENTRY_RELEASE", default=None)

if SENTRY_DSN:
    sentry_sdk.init(
        dsn=SENTRY_DSN,
        integrations=[
            DjangoIntegration(),
            CeleryIntegration(),
            RedisIntegration(),
        ],
        enable_logs=True,
        environment=SENTRY_ENVIRONMENT,
        release=SENTRY_RELEASE,
        # Set traces_sample_rate to 1.0 to capture 100% of transactions for performance monitoring.
        # Adjust this value in production to reduce overhead
        traces_sample_rate=1.0 if DEBUG else 0.1,
        # Set profiles_sample_rate to profile 100% of sampled transactions.
        profiles_sample_rate=1.0 if DEBUG else 0.1,
        # If you wish to associate users to errors (assuming you send personal data to Sentry)
        send_default_pii=True,
        # Capture SQL queries
        _experiments={
            "profiles_sample_rate": 1.0 if DEBUG else 0.1,
        },
    )

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
