import { H, HighlightOptions } from 'highlight.run';
import { redactString } from './redact';

export interface HighlightEnvConfig {
  projectId?: string;
  otlpEndpoint?: string;
  environment?: string;
  release?: string;
  nodeEnv?: string;
}

export interface HighlightLogger {
  info: (msg: string) => void;
  warn: (msg: string) => void;
}

export function readHighlightEnv(env: NodeJS.ProcessEnv = process.env): HighlightEnvConfig {
  return {
    projectId: env.REACT_APP_HIGHLIGHT_PROJECT_ID,
    otlpEndpoint: env.REACT_APP_HIGHLIGHT_OTLP_ENDPOINT,
    environment: env.REACT_APP_HIGHLIGHT_ENVIRONMENT,
    release: env.REACT_APP_GIT_HASH,
    nodeEnv: env.NODE_ENV,
  };
}

// Initialize Highlight from env config. Returns true if initialized, false if
// skipped (project id missing). Always emits a single INFO or WARN log so a
// missing or misconfigured project id is diagnosable from a deployed build's
// console — same diagnostic contract as the prior Sentry init (oms-78t).
export function initHighlight(
  config: HighlightEnvConfig = readHighlightEnv(),
  init: typeof H.init = H.init,
  logger: HighlightLogger = console,
  consumeError: typeof H.consumeError = H.consumeError,
): boolean {
  const { projectId, otlpEndpoint, environment, release, nodeEnv } = config;

  if (!projectId) {
    logger.warn('[Highlight] PROJECT_ID missing — session replay & error reporting disabled');
    return false;
  }

  const env = environment || nodeEnv || 'development';
  const options: HighlightOptions = {
    environment: env,
    version: release || undefined,
    serviceName: 'oms-frontend',
    backendUrl: "https://highlighter.openmakersuite.net/public",
    tracingOrigins: true,
    // gh #378: header + body redaction for the network recording surface.
    // Mirrors the backend's `_KEY_PATTERNS` in
    // `config/observability_redaction.py` so the same secret-shaped
    // names are scrubbed in both directions. The
    // `requestResponseSanitizer` runs the value-shape pass over any
    // remaining string content as a safety net for headers/bodies the
    // key-name lists don't cover.
    networkRecording: {
      enabled: true,
      recordHeadersAndBody: true,
      networkHeadersToRedact: [
        'Authorization',
        'X-Authorization',
        'Cookie',
        'Set-Cookie',
        'X-CSRFToken',
        'X-CSRF-Token',
        'X-API-Key',
        'X-Api-Key',
        'X-Session-Key',
        'X-Webhook-Signature',
        'X-Forgekey-Token',
      ],
      networkBodyKeysToRedact: [
        'token',
        'access_token',
        'refresh_token',
        'id_token',
        'csrf',
        'csrf_token',
        'csrfmiddlewaretoken',
        'password',
        'passwd',
        'pwd',
        'old_password',
        'new_password',
        'secret',
        'api_key',
        'api-key',
        'apiKey',
        'signing_key',
        'private_key',
        'session_key',
        'bearer',
        'authorization',
        'signature',
      ],
      requestResponseSanitizer: (pair) => {
        if (pair?.request?.body && typeof pair.request.body === 'string') {
          pair.request.body = redactString(pair.request.body);
        }
        if (pair?.response?.body && typeof pair.response.body === 'string') {
          pair.response.body = redactString(pair.response.body);
        }
        return pair;
      },
    },
  };
  if (otlpEndpoint) {
    options.otlpEndpoint = otlpEndpoint;
    options.backendUrl = otlpEndpoint;
  }

  init(projectId, options);

  logger.info(
    `[Highlight] initialized (env=${env}, release=${release || 'unknown'}, project=${projectIdFingerprint(projectId)})`,
  );

  // Boot ping (oms-78t): emit a single non-fatal error on init so a deployed
  // build that *thinks* it initialized but is silently rejected by the
  // collector still leaves a trail in the dashboard. If consumeError throws
  // (e.g. SDK not yet ready), don't take the app down for it.
  try {
    consumeError(new Error('OMS frontend boot ping'), 'boot', {
      release: release || 'unknown',
      environment: env,
    });
  } catch (e) {
    logger.warn(`[Highlight] boot ping failed: ${(e as Error).message}`);
  }

  return true;
}

// First 8 chars of the project id, for diagnostic logging without revealing
// the full id in console output.
export function projectIdFingerprint(projectId: string): string {
  return projectId.slice(0, 8);
}
