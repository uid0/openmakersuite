FROM python:3.13-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Set work directory
WORKDIR /app

# Install system dependencies
#   libzbar0:      runtime for pyzbar (QR-code fallback in
#                  inventory.services.work_order_ingest). Without it pyzbar
#                  errors on import and the work-order PDF QR extraction
#                  falls back to cv2 only.
#   libgl1, libglib2.0-0:
#                  runtime libs opencv-python-headless dlopen()s for
#                  image decode. The wheel bundles most things but on
#                  python:slim it still needs these from the OS.
RUN apt-get update && apt-get install -y \
    postgresql-client \
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
