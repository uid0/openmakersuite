/**
 * Stripped-down print route for the per-Location Safety Sign
 * (oms-gzycmj). Mounted at
 * ``/facilities/locations/:id/safety-sign/print`` without the workspace
 * shell so the browser print dialog lands on a clean letter-sized
 * sheet.
 */
import { Box, Button, Group, Stack, Text } from '@mantine/core';
import { IconPrinter } from '@tabler/icons-react';
import React, { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';

import LocationSafetySignBody from '../components/LocationSafetySignBody';
import { SafetySheet, safetySheetAPI } from '../services/api';
import '../styles/safety-sign-print.css';

const LocationSafetySignPrintPage: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const [sheet, setSheet] = useState<SafetySheet | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    safetySheetAPI
      .getLocationSafetySheet(id)
      .then((response) => {
        if (!cancelled) setSheet(response.data);
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err?.response?.data?.detail || 'Failed to load safety sheet.');
        }
      });
    return () => {
      cancelled = true;
    };
  }, [id]);

  if (error) {
    return (
      <Box p="xl" data-testid="safety-sign-print-error">
        <Text c="red">{error}</Text>
      </Box>
    );
  }

  if (!sheet) {
    return (
      <Box p="xl">
        <Text c="dimmed">Loading safety sheet…</Text>
      </Box>
    );
  }

  return (
    <Box className="safety-sign-page" data-testid="safety-sign-print-page" p="xl">
      <Stack gap="md">
        <Group justify="space-between" align="flex-start" className="safety-sign-no-print">
          <div>
            <Text size="sm" c="dimmed">
              Printable per-room safety sign
            </Text>
            <Text size="xs" c="dimmed">
              Use your browser&apos;s print dialog (Cmd/Ctrl+P) and pick letter / A4.
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

        <LocationSafetySignBody sheet={sheet} />

        <Group justify="space-between" className="safety-sign-no-print">
          <Text size="xs" c="dimmed">
            Generated from OMS · location #{sheet.location.id}
          </Text>
          <Text size="xs" c="dimmed">
            {new Date().toISOString().slice(0, 10)}
          </Text>
        </Group>
      </Stack>
    </Box>
  );
};

export default LocationSafetySignPrintPage;
