from django.apps import AppConfig


class StorageVisionConfig(AppConfig):
    """Marker-assisted supply-area monitoring (.criteria/storage-vision-supply-reorder.md).

    Phase 1 of the rollout: model layer only. Views, serializers, URL
    routing, Celery processing, and the Facilities UI land in
    follow-up PRs.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "storage_vision"
    verbose_name = "Storage Vision"
