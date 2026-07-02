"""Admin for interlocks.

The SSH password is never rendered in plaintext (mirrors ``bms.admin``): the
change form shows only a ciphertext summary and offers a write-only
``new_ssh_password`` field to set/replace it.
"""

from __future__ import annotations

from django import forms
from django.contrib import admin

from .models import Interlock, InterlockCommand


class InterlockAdminForm(forms.ModelForm):
    new_ssh_password = forms.CharField(
        required=False,
        widget=forms.PasswordInput(render_value=False),
        help_text="Set/replace the SSH password. Leave blank to keep the current one.",
    )

    class Meta:
        model = Interlock
        # The raw ciphertext is never edited directly; it's managed via
        # ``new_ssh_password`` + the model's set_ssh_password().
        exclude = ["encrypted_ssh_password"]

    def save(self, commit=True):
        instance = super().save(commit=False)
        password = self.cleaned_data.get("new_ssh_password")
        if password:
            instance.set_ssh_password(password)
        if commit:
            instance.save()
            self.save_m2m()
        return instance


@admin.register(Interlock)
class InterlockAdmin(admin.ModelAdmin):
    form = InterlockAdminForm
    list_display = [
        "label",
        "host",
        "asset",
        "desired_state",
        "last_reported_state",
        "online",
        "in_use",
        "has_credentials",
        "updated_at",
    ]
    list_filter = ["desired_state", "online", "auth_type", "relay_interface"]
    search_fields = ["label", "host"]
    raw_id_fields = ["asset"]
    readonly_fields = [
        "credentials_summary",
        "last_reported_state",
        "in_use",
        "online",
        "last_seen_at",
        "created_at",
        "updated_at",
    ]
    fields = [
        "label",
        "asset",
        "host",
        "ssh_port",
        "ssh_username",
        "auth_type",
        "credentials_summary",
        "new_ssh_password",
        "service_name",
        "relay_pin",
        "relay_interface",
        "desired_state",
        "last_reported_state",
        "in_use",
        "online",
        "last_seen_at",
        "created_at",
        "updated_at",
    ]

    @admin.display(boolean=True, description="Has credentials")
    def has_credentials(self, obj):
        return obj.has_credentials

    @admin.display(description="SSH password")
    def credentials_summary(self, obj):
        blob = bytes(obj.encrypted_ssh_password or b"")
        if not blob:
            return "(none set)"
        return f"present ({len(blob)} bytes ciphertext)"


@admin.register(InterlockCommand)
class InterlockCommandAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "interlock",
        "action",
        "state",
        "success",
        "requested_by",
        "created_at",
        "claimed_at",
        "completed_at",
    ]
    list_filter = ["action", "state", "success"]
    search_fields = ["interlock__label", "interlock__host"]
    raw_id_fields = ["interlock", "requested_by"]
    readonly_fields = [
        "interlock",
        "action",
        "state",
        "requested_by",
        "created_at",
        "claimed_at",
        "completed_at",
        "result_text",
        "success",
    ]
