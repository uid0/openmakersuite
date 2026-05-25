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
// *.ingest.sentry.io / sentry.io / sentry-cdn.com (nginx template).
const sentryDsn = import.meta.env.VITE_SENTRY_DSN;
if (sentryDsn) {
  Sentry.init({
    dsn: sentryDsn,
    environment: import.meta.env.MODE,
    release: import.meta.env.VITE_GIT_HASH || undefined,
    integrations: [Sentry.browserTracingIntegration()],
    tracesSampleRate: 0.1,
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
