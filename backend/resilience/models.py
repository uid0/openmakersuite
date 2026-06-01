from django.db import models


class CircuitBreakerEvent(models.Model):
    """Audit row for a circuit-breaker state transition (open / half-open /
    close). Written by ``resilience.circuit.CircuitBreaker`` on every
    transition so trips are visible in-app (and in Sentry)."""

    name = models.CharField(max_length=100, db_index=True)
    from_state = models.CharField(max_length=20)
    to_state = models.CharField(max_length=20)
    detail = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["name", "-created_at"])]

    def __str__(self) -> str:
        return f"{self.name}: {self.from_state} -> {self.to_state}"
