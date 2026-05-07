"""Standardized API error contract (AC-7).

All DRF exceptions and explicit raises route through ``standardized_exception_handler``
and emerge with the documented shape::

    {
        "error": {
            "code": "validation_failed",
            "message": "One or more fields failed validation.",
            "details": {...}            # optional; field map, list, or scalar payload
        }
    }

Error codes are stable identifiers — frontend and integration clients may switch
on them. Messages are user-safe; details are machine-readable hints (field
errors, retry hints, dependency names, task ids).

Endpoints that need to return a standardized error from a non-exception path
should ``raise`` one of the public ``APIException`` subclasses below or build a
response with ``error_response()``.
"""

from __future__ import annotations

from typing import Any, Optional

from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import Http404

from rest_framework import status
from rest_framework.exceptions import (
    APIException,
    AuthenticationFailed,
    MethodNotAllowed,
    NotAuthenticated,
    NotFound,
    ParseError,
    PermissionDenied,
    Throttled,
    UnsupportedMediaType,
    ValidationError,
)
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

from .observability_redaction import redact as _redact_for_logs


class ErrorCode:
    """Stable machine-readable error codes returned at ``error.code``."""

    VALIDATION_FAILED = "validation_failed"
    PARSE_ERROR = "parse_error"
    NOT_AUTHENTICATED = "not_authenticated"
    AUTHENTICATION_FAILED = "authentication_failed"
    PERMISSION_DENIED = "permission_denied"
    NOT_FOUND = "not_found"
    METHOD_NOT_ALLOWED = "method_not_allowed"
    UNSUPPORTED_MEDIA_TYPE = "unsupported_media_type"
    THROTTLED = "throttled"
    DEPENDENCY_UNAVAILABLE = "dependency_unavailable"
    TASK_QUEUED = "task_queued"
    CONFLICT = "conflict"
    SERVER_ERROR = "server_error"


_DEFAULT_MESSAGES = {
    ErrorCode.VALIDATION_FAILED: "One or more fields failed validation.",
    ErrorCode.PARSE_ERROR: "Malformed request body.",
    ErrorCode.NOT_AUTHENTICATED: "Authentication is required.",
    ErrorCode.AUTHENTICATION_FAILED: "Authentication credentials were rejected.",
    ErrorCode.PERMISSION_DENIED: "You do not have permission to perform this action.",
    ErrorCode.NOT_FOUND: "The requested resource was not found.",
    ErrorCode.METHOD_NOT_ALLOWED: "Method not allowed for this endpoint.",
    ErrorCode.UNSUPPORTED_MEDIA_TYPE: "Unsupported content type.",
    ErrorCode.THROTTLED: "Request was throttled.",
    ErrorCode.DEPENDENCY_UNAVAILABLE: "A required dependency is currently unavailable.",
    ErrorCode.TASK_QUEUED: "Work has been queued for asynchronous processing.",
    ErrorCode.CONFLICT: "The request conflicts with the current state of the resource.",
    ErrorCode.SERVER_ERROR: "Internal server error.",
}


class DependencyUnavailable(APIException):
    """Raise when a downstream dependency (broker, MQTT, email, webhook target)
    is unreachable. Maps to ``503`` and ``error.code = "dependency_unavailable"``."""

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = _DEFAULT_MESSAGES[ErrorCode.DEPENDENCY_UNAVAILABLE]
    default_code = ErrorCode.DEPENDENCY_UNAVAILABLE


class TaskQueued(APIException):
    """Raise from views that hand work off to Celery and want the response to
    advertise async semantics. Maps to ``202`` and ``error.code = "task_queued"``.

    DRF treats ``APIException`` as an error, so the response status is set to
    202 Accepted but uses the standardized envelope so clients can branch on
    ``error.code == 'task_queued'`` and read ``error.details.task_id``.
    """

    status_code = status.HTTP_202_ACCEPTED
    default_detail = _DEFAULT_MESSAGES[ErrorCode.TASK_QUEUED]
    default_code = ErrorCode.TASK_QUEUED


class Conflict(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = _DEFAULT_MESSAGES[ErrorCode.CONFLICT]
    default_code = ErrorCode.CONFLICT


_STATUS_TO_CODE = {
    status.HTTP_400_BAD_REQUEST: ErrorCode.VALIDATION_FAILED,
    status.HTTP_401_UNAUTHORIZED: ErrorCode.NOT_AUTHENTICATED,
    status.HTTP_403_FORBIDDEN: ErrorCode.PERMISSION_DENIED,
    status.HTTP_404_NOT_FOUND: ErrorCode.NOT_FOUND,
    status.HTTP_405_METHOD_NOT_ALLOWED: ErrorCode.METHOD_NOT_ALLOWED,
    status.HTTP_409_CONFLICT: ErrorCode.CONFLICT,
    status.HTTP_415_UNSUPPORTED_MEDIA_TYPE: ErrorCode.UNSUPPORTED_MEDIA_TYPE,
    status.HTTP_429_TOO_MANY_REQUESTS: ErrorCode.THROTTLED,
    status.HTTP_503_SERVICE_UNAVAILABLE: ErrorCode.DEPENDENCY_UNAVAILABLE,
}


def _classify(exc: Exception, status_code: int) -> str:
    if isinstance(exc, ValidationError):
        return ErrorCode.VALIDATION_FAILED
    if isinstance(exc, ParseError):
        return ErrorCode.PARSE_ERROR
    if isinstance(exc, AuthenticationFailed):
        return ErrorCode.AUTHENTICATION_FAILED
    if isinstance(exc, NotAuthenticated):
        return ErrorCode.NOT_AUTHENTICATED
    if isinstance(exc, PermissionDenied):
        return ErrorCode.PERMISSION_DENIED
    if isinstance(exc, (NotFound, Http404)):
        return ErrorCode.NOT_FOUND
    if isinstance(exc, MethodNotAllowed):
        return ErrorCode.METHOD_NOT_ALLOWED
    if isinstance(exc, UnsupportedMediaType):
        return ErrorCode.UNSUPPORTED_MEDIA_TYPE
    if isinstance(exc, Throttled):
        return ErrorCode.THROTTLED
    if isinstance(exc, APIException):
        # Honour the exception's own ``default_code`` when it matches a known code.
        code = getattr(exc, "default_code", None)
        if code in _DEFAULT_MESSAGES:
            return code
    return _STATUS_TO_CODE.get(status_code, ErrorCode.SERVER_ERROR)


def _normalize_details(payload: Any) -> Any:
    """Strip wrapping ``{"detail": ...}`` envelopes; surface field maps and lists
    untouched so frontend clients can iterate field errors directly."""
    if isinstance(payload, dict):
        # DRF wraps simple errors as {"detail": "..."} — flatten that out.
        if set(payload.keys()) == {"detail"}:
            inner = payload["detail"]
            return None if isinstance(inner, str) else inner
        return payload
    return payload


def _humanize(payload: Any, fallback: str) -> str:
    """Pick a single user-safe message string from a DRF error payload."""
    if isinstance(payload, dict):
        if "detail" in payload and isinstance(payload["detail"], str):
            return payload["detail"]
        # Otherwise this is a field-error map; the fallback (default code message)
        # is more useful than concatenating every field.
        return fallback
    if isinstance(payload, list):
        if payload and isinstance(payload[0], str):
            return str(payload[0])
        return fallback
    if isinstance(payload, str):
        return payload
    return fallback


def standardized_exception_handler(exc, context):
    """DRF EXCEPTION_HANDLER — wraps the default handler with a consistent
    envelope. Returns ``None`` for unhandled exceptions so DRF / Django can
    surface them as 500s through their normal middleware path."""
    # Translate Django's ValidationError into DRF's so it picks up the same
    # envelope rather than escaping as an unhandled 500.
    if isinstance(exc, DjangoValidationError) and not isinstance(exc, ValidationError):
        exc = ValidationError(detail=getattr(exc, "message_dict", None) or exc.messages)

    response = drf_exception_handler(exc, context)
    if response is None:
        return None

    code = _classify(exc, response.status_code)
    fallback = _DEFAULT_MESSAGES.get(code, _DEFAULT_MESSAGES[ErrorCode.SERVER_ERROR])
    message = _humanize(response.data, fallback)
    details = _normalize_details(response.data)

    # For Throttled, DRF's default already includes "Expected available in N seconds".
    # Surface the wait hint in details so clients don't have to parse the message.
    if isinstance(exc, Throttled) and exc.wait is not None:
        details = {"retry_after_seconds": int(exc.wait)}

    body: dict[str, Any] = {"code": code, "message": message}
    if details is not None and details != message:
        # Scrub sensitive keys and value-shape secrets from the details
        # payload before they leave the process. AC-27: error envelopes
        # are an observability surface; details can carry user-supplied
        # field maps (e.g. validation errors over a registration form
        # that includes a password field). Redact before serialization.
        body["details"] = _redact_for_logs(details)

    response.data = {"error": body}
    return response


def error_response(
    code: str,
    message: Optional[str] = None,
    details: Any = None,
    status_code: int = status.HTTP_400_BAD_REQUEST,
) -> Response:
    """Build a standardized error response without raising. Useful from views
    that compose multi-step results (e.g. bulk imports) and want to emit a
    single envelope at the end."""
    body: dict[str, Any] = {
        "code": code,
        "message": message
        or _DEFAULT_MESSAGES.get(code, _DEFAULT_MESSAGES[ErrorCode.SERVER_ERROR]),
    }
    if details is not None:
        body["details"] = details
    return Response({"error": body}, status=status_code)
