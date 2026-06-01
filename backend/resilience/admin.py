from django.contrib import admin

from .models import CircuitBreakerEvent


@admin.register(CircuitBreakerEvent)
class CircuitBreakerEventAdmin(admin.ModelAdmin):
    list_display = ["name", "from_state", "to_state", "created_at", "detail"]
    list_filter = ["name", "to_state", "created_at"]
    search_fields = ["name", "detail"]
    readonly_fields = ["name", "from_state", "to_state", "detail", "created_at"]

    def has_add_permission(self, request):
        return False
