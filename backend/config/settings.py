"""
Django settings for makerspace inventory management system.
"""

from pathlib import Path

import dj_database_url
import sentry_sdk
from decouple import config
from sentry_sdk.integrations.celery import CeleryIntegration
from sentry_sdk.integrations.django import DjangoIntegration
from sentry_sdk.integrations.redis import RedisIntegration

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
    # Local apps
    "inventory",
    "reorder_queue",
    "index_cards",
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
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
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
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# Media files
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

# Default primary key field type
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# REST Framework
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.AllowAny",
    )
    if DEVELOPMENT_MODE
    else ("rest_framework.permissions.IsAuthenticatedOrReadOnly",),
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 50,
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

FRONTEND_URL = config("FRONTEND_URL", default="http://localhost:3000")

# Redis configuration
REDIS_URL = config("REDIS_URL", default="redis://localhost:6379/0")

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
CELERY_BROKER_URL = config("CELERY_BROKER_URL", default="redis://localhost:6379/0")
CELERY_RESULT_BACKEND = "django-db"  # Store results in Django database
CELERY_CACHE_BACKEND = "django-cache"  # Use Django cache for intermediate results
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_TRACK_STARTED = True  # Track when tasks start
CELERY_TASK_TIME_LIMIT = 30 * 60  # 30 minutes hard timeout
CELERY_RESULT_EXTENDED = True  # Store additional task metadata

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
SENTRY_DSN = config("SENTRY_DSN", default="https://af885209b7663c58d3fe82ace2863941@o4510248461074432.ingest.us.sentry.io/4510248465661952")
SENTRY_ENVIRONMENT = config("SENTRY_ENVIRONMENT", default="development")

if SENTRY_DSN:
    sentry_sdk.init(
        dsn=SENTRY_DSN,
        integrations=[
            DjangoIntegration(),
            CeleryIntegration(),
            RedisIntegration(),
        ],
        environment=SENTRY_ENVIRONMENT,
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
