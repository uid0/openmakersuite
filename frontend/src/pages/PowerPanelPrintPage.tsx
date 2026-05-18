/**
 * Standalone printable panel directory at
 * `/facilities/electrical/panels/:id/print`.
 *
 * Rendered without the workspace shell / sidebar so the page prints
 * directly to a clean letter-size sheet that fits inside the cabinet
 * door. The user lands here from the "Print directory" button on the
 * panel detail page (opened in a new tab) and then hits Cmd+P / Ctrl+P.
 *
 * Print CSS lives in styles/panel-print.css.
 */
import { Box, Button, Group, Stack, Text } from '@mantine/core';
import { IconPrinter } from '@tabler/icons-react';
import React, { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';

import PanelLayoutGrid from '../components/PanelLayoutGrid';
import { electricalSafetyAPI, PowerPanelTopology } from '../services/api';
import '../styles/panel-print.css';

const PowerPanelPrintPage: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const [topology, setTopology] = useState<PowerPanelTopology | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    electricalSafetyAPI
      .getPanelTopology(id)
      .then((response) => {
        if (cancelled) return;
        setTopology(response.data);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err?.response?.data?.detail || 'Failed to load panel topology.');
      });
    return () => {
      cancelled = true;
    };
  }, [id]);

  if (error) {
    return (
      <Box p="xl" data-testid="panel-print-error">
        <Text c="red">{error}</Text>
      </Box>
    );
  }

  if (!topology) {
    return (
      <Box p="xl">
        <Text c="dimmed">Loading panel directory…</Text>
      </Box>
    );
  }

  const subtitle = [
    topology.location_name,
    `${topology.voltage}V`,
    `${topology.phase_configuration} phase`,
    topology.main_breaker_amperage ? `${topology.main_breaker_amperage}A main` : null,
  ]
    .filter(Boolean)
    .join(' · ');

  return (
    <Box className="panel-print-page" data-testid="panel-print-page" p="xl">
      <Stack gap="md">
        <Group justify="space-between" align="flex-start" className="panel-print-no-print">
          <div>
            <Text size="sm" c="dimmed">
              Printable cabinet directory
            </Text>
            <Text size="xs" c="dimmed">
              Use your browser's print dialog (Cmd/Ctrl+P) and choose letter / A4.
            </Text>
          </div>
          <Button
            variant="default"
            leftSection={<IconPrinter size={16} />}
            onClick={() => window.print()}
          >
            Print
          </Button>
        </Group>

        <Stack gap={2} className="panel-print-header">
          <Text size="xl" fw={700}>
            {topology.name}
          </Text>
          <Text size="sm" c="dimmed">
            {subtitle}
          </Text>
        </Stack>

        <PanelLayoutGrid topology={topology} density="print" />

        <Group justify="space-between" className="panel-print-footer">
          <Text size="xs" c="dimmed">
            Generated from OMS · panel #{topology.id}
          </Text>
          <Text size="xs" c="dimmed">
            {new Date().toISOString().slice(0, 10)}
          </Text>
        </Group>
      </Stack>
    </Box>
  );
};

export default PowerPanelPrintPage;
