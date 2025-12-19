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

const DashboardWidget: React.FC<DashboardWidgetProps> = ({
  title,
  loading = false,
  error,
  children,
  className = '',
}) => {
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
        {error && (
          <div className="dashboard-widget-error">
            <Text c="red" size="sm">
              {error}
            </Text>
          </div>
        )}
        {!loading && !error && children}
      </div>
    </Card>
  );
};

export default DashboardWidget;
