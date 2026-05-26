import { MantineProvider } from '@mantine/core';
import '@mantine/core/styles.css';
import '@mantine/dates/styles.css';
import '@mantine/dropzone/styles.css';
import { ModalsProvider } from '@mantine/modals';
import { Notifications } from '@mantine/notifications';
import '@mantine/notifications/styles.css';
import * as Sentry from '@sentry/react';
import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import { NotificationProvider } from './contexts/NotificationContext';
import './styles/index.css';

// Sentry — no-op when VITE_SENTRY_DSN is unset. CSP already allows
// *.ingest.sentry.io / sentry.io / sentry-cdn.com (nginx template);
// replay also uses `worker-src 'self' blob:` which is already allowed.
const sentryDsn = import.meta.env.VITE_SENTRY_DSN;
if (sentryDsn) {
  Sentry.init({
    dsn: sentryDsn,
    environment: import.meta.env.MODE,
    release: import.meta.env.VITE_GIT_HASH || undefined,
    integrations: [
      Sentry.browserTracingIntegration(),
      // Session Replay: maskAllText / blockAllMedia are off so the replay
      // is fully visible — this is an internal makerspace tool, not a
      // multi-tenant SaaS, so we accept the privacy trade-off in exchange
      // for actionable repros. Re-enable masking if a member-facing
      // surface (donor form, payment) ever serves replays.
      Sentry.replayIntegration({
        maskAllText: false,
        blockAllMedia: false,
      }),
    ],
    tracesSampleRate: 0.1,
    // Replay every session and every error. Storage cost on a self-hosted
    // Sentry is tractable at this org's traffic; revisit if SeaweedFS fills up.
    replaysSessionSampleRate: 1.0,
    replaysOnErrorSampleRate: 1.0,
    // Don't ship full request bodies; explicit logger.error / captureException
    // calls in app code carry the curated context.
    sendDefaultPii: false,
  });
}

const root = ReactDOM.createRoot(
  document.getElementById('root') as HTMLElement
);

root.render(
  <React.StrictMode>
    <MantineProvider>
      <ModalsProvider>
        <NotificationProvider>
          <Notifications />
          <App />
        </NotificationProvider>
      </ModalsProvider>
    </MantineProvider>
  </React.StrictMode>
);
