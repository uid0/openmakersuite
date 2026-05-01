import { MantineProvider } from '@mantine/core';
import '@mantine/core/styles.css';
import '@mantine/dates/styles.css';
import '@mantine/dropzone/styles.css';
import { ModalsProvider } from '@mantine/modals';
import { Notifications } from '@mantine/notifications';
import '@mantine/notifications/styles.css';
import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import { NotificationProvider } from './contexts/NotificationContext';
import './styles/index.css';
import { initSentry } from './utils/sentry';

initSentry();

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
