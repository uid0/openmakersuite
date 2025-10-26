# Celery Task Management Guide

This guide explains how to monitor and manage Celery background tasks in OpenMakerSuite.

## Overview

OpenMakerSuite uses Celery for asynchronous task processing, including:
- **Image downloads** - Downloading item images from URLs
- **QR code generation** - Creating QR codes for inventory items
- **Index card generation** - Generating PDF index cards
- **Lead time updates** - Calculating average supplier lead times

## Management Tools

### 1. Django Admin Interface

The Django admin provides a built-in interface for viewing task results.

**Access:** Navigate to `http://localhost:8000/admin/django_celery_results/taskresult/`

**Features:**
- View all task executions with status (SUCCESS, FAILURE, PENDING, etc.)
- Filter by task name, status, date, and worker
- Search by task ID or arguments
- View detailed task information including:
  - Task inputs (arguments and kwargs)
  - Task outputs (results or error tracebacks)
  - Execution timing
  - Worker information
- Color-coded status indicators:
  - **Green** - SUCCESS
  - **Red** - FAILURE
  - **Orange** - PENDING
  - **Blue** - STARTED
  - **Purple** - RETRY
  - **Gray** - REVOKED

**Usage Tips:**
- Results are read-only (cannot be edited)
- Only superusers can delete old task results
- Use date hierarchy to browse by creation date
- Filter by task name to see specific task types

### 2. Flower - Real-time Monitoring Dashboard

Flower is a web-based tool for monitoring and administrating Celery clusters.

**Starting Flower:**
```bash
# In the backend directory
celery -A config flower --port=5555
```

**Access:** Navigate to `http://localhost:5555`

**Features:**
- **Real-time monitoring** - See tasks as they execute
- **Worker management** - View worker status, restart workers
- **Task statistics** - Graphs and charts of task execution
- **Task details** - Inspect individual task results
- **Rate limiting** - Control task execution rates
- **Worker pools** - Monitor worker pool sizes

**Dashboard Sections:**
1. **Tasks** - List all tasks with filtering and search
2. **Workers** - View active workers and their status
3. **Broker** - Monitor Redis/broker connection
4. **Monitor** - Real-time task execution graphs
5. **Tasks (by type)** - Group tasks by task name

## Available Tasks

### Image Download Task
**Task:** `inventory.tasks.download_image_from_url`

**Purpose:** Download images from URLs and attach to inventory items

**Arguments:**
- `item_id` (str) - UUID of the inventory item
- `image_url` (str) - URL to download image from

**Triggered:** Automatically when an item is created/updated with an `image_url`

**Example:**
```python
from inventory.tasks import download_image_from_url

result = download_image_from_url.delay(
    item_id='94cb6421-7fa1-4dab-802f-ec76eba2bfe7',
    image_url='https://example.com/image.jpg'
)
```

### QR Code Generation Task
**Task:** `inventory.tasks.generate_qr_code`

**Purpose:** Generate QR codes for inventory items

**Arguments:**
- `item_id` (str) - UUID of the inventory item

**Example:**
```python
from inventory.tasks import generate_qr_code

result = generate_qr_code.delay(
    item_id='94cb6421-7fa1-4dab-802f-ec76eba2bfe7'
)
```

### Index Card Generation Task
**Task:** `inventory.tasks.generate_index_card`

**Purpose:** Generate PDF index cards for items

**Arguments:**
- `item_id` (str) - UUID of the inventory item

### Lead Time Update Task
**Task:** `inventory.tasks.update_average_lead_times`

**Purpose:** Update average lead times based on historical data

**Arguments:** None (periodic task)

**Schedule:** Run this manually or set up with Celery Beat for periodic execution

## Task Status Meanings

- **PENDING** - Task is waiting to be executed
- **STARTED** - Task has begun execution
- **SUCCESS** - Task completed successfully
- **FAILURE** - Task failed with an error
- **RETRY** - Task is being retried after a failure
- **REVOKED** - Task was cancelled/revoked

## Monitoring Best Practices

### 1. Regular Checks
- Review failed tasks daily in Django admin
- Monitor worker health in Flower
- Check task queue lengths to avoid backlogs

### 2. Error Handling
- Failed tasks appear in red in Django admin
- Click on failed tasks to view full error traceback
- Common issues:
  - Network timeouts (image downloads)
  - Invalid URLs
  - Missing items (deleted before task ran)

### 3. Performance Monitoring
- Use Flower to track task execution times
- Monitor worker CPU/memory usage
- Adjust worker pool size if needed

### 4. Cleanup
- Periodically delete old successful task results
- Keep failed tasks for debugging
- Use Django admin filters to find old tasks:
  ```
  Status: SUCCESS
  Date created: Before [date]
  ```

## Configuration

### Current Settings
Located in `backend/config/settings.py`:

```python
CELERY_BROKER_URL = "redis://localhost:6379/0"
CELERY_RESULT_BACKEND = "django-db"  # Store in Django database
CELERY_TASK_TRACK_STARTED = True  # Track when tasks start
CELERY_TASK_TIME_LIMIT = 30 * 60  # 30 minute timeout
CELERY_RESULT_EXTENDED = True  # Store extra metadata
```

### Worker Configuration
- **Concurrency:** Default (number of CPU cores)
- **Pool:** prefork (multiprocessing)
- **Loglevel:** info

## Common Commands

### Start Celery Worker
```bash
cd backend
celery -A config worker -l info
```

### Start Celery Worker (Background)
```bash
cd backend
nohup celery -A config worker -l info > /tmp/celery.log 2>&1 &
```

### Check Worker Status
```bash
celery -A config inspect active
```

### View Active Tasks
```bash
celery -A config inspect active
```

### Purge All Tasks
```bash
celery -A config purge
```

### Stop Workers Gracefully
```bash
celery -A config control shutdown
```

## Troubleshooting

### Workers Not Starting
1. Check if Redis is running: `redis-cli ping`
2. Check for port conflicts
3. Review logs: `tail -f /tmp/celery.log`

### Tasks Not Executing
1. Verify worker is running: `ps aux | grep celery`
2. Check broker connection in Flower
3. Verify task is registered: `celery -A config inspect registered`

### Database Growing Large
1. Delete old successful tasks via Django admin
2. Set up automatic cleanup:
```python
# In Django shell or management command
from django_celery_results.models import TaskResult
from datetime import timedelta
from django.utils import timezone

# Delete results older than 30 days
threshold = timezone.now() - timedelta(days=30)
TaskResult.objects.filter(
    date_created__lt=threshold,
    status='SUCCESS'
).delete()
```

## Production Deployment

### Using Supervisor (Recommended)
```ini
[program:celery_worker]
command=/path/to/venv/bin/celery -A config worker -l info
directory=/path/to/backend
user=www-data
autostart=true
autorestart=true
redirect_stderr=true
stdout_logfile=/var/log/celery/worker.log
```

### Using systemd
```ini
[Unit]
Description=Celery Worker
After=network.target

[Service]
Type=forking
User=www-data
Group=www-data
WorkingDirectory=/path/to/backend
ExecStart=/path/to/venv/bin/celery -A config worker -l info --detach
Restart=always

[Install]
WantedBy=multi-user.target
```

## Security Considerations

1. **Access Control** - Only superusers can delete task results
2. **Sensitive Data** - Avoid logging sensitive data in task arguments
3. **Flower Access** - Secure Flower with authentication in production:
   ```bash
   celery -A config flower --port=5555 --basic_auth=user:password
   ```

## Further Reading

- [Celery Documentation](https://docs.celeryproject.org/)
- [django-celery-results Documentation](https://django-celery-results.readthedocs.io/)
- [Flower Documentation](https://flower.readthedocs.io/)
