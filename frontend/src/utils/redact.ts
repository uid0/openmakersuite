/**
 * Frontend-side observability redactor (gh #378).
 *
 * Mirrors `backend/config/observability_redaction.py` so the same
 * value-shape patterns scrub Highlight payloads before they leave the
 * browser. Only the value-shape pass is implemented here — the
 * key-name pass is handled by Highlight's own
 * `networkHeadersToRedact` / `networkBodyKeysToRedact` configuration,
 * which is wired in `initHighlight` and is more efficient than
 * iterating every dict ourselves.
 *
 * Patterns covered:
 *  - `Bearer <token>` (auth header values)
 *  - JWTs (`eyJ...` triple-segment base64url)
 *  - PEM private key blocks
 *  - High-entropy hex/base64 tokens (32+ chars, word-boundary matched)
 */

const REDACTED = '[REDACTED]';

const VALUE_PATTERNS: RegExp[] = [
  /Bearer\s+[A-Za-z0-9\-._~+/]{20,}=*/gi,
  /\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b/g,
  /-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----/g,
  /\b[A-Za-z0-9+/]{32,}={0,2}\b/g,
];

export function redactString(value: string): string {
  let out = value;
  for (const pattern of VALUE_PATTERNS) {
    out = out.replace(pattern, REDACTED);
  }
  return out;
}

/**
 * Wrap an Error so its `message` and `stack` are scrubbed before
 * downstream code (Highlight, console) reads them. Returns a new
 * Error whose `.name` is preserved.
 */
export function redactError(error: Error): Error {
  const scrubbed = new Error(redactString(error.message ?? ''));
  scrubbed.name = error.name;
  if (error.stack) {
    scrubbed.stack = redactString(error.stack);
  }
  return scrubbed;
}
