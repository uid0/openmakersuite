/**
 * Base Dashboard Widget Wrapper
 * Provides consistent styling and drag handle for all widgets
 */
import { Card, Loader, Text } from '@mantine/core';
import { IconGripVertical } from '@tabler/icons-react';
import React from 'react';
import './DashboardWidget.css';

interface DashboardWidgetProps {
  title: string;
  loading?: boolean;
  error?: string;
  children: React.ReactNode;
  className?: string;
}

/**
 * Coerce whatever lands in the `error` prop to a safe, renderable string.
 *
 * The standardized API error envelope is an object (`{code, message}`). If a
 * widget ever forwards that object instead of a string, rendering it as a JSX
 * child throws React #31 — which, before the per-widget error boundary existed,
 * blanked the entire dashboard (op-8lhv). This base component is the single
 * funnel every widget's error passes through, so guard here: never render a raw
 * object as a child.
 */
function normalizeErrorText(error: unknown): string | undefined {
  if (!error) {
    return undefined;
  }
  if (typeof error === 'string') {
    return error;
  }
  if (typeof error === 'object' && error !== null && 'message' in error) {
    const message = (error as { message?: unknown }).message;
    if (typeof message === 'string' && message.trim() !== '') {
      return message;
    }
  }
  return 'Something went wrong loading this widget.';
}

const DashboardWidget: React.FC<DashboardWidgetProps> = ({
  title,
  loading = false,
  error,
  children,
  className = '',
}) => {
  const errorText = normalizeErrorText(error);
  return (
    <Card className={`dashboard-widget ${className}`} shadow="sm" padding="md" radius="md" withBorder>
      <div className="dashboard-widget-header">
        <div className="dashboard-widget-drag-handle">
          <IconGripVertical size={16} />
        </div>
        <Text fw={600} size="sm" className="dashboard-widget-title">
          {title}
        </Text>
      </div>
      <div className="dashboard-widget-content">
        {loading && (
          <div className="dashboard-widget-loading">
            <Loader size="sm" />
          </div>
        )}
        {errorText && (
          <div className="dashboard-widget-error">
            <Text c="red" size="sm">
              {errorText}
            </Text>
          </div>
        )}
        {!loading && !errorText && children}
      </div>
    </Card>
  );
};

export default DashboardWidget;
