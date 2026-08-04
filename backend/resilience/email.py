"""Outbound email routed through the ``email`` circuit breaker.

Every outbound message in the codebase goes through one of the two helpers
here so a dead mail provider fails *fast* and *visibly* instead of costing
every notification path a full provider timeout and a swallowed exception.
The breaker is registered as the "Email delivery" service in
:mod:`resilience.services`, so an outage shows up in
``GET /api/resilience/status/`` rather than only in Sentry.

Usage — replace ``message.send(...)`` / ``send_mail(...)``::

    from resilience.email import breakered_send_mail, send_breakered_email

    send_breakered_email(message, fail_silently=False)
    breakered_send_mail(subject, body, None, recipients, fail_silently=True)

Why the breaker is tuned differently from the default
-----------------------------------------------------
The failure this exists for is a bad/expired ``POSTMARK_SERVER_TOKEN`` — a
401 that is *persistent*, not transient. The circuit default
``reset_timeout=30`` would re-probe the provider twice a minute forever; the
"email" breaker uses 300s (settings-overridable) so a broken credential backs
off properly instead of being hammered.

Which exception an open breaker raises
--------------------------------------
Mirrors ``resilience.circuit.breakered_request``, which translates
``CircuitBreakerOpen`` into ``requests.exceptions.ConnectionError`` so that
existing ``except`` blocks keep behaving identically. For email the two real
failure families are:

* ``anymail.exceptions.AnymailRequestsAPIError`` (Postmark, prod) —
  ``AnymailError`` -> ``requests.HTTPError`` -> ``RequestException`` ->
  ``OSError``. It is **not** an ``smtplib`` error, despite the surface
  similarity.
* ``smtplib.SMTPException`` (Django's SMTP backend) -> ``OSError``.

Their only common ancestor is ``OSError``. :class:`EmailCircuitOpen`
subclasses ``smtplib.SMTPException`` — and therefore ``OSError`` — which puts
it inside that intersection while still naming itself honestly. No current
call site narrows on an email exception type at all (they catch bare
``Exception``, use ``fail_silently=True``, or let it propagate), so this is
behavior-preserving everywhere today, and a future ``except OSError`` handler
catches an open breaker for free.
"""

from __future__ import annotations

import logging
import smtplib

from django.conf import settings

from .circuit import CircuitBreakerOpen, get_breaker

logger = logging.getLogger(__name__)

#: The single breaker every outbound message routes through.
BREAKER_NAME = "email"

#: Consecutive failures inside the breaker's window before it opens.
DEFAULT_FAIL_MAX = 5

#: Seconds an open email breaker waits before letting one trial send through.
#: Deliberately much longer than the circuit default (30s) — see the module
#: docstring.
DEFAULT_RESET_TIMEOUT = 300.0


class EmailCircuitOpen(smtplib.SMTPException):
    """Raised instead of calling the provider while the breaker is open.

    An ``OSError`` subclass, like both real mail-failure families.
    """


def email_breaker():
    """The shared "email" breaker, tuned from settings."""
    return get_breaker(
        BREAKER_NAME,
        fail_max=getattr(settings, "CIRCUIT_BREAKER_EMAIL_FAIL_MAX", DEFAULT_FAIL_MAX),
        reset_timeout=getattr(
            settings, "CIRCUIT_BREAKER_EMAIL_RESET_TIMEOUT", DEFAULT_RESET_TIMEOUT
        ),
    )


def _recipient_count(recipients) -> str:
    """Recipient count for the log line, never raising — ``recipients`` can be
    anything a caller (or a test double) passed in."""
    try:
        return str(len(recipients or []))
    except TypeError:
        return "?"


def _rejected(exc: CircuitBreakerOpen, fail_silently: bool, recipients) -> int:
    """Common open-breaker path: swallow or translate.

    ``fail_silently=True`` callers asked never to see mail errors, so an open
    breaker returns 0 ("nothing sent") exactly as Django's backends do when
    they swallow a real provider error.
    """
    logger.warning(
        "Email circuit is open; skipped a message to %s recipient(s) without "
        "calling the provider",
        _recipient_count(recipients),
    )
    if fail_silently:
        return 0
    raise EmailCircuitOpen(str(exc)) from exc


def send_breakered_email(message, *, fail_silently: bool = False) -> int:
    """``message.send()`` routed through the "email" breaker.

    Drop-in for ``EmailMessage.send`` / ``EmailMultiAlternatives.send``:
    returns the number of messages sent.

    Note that ``fail_silently=True`` sends cannot *trip* the breaker — Django's
    backends swallow the provider error internally, so the breaker only ever
    sees a successful return. They still benefit from an open breaker (they
    fail fast instead of waiting out a provider timeout); the trip itself comes
    from the ``fail_silently=False`` sites.
    """
    try:
        return email_breaker().call(message.send, fail_silently=fail_silently)
    except CircuitBreakerOpen as exc:
        return _rejected(exc, fail_silently, getattr(message, "to", None))


def breakered_send_mail(
    subject,
    body,
    from_email,
    recipient_list,
    fail_silently: bool = False,
    **kwargs,
) -> int:
    """``django.core.mail.send_mail`` routed through the "email" breaker.

    Drop-in for ``send_mail`` (``body`` is Django's ``message`` argument);
    remaining keyword arguments — ``html_message``, ``connection``,
    ``auth_user`` / ``auth_password`` — pass straight through.
    """
    from django.core.mail import send_mail

    try:
        return email_breaker().call(
            send_mail,
            subject,
            body,
            from_email,
            recipient_list,
            fail_silently=fail_silently,
            **kwargs,
        )
    except CircuitBreakerOpen as exc:
        return _rejected(exc, fail_silently, recipient_list)


__all__ = [
    "BREAKER_NAME",
    "EmailCircuitOpen",
    "breakered_send_mail",
    "email_breaker",
    "send_breakered_email",
]
