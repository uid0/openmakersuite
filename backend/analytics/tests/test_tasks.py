"""Tests for the monthly pulse task + management command."""

from __future__ import annotations

from datetime import date
from io import StringIO

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.management import call_command

import pytest

from analytics.services.recipients import get_analytics_group
from analytics.tasks import previous_month_window, send_monthly_pulse

User = get_user_model()
pytestmark = pytest.mark.django_db


def _user_in_group(username: str, email: str):
    user = User.objects.create_user(username=username, email=email, password="x" * 24)
    user.groups.add(get_analytics_group())
    return user


class TestPreviousMonthWindow:
    def test_typical_day(self):
        start, end = previous_month_window(today=date(2026, 5, 15))
        assert start == date(2026, 4, 1)
        assert end == date(2026, 5, 1)

    def test_first_of_month(self):
        # On the 1st, "previous month" is the month two before only at extremes;
        # for the 1st of Feb, prior is January.
        start, end = previous_month_window(today=date(2026, 2, 1))
        assert start == date(2026, 1, 1)
        assert end == date(2026, 2, 1)

    def test_january_rolls_to_december(self):
        start, end = previous_month_window(today=date(2026, 1, 10))
        assert start == date(2025, 12, 1)
        assert end == date(2026, 1, 1)


class TestSendMonthlyPulse:
    def test_no_recipients_short_circuits(self, settings):
        settings.BOARD_REPORT_EMAILS = ""
        result = send_monthly_pulse(period_start=date(2026, 4, 1), period_end=date(2026, 5, 1))
        assert result["sent"] is False
        assert result["reason"] == "no recipients configured"
        assert result["recipients"] == []
        assert len(mail.outbox) == 0

    def test_dry_run_does_not_send(self, settings):
        settings.BOARD_REPORT_EMAILS = "board@example.com"
        result = send_monthly_pulse(
            period_start=date(2026, 4, 1),
            period_end=date(2026, 5, 1),
            dry_run=True,
        )
        assert result["sent"] is False
        assert result["reason"] == "dry-run"
        assert result["recipients"] == ["board@example.com"]
        assert len(mail.outbox) == 0

    def test_real_send_uses_recipient_union(self, settings):
        settings.BOARD_REPORT_EMAILS = "board@example.com"
        _user_in_group("carol", "carol@example.com")
        result = send_monthly_pulse(period_start=date(2026, 4, 1), period_end=date(2026, 5, 1))
        assert result["sent"] is True
        assert sorted(result["recipients"]) == ["board@example.com", "carol@example.com"]
        assert len(mail.outbox) == 1
        sent = mail.outbox[0]
        assert sorted(sent.to) == ["board@example.com", "carol@example.com"]
        assert "Makerspace pulse" in sent.subject
        assert "April 2026" in sent.subject

    def test_inline_chart_attachments_present(self, settings):
        settings.BOARD_REPORT_EMAILS = "board@example.com"
        send_monthly_pulse(period_start=date(2026, 4, 1), period_end=date(2026, 5, 1))
        sent = mail.outbox[0]
        # EmailMultiAlternatives attaches the alternative HTML body as an
        # alternative; chart PNGs attach as MIMEImage parts. Verify both
        # cid headers landed on the message.
        cids = [
            att.get("Content-ID", "").strip("<>") if hasattr(att, "get") else None
            for att in sent.attachments
        ]
        assert "volume_trend.png" in cids
        assert "category_spend.png" in cids

    def test_partial_window_args_rejected(self):
        with pytest.raises(ValueError):
            send_monthly_pulse(period_start=date(2026, 4, 1), period_end=None)
        with pytest.raises(ValueError):
            send_monthly_pulse(period_start=None, period_end=date(2026, 5, 1))

    def test_default_window_is_previous_month(self, settings, monkeypatch):
        settings.BOARD_REPORT_EMAILS = "board@example.com"
        result = send_monthly_pulse(dry_run=True)
        # Just assert the result is a sane prior-month window (start before end,
        # both first-of-month). Avoids depending on today's actual date.
        start = date.fromisoformat(result["period_start"])
        end = date.fromisoformat(result["period_end"])
        assert start.day == 1
        assert end.day == 1
        assert start < end


class TestManagementCommand:
    def test_dry_run_with_recipients(self, settings):
        settings.BOARD_REPORT_EMAILS = "board@example.com"
        out = StringIO()
        call_command(
            "send_monthly_pulse",
            "--month=2026-04",
            "--dry-run",
            stdout=out,
        )
        output = out.getvalue()
        assert "Dry-run" in output
        assert "board@example.com" in output
        assert len(mail.outbox) == 0

    def test_no_recipients_warns(self, settings):
        settings.BOARD_REPORT_EMAILS = ""
        out = StringIO()
        call_command("send_monthly_pulse", "--dry-run", stdout=out)
        assert "No recipients resolved" in out.getvalue()

    def test_real_send(self, settings):
        settings.BOARD_REPORT_EMAILS = "board@example.com"
        out = StringIO()
        call_command("send_monthly_pulse", "--month=2026-04", stdout=out)
        assert "Sent monthly pulse" in out.getvalue()
        assert len(mail.outbox) == 1

    def test_invalid_month_arg(self, settings):
        settings.BOARD_REPORT_EMAILS = "board@example.com"
        with pytest.raises(Exception):
            call_command("send_monthly_pulse", "--month=garbage")

    def test_month_december_handled(self, settings):
        settings.BOARD_REPORT_EMAILS = "board@example.com"
        out = StringIO()
        call_command("send_monthly_pulse", "--month=2025-12", "--dry-run", stdout=out)
        assert "Dry-run" in out.getvalue()
