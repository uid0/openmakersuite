import hashlib
import logging

from django.apps import AppConfig
from django.conf import settings

logger = logging.getLogger(__name__)


def _token_fingerprint(token: str) -> str:
    if not token:
        return ""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:8]


class ForgekeyConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "forgekey"
    verbose_name = "ForgeKey - ESP32 Device Management"

    def ready(self) -> None:
        # Imported for side effects: system checks + indicator-sync signals.
        from . import checks  # noqa: F401
        from . import signals  # noqa: F401

        token = (getattr(settings, "FORGEKEY_PROVISIONING_TOKEN", "") or "").strip()
        if token:
            logger.info(
                "ForgeKey provisioning token configured (length=%d, fingerprint=%s)",
                len(token),
                _token_fingerprint(token),
            )
        else:
            logger.warning(
                "ForgeKey provisioning token NOT configured "
                "(FORGEKEY_PROVISIONING_TOKEN is empty); device registration "
                "will return 401 server_unconfigured for every request."
            )
