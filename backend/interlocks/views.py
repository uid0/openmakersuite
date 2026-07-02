"""API for the RFID-KeyMaster interlock integration (Phase 1, backend only).

Two surfaces on one router-mounted viewset:

* **Operator CRUD + enable/disable/status** — staff/admin only
  (:class:`~interlocks.permissions.IsStaffUser`). enable/disable set
  ``desired_state`` and enqueue a command; status enqueues a poll.
* **Pi-executor plumbing** — ``command-queue`` (returns *decrypted* SSH creds
  for pending commands and marks them claimed) and ``commands/{id}/report``
  (ingests the result + device telemetry). These are **authenticated** with a
  dedicated daemon token (never ``AllowAny``) because the queue returns
  plaintext credentials.
"""

from __future__ import annotations

from django.db import connection, transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import Interlock, InterlockCommand
from .permissions import (
    InterlockDaemonTokenAuthentication,
    IsInterlockDaemon,
    IsStaffUser,
)
from .serializers import (
    InterlockCommandReportSerializer,
    InterlockCommandSerializer,
    InterlockSerializer,
)


class InterlockViewSet(viewsets.ModelViewSet):
    """Manage interlocks and drive their enable/disable/status commands."""

    queryset = Interlock.objects.select_related("asset").all()
    serializer_class = InterlockSerializer
    permission_classes = [IsStaffUser]

    # ------------------------------------------------------------------
    # Operator actions — enqueue work for the Pi executor
    # ------------------------------------------------------------------
    def _enqueue(self, interlock, action_name, request):
        user = getattr(request, "user", None)
        return InterlockCommand.objects.create(
            interlock=interlock,
            action=action_name,
            state=InterlockCommand.STATE_PENDING,
            requested_by=user if (user and user.is_authenticated) else None,
        )

    @action(detail=True, methods=["post"])
    def enable(self, request, pk=None):
        """Set desired_state=enabled and queue an enable command."""
        interlock = self.get_object()
        interlock.desired_state = Interlock.STATE_ENABLED
        interlock.save(update_fields=["desired_state", "updated_at"])
        command = self._enqueue(interlock, InterlockCommand.ACTION_ENABLE, request)
        return Response(
            {
                "status": "enable command queued",
                "desired_state": interlock.desired_state,
                "command": InterlockCommandSerializer(command).data,
            },
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["post"])
    def disable(self, request, pk=None):
        """Set desired_state=disabled and queue a disable command."""
        interlock = self.get_object()
        interlock.desired_state = Interlock.STATE_DISABLED
        interlock.save(update_fields=["desired_state", "updated_at"])
        command = self._enqueue(interlock, InterlockCommand.ACTION_DISABLE, request)
        return Response(
            {
                "status": "disable command queued",
                "desired_state": interlock.desired_state,
                "command": InterlockCommandSerializer(command).data,
            },
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["post"], url_path="status", url_name="status")
    def poll_status(self, request, pk=None):
        """Queue a status poll (does not change desired_state)."""
        interlock = self.get_object()
        command = self._enqueue(interlock, InterlockCommand.ACTION_STATUS, request)
        return Response(
            {
                "status": "status command queued",
                "command": InterlockCommandSerializer(command).data,
            },
            status=status.HTTP_201_CREATED,
        )

    # ------------------------------------------------------------------
    # Pi-executor plumbing — SECURITY-CRITICAL, daemon-token authenticated.
    # The command-queue returns DECRYPTED SSH credentials, so unlike the
    # project-storage print-queue it is NOT AllowAny.
    # ------------------------------------------------------------------
    @action(
        detail=False,
        methods=["get"],
        url_path="command-queue",
        authentication_classes=[InterlockDaemonTokenAuthentication],
        permission_classes=[IsInterlockDaemon],
    )
    def command_queue(self, request):
        """Return pending commands with the connection payload the Pi needs.

        Each row carries the *decrypted* SSH password — this is the only path
        that decrypts it, and it requires the daemon token. Commands are marked
        ``claimed`` as they are handed out so a second poll won't re-run them.
        """
        payloads = []
        with transaction.atomic():
            pending = (
                InterlockCommand.objects.filter(state=InterlockCommand.STATE_PENDING)
                .select_related("interlock")
                .order_by("created_at")
            )
            # Row-lock so concurrent polls can't hand out the same command
            # twice. SQLite (local tests) has no row locking — skip it there;
            # Postgres (prod/CI) enforces it.
            if connection.features.has_select_for_update:
                pending = pending.select_for_update()
            now = timezone.now()
            for command in pending:
                interlock = command.interlock
                payloads.append(
                    {
                        "command_id": command.id,
                        "action": command.action,
                        "host": interlock.host,
                        "port": interlock.ssh_port,
                        "username": interlock.ssh_username,
                        "password": interlock.get_ssh_password(),
                        "auth_type": interlock.auth_type,
                        "service_name": interlock.service_name,
                        "relay_pin": interlock.relay_pin,
                        "relay_interface": interlock.relay_interface,
                    }
                )
                command.state = InterlockCommand.STATE_CLAIMED
                command.claimed_at = now
                command.save(update_fields=["state", "claimed_at"])
        return Response(payloads)

    @action(
        detail=False,
        methods=["post"],
        url_path=r"commands/(?P<command_pk>[^/.]+)/report",
        authentication_classes=[InterlockDaemonTokenAuthentication],
        permission_classes=[IsInterlockDaemon],
    )
    def report(self, request, command_pk=None):
        """Ingest the Pi's result for a command + refresh device telemetry."""
        command = get_object_or_404(InterlockCommand, pk=command_pk)
        serializer = InterlockCommandReportSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        now = timezone.now()

        success = data["success"]
        command.success = success
        command.state = InterlockCommand.STATE_DONE if success else InterlockCommand.STATE_FAILED
        command.result_text = data.get("result_text", "")
        command.completed_at = now
        command.save(update_fields=["success", "state", "result_text", "completed_at"])

        interlock = command.interlock
        update_fields = ["last_seen_at", "updated_at"]
        interlock.last_seen_at = now
        if data.get("state"):
            interlock.last_reported_state = data["state"]
            update_fields.append("last_reported_state")
        if "in_use" in data:
            interlock.in_use = data["in_use"]
            update_fields.append("in_use")
        # A successful report means the Pi reached the device; honour an
        # explicit ``online`` flag when the daemon sends one.
        interlock.online = bool(data["online"]) if "online" in data else success
        update_fields.append("online")
        interlock.save(update_fields=update_fields)

        return Response(InterlockCommandSerializer(command).data)
