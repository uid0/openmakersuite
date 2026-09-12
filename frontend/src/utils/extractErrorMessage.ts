/**
 * Pull a user-safe error message off any axios error / arbitrary thrown value.
 *
 * The OMS backend emits errors in two shapes during the migration to the
 * standardized envelope (gh #330):
 *
 *   1. **Envelope** — DRF exception handler + ``config.api_errors.error_response()``
 *      ``{"error": {"code": "...", "message": "...", "details": {...}}}``
 *
 *   2. **Legacy detail** — direct ``Response({"detail": "..."}, status=4xx)``
 *      ``{"detail": "..."}``
 *
 * This helper preserves the envelope-first preference so once a backend
 * site is converted to ``error_response`` the UI surfaces the canonical
 * code/message without any change at the call site. The legacy ``detail``
 * branch keeps existing untouched endpoints displaying the same toast they
 * always did.
 *
 * Always supply a ``fallback``: it covers the wholly unexpected shape
 * (network error, CORS preflight failure, browser cancel, malformed JSON)
 * where neither shape is present.
 */
export function extractErrorMessage(err: unknown, fallback: string): string {
  // Axios shape: ``err.response.data``. Also handle a bare ``data`` payload
  // for code paths that already destructured the response.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const anyErr = err as any;
  const data = anyErr?.response?.data ?? anyErr?.data ?? anyErr;

  if (data && typeof data === "object") {
    // Envelope shape (preferred — once #330 conversion lands).
    const envelope = data.error;
    if (envelope && typeof envelope === "object") {
      const message = envelope.message;
      if (typeof message === "string" && message.trim() !== "") {
        return message;
      }
    }

    // Legacy ``detail`` field. DRF's default ValidationError handler
    // sometimes ships ``detail`` as a string and sometimes as a list of
    // field errors; prefer the first non-empty string.
    const detail = data.detail;
    if (typeof detail === "string" && detail.trim() !== "") {
      return detail;
    }
    if (Array.isArray(detail) && typeof detail[0] === "string" && detail[0].trim() !== "") {
      return detail[0];
    }

    // ``data.error`` might be a bare string in older endpoints (we encode
    // ``Response({"error": "..."})`` literally in a few places). Surface
    // that before falling back.
    if (typeof data.error === "string" && data.error.trim() !== "") {
      return data.error;
    }
  }

  return fallback;
}


/**
 * Pull the machine-readable error CODE off the same shapes.
 *
 * The companion to ``extractErrorMessage``: the message is what the operator
 * reads, the code is what the UI branches on ("this refusal came with a
 * choice set", "this one means the kiosk is unconfigured, not offline").
 *
 * Envelope first (``error.code``), then the flat ``code`` a handful of
 * endpoints still write beside a bare-string ``error``. Both branches exist
 * for the same reason the message helper's do — the #330 conversion is
 * endpoint-by-endpoint, and a page must keep working either side of it.
 *
 * Returns ``undefined`` rather than a fallback string: "no code" is a real
 * answer callers branch on, and inventing one would make an unconverted
 * endpoint look like a coded refusal it never made.
 */
export function extractErrorCode(err: unknown): string | undefined {
  const anyErr = err as any;
  const data = anyErr?.response?.data ?? anyErr?.data ?? anyErr;
  if (!data || typeof data !== 'object') {
    return undefined;
  }

  const envelope = data.error;
  if (envelope && typeof envelope === 'object' && typeof envelope.code === 'string') {
    return envelope.code.trim() || undefined;
  }

  if (typeof data.code === 'string') {
    return data.code.trim() || undefined;
  }

  return undefined;
}

/**
 * The envelope's ``error.details`` payload — the machine-readable hints that
 * ride alongside a coded refusal (the add-line endpoint's ambiguity candidate
 * set, for one). Anything that is not an object is reported as absent.
 */
export function extractErrorDetails(err: unknown): Record<string, unknown> | undefined {
  const anyErr = err as any;
  const data = anyErr?.response?.data ?? anyErr?.data ?? anyErr;
  const details = data && typeof data === 'object' ? data.error?.details : undefined;
  return details && typeof details === 'object' && !Array.isArray(details) ? details : undefined;
}

export default extractErrorMessage;
