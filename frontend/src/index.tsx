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
      // Floating "Report a Bug" widget. When a member files a report, the
      // SDK auto-links the active Session Replay so uid0 can watch what the
      // member did right before clicking. autoInject:true renders the
      // default trigger button bottom-right; per-page customization can use
      // Sentry.getFeedback()?.attachTo(el) later.
      Sentry.feedbackIntegration({
        autoInject: true,
        showBranding: false,
        colorScheme: 'system',
        enableScreenshot: true,
        triggerLabel: 'Report a Bug',
        formTitle: 'Report a Bug',
        submitButtonLabel: 'Send Report',
        messagePlaceholder:
          "What happened? Steps to reproduce help — uid0 will see your screen recording from the last minute.",
        successMessageText: 'Thanks — your report and session replay are on their way to uid0.',
      }),
    ],
    // 0.5 = 50% of page navigations and SPA route changes emit a full
    // transaction. At this org's traffic that's well within self-hosted
    // Sentry's headroom and gives uid0 real coverage of slow renders +
    // axios call timing instead of a 10% sample.
    tracesSampleRate: 0.5,
    // Replay every session and every error. Storage cost on a self-hosted
    // Sentry is tractable at this org's traffic; revisit if SeaweedFS fills up.
    replaysSessionSampleRate: 1.0,
    replaysOnErrorSampleRate: 1.0,
    // Opt into the Logs beta — Sentry.logger.{info,warn,error}(...) ships
    // structured log entries (searchable, no alerting) alongside events.
    // Self-hosted instance has ourlogs-enabled, ourlogs-ingestion, and
    // ourlogs-stats turned on for the `sentry` org.
    enableLogs: true,
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
