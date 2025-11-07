"""
 *
 * @file            backend/config/apps.py
 * @description     App configuration for the config module.
 * @author          Ian Wilson <me@ianwilson.org>
 * @createTime      2025-11-07 04:46:27
 * @lastModified    2025-11-07 04:46:27
 * Copyright ©Ian Wilson All rights reserved.
 *
 *  This program is free software: you can redistribute it and/or modify
 *  it under the terms of the GNU Affero General Public License as
 *  published by the Free Software Foundation, either version 3 of the
 *  License, or (at your option) any later version.
 *
App configuration for the config module.
"""

from django.apps import AppConfig


class ConfigConfig(AppConfig):
    """Configuration app settings."""

    name = "config"
    verbose_name = "Configuration"

    def ready(self):
        """Import custom admin configurations when Django starts."""
        # Import celery admin customizations
        try:
            import config.celery_admin  # noqa: F401
        except ImportError:
            pass
