/**
 * Dashboard Page
 * Customizable dashboard with drag-and-drop widgets
 */
import { Button, Loader, Paper, Stack, Text } from '@mantine/core';
import { useDebouncedCallback } from '@mantine/hooks';
import React, { useCallback, useEffect, useState } from 'react';
import GridLayout, { Layout } from 'react-grid-layout';
import 'react-grid-layout/css/styles.css';
import 'react-resizable/css/styles.css';
import AssetProblemsWidget from '../components/dashboard/AssetProblemsWidget';
import DeliveriesWidget from '../components/dashboard/DeliveriesWidget';
import LowStockWidget from '../components/dashboard/LowStockWidget';
import PendingReordersWidget from '../components/dashboard/PendingReordersWidget';
import QRScansWidget from '../components/dashboard/QRScansWidget';
import WorkspacePage from '../components/landing/WorkspacePage';
import { dashboardAPI } from '../services/api';
import '../styles/DashboardPage.css';
import { DashboardWidget as DashboardWidgetType } from '../types';

const DashboardPage: React.FC = () => {
  const [widgets, setWidgets] = useState<DashboardWidgetType[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [containerWidth, setContainerWidth] = useState(1200);

  useEffect(() => {
    loadWidgets();
    
    // Calculate container width for responsive grid
    const updateWidth = () => {
      const container = document.querySelector('.mantine-Container-root');
      if (container) {
        const width = container.clientWidth;
        setContainerWidth(Math.max(600, width - 48)); // Account for padding
      }
    };
    
    updateWidth();
    window.addEventListener('resize', updateWidth);
    return () => window.removeEventListener('resize', updateWidth);
  }, []);

  const loadWidgets = async () => {
    try {
      setLoading(true);
      setError(null);
      const response = await dashboardAPI.getWidgets();
      setWidgets(response.data);
    } catch (err: any) {
      setError(err.response?.data?.error || 'Failed to load dashboard widgets');
      console.error('Error loading widgets:', err);
    } finally {
      setLoading(false);
    }
  };

  const saveWidgets = useCallback(async (updatedWidgets: DashboardWidgetType[]) => {
    try {
      setSaving(true);
      const widgetsToSave = updatedWidgets.map((w) => ({
        id: w.id,
        widget_type: w.widget_type,
        position_x: w.position_x,
        position_y: w.position_y,
        width: w.width,
        height: w.height,
        is_visible: w.is_visible,
        order: w.order,
        settings: w.settings,
      }));
      await dashboardAPI.saveWidgets(widgetsToSave);
    } catch (err: any) {
      console.error('Error saving widgets:', err);
    } finally {
      setSaving(false);
    }
  }, []);

  const debouncedSave = useDebouncedCallback(saveWidgets, 1000);

  const handleLayoutChange = (layout: Layout[]) => {
    const updatedWidgets = widgets.map((widget) => {
      const layoutItem = layout.find((item) => item.i === widget.id.toString());
      if (layoutItem) {
        return {
          ...widget,
          position_x: layoutItem.x,
          position_y: layoutItem.y,
          width: layoutItem.w,
          height: layoutItem.h,
        };
      }
      return widget;
    });
    setWidgets(updatedWidgets);
    debouncedSave(updatedWidgets);
  };

  const renderWidget = (widget: DashboardWidgetType) => {
    if (!widget.is_visible) {
      return null;
    }

    const widgetProps = {
      key: widget.id,
    };

    switch (widget.widget_type) {
      case 'low_stock':
        return <LowStockWidget {...widgetProps} />;
      case 'pending_reorders':
        return <PendingReordersWidget {...widgetProps} />;
      case 'asset_problems':
        return <AssetProblemsWidget {...widgetProps} />;
      case 'qr_scans':
        return <QRScansWidget {...widgetProps} />;
      case 'deliveries':
        return <DeliveriesWidget {...widgetProps} />;
      default:
        return null;
    }
  };

  const getLayout = (): Layout[] => {
    return widgets
      .filter((w) => w.is_visible)
      .map((widget) => ({
        i: widget.id.toString(),
        x: widget.position_x,
        y: widget.position_y,
        w: widget.width,
        h: widget.height,
        minW: 2,
        minH: 2,
        maxW: 6,
        maxH: 6,
      }));
  };

  const visibleWidgets = widgets.filter((w) => w.is_visible);

  const heroDescription = saving
    ? 'Saving layout…'
    : 'Live operational signals — drag widgets to recompose the layout, resize to focus.';

  return (
    <WorkspacePage
      testId="dashboard-page"
      hero={{
        eyebrow: 'Operations',
        title: 'Dashboard',
        description: heroDescription,
      }}
    >
      {loading ? (
        <Paper withBorder p="xl" radius="md">
          <Stack align="center" gap="md">
            <Loader size="lg" />
            <Text c="dimmed">Loading widgets…</Text>
          </Stack>
        </Paper>
      ) : error ? (
        <Paper withBorder p="xl" radius="md" bg="red.0">
          <Stack align="center" gap="md">
            <Text c="red.9" fw={500}>{error}</Text>
            <Button onClick={loadWidgets}>Retry</Button>
          </Stack>
        </Paper>
      ) : visibleWidgets.length === 0 ? (
        <Paper withBorder p="xl" radius="md">
          <Stack align="center" gap="md">
            <Text c="dimmed">
              No widgets configured. Default widgets will be created on first access.
            </Text>
            <Button onClick={loadWidgets}>Load widgets</Button>
          </Stack>
        </Paper>
      ) : (
        <GridLayout
          className="layout"
          layout={getLayout()}
          cols={6}
          rowHeight={100}
          width={containerWidth}
          onLayoutChange={handleLayoutChange}
          isDraggable={true}
          isResizable={true}
          draggableHandle=".dashboard-widget-drag-handle"
        >
          {visibleWidgets.map((widget) => (
            <div key={widget.id}>{renderWidget(widget)}</div>
          ))}
        </GridLayout>
      )}
    </WorkspacePage>
  );
};

export default DashboardPage;
