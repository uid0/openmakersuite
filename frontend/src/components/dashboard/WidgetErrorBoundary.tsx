/**
 * Per-widget error boundary.
 *
 * The dashboard renders each widget independently. A single widget that throws
 * during render — e.g. React #31 when an unexpected error-object shape reaches
 * JSX, or any other downstream render bug — must degrade to a compact
 * "couldn't load" card for THAT widget only. Without this, the error bubbles to
 * the app-level ErrorBoundary in App.tsx and blanks the entire page with the
 * global "Something went wrong" screen: one widget takes down the whole
 * dashboard (op-8lhv).
 *
 * Errors are still reported to Sentry (mirroring the app-level boundary) so
 * isolating a widget failure does not cost us observability.
 */
import { Card, Text } from '@mantine/core';
import * as Sentry from '@sentry/react';
import React from 'react';

interface WidgetErrorBoundaryProps {
  /** Human-readable widget name, shown in the fallback so the user knows what failed. */
  title?: string;
  children: React.ReactNode;
}

class WidgetErrorBoundary extends React.Component<
  WidgetErrorBoundaryProps,
  { hasError: boolean }
> {
  state = { hasError: false };

  static getDerivedStateFromError() {
    return { hasError: true };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error('Dashboard widget crashed:', error, info);
    // Report with the React component stack as context. No-op when Sentry.init
    // wasn't called (VITE_SENTRY_DSN unset), matching the app-level boundary.
    Sentry.captureException(error, {
      contexts: { react: { componentStack: info.componentStack ?? '' } },
    });
  }

  render() {
    if (this.state.hasError) {
      return (
        <Card shadow="sm" padding="md" radius="md" withBorder role="alert">
          <Text fw={600} size="sm">
            {this.props.title ?? 'Widget'}
          </Text>
          <Text c="red" size="sm" mt="xs">
            This widget couldn&apos;t load. The rest of the dashboard is unaffected.
          </Text>
        </Card>
      );
    }
    return this.props.children;
  }
}

export default WidgetErrorBoundary;
