import * as Sentry from '@sentry/react';

export interface SentryEnvConfig {
  dsn?: string;
  environment?: string;
  release?: string;
  nodeEnv?: string;
}

export interface SentryLogger {
  info: (msg: string) => void;
  warn: (msg: string) => void;
}

export function readSentryEnv(env: NodeJS.ProcessEnv = process.env): SentryEnvConfig {
  return {
    dsn: env.REACT_APP_SENTRY_DSN,
    environment: env.REACT_APP_SENTRY_ENVIRONMENT,
    release: env.REACT_APP_GIT_HASH,
    nodeEnv: env.NODE_ENV,
  };
}

// Initialize Sentry from env config. Returns true if initialized, false if
// skipped (DSN missing). Always emits a single INFO or WARN log so a missing
// or misconfigured DSN is diagnosable from a deployed build's console.
export function initSentry(
  config: SentryEnvConfig = readSentryEnv(),
  init: typeof Sentry.init = Sentry.init,
  logger: SentryLogger = console,
): boolean {
  const { dsn, environment, release, nodeEnv } = config;

  if (!dsn) {
    logger.warn('[Sentry] DSN missing — exception reporting disabled');
    return false;
  }

  const env = environment || nodeEnv || 'development';

  init({
    dsn,
    environment: env,
    release: release || undefined,
    integrations: [
      new Sentry.BrowserTracing(),
      new Sentry.Replay({
        maskAllText: false,
        blockAllMedia: false,
      }),
    ],
    tracesSampleRate: nodeEnv === 'production' ? 0.1 : 1.0,
    replaysSessionSampleRate: 0.1,
    replaysOnErrorSampleRate: 1.0,
  });

  logger.info(
    `[Sentry] initialized (env=${env}, release=${release || 'unknown'}, dsn-fingerprint=${dsnFingerprint(dsn)})`,
  );
  return true;
}

// First 8 chars of the DSN's public key. The public key is the segment between
// `://` and `@` in `https://<publicKey>@host/projectId`. Falls back to first 8
// chars of the raw DSN if the format is unrecognized.
export function dsnFingerprint(dsn: string): string {
  const match = dsn.match(/:\/\/([^@]+)@/);
  const key = match ? match[1] : dsn;
  return key.slice(0, 8);
}
