FROM python:3.14-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Set work directory
WORKDIR /app

# Install system dependencies
#   postgresql-client-18:
#                  the backup task (backups.tasks.daily_postgres_backup)
#                  shells out to `pg_dump -Fc` against the prod postgres
#                  18 server. Debian's default postgresql-client package
#                  ships v15 on bookworm-slim, and pg_dump <= server
#                  refuses to dump (custom-format archive version skew),
#                  which is what BACKEND-A in Sentry was catching. Install
#                  v18 explicitly from the PostgreSQL APT repo.
#   libzbar0:      runtime for pyzbar (QR-code fallback in
#                  inventory.services.work_order_ingest). Without it pyzbar
#                  errors on import and the work-order PDF QR extraction
#                  falls back to cv2 only.
#   libgl1, libglib2.0-0:
#                  runtime libs opencv-python-headless dlopen()s for
#                  image decode. The wheel bundles most things but on
#                  python:slim it still needs these from the OS.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl gnupg \
    && install -d /usr/share/postgresql-common/pgdg \
    && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
       -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
    && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt $(. /etc/os-release && echo "$VERSION_CODENAME")-pgdg main" \
       > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update && apt-get install -y --no-install-recommends \
        postgresql-client-18 \
        build-essential \
        libpq-dev \
        libzbar0 \
        libgl1 \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt /app/
RUN pip install --upgrade pip && pip install -r requirements.txt

# Copy project
COPY . /app/

# Collect static files (will be overridden by volume in development)
RUN mkdir -p /app/staticfiles /app/media

EXPOSE 8000
