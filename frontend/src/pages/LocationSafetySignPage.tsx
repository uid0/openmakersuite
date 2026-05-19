/**
 * Per-Location Safety Sign page (oms-gzycmj).
 *
 * Mounted at ``/facilities/locations/:id/safety-sign``. The print
 * variant (``/safety-sign/print``) renders the same body in a stripped
 * shell driven by ``styles/safety-sign-print.css``.
 */
import { Alert, Button, Group, Paper, Stack, Text } from '@mantine/core';
import { IconAlertCircle, IconPrinter } from '@tabler/icons-react';
import React, { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';

import LocationSafetySignBody from '../components/LocationSafetySignBody';
import WorkspacePage from '../components/landing/WorkspacePage';
import { SafetySheet, safetySheetAPI } from '../services/api';
import '../styles/safety-sign-print.css';
import { extractErrorMessage } from '../utils/extractErrorMessage';

const LocationSafetySignPage: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const [sheet, setSheet] = useState<SafetySheet | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!id) return;
    setLoading(true);
    safetySheetAPI
      .getLocationSafetySheet(id)
      .then((response) => setSheet(response.data))
      .catch((err) => setError(extractErrorMessage(err, 'Failed to load safety sheet')))
      .finally(() => setLoading(false));
  }, [id]);

  if (loading) {
    return (
      <WorkspacePage
        testId="location-safety-sign-page"
        hero={{
          eyebrow: 'Facilities · Safety',
          title: 'Safety Sign',
          description: 'Loading…',
        }}
      >
        <Paper withBorder p="md">
          <Text c="dimmed">Loading safety sheet…</Text>
        </Paper>
      </WorkspacePage>
    );
  }

  if (error || !sheet) {
    return (
      <WorkspacePage
        testId="location-safety-sign-page"
        hero={{
          eyebrow: 'Facilities · Safety',
          title: 'Safety Sign',
          description: error || 'Not found.',
        }}
      >
        <Alert icon={<IconAlertCircle size={16} />} color="red">
          {error || 'Safety sheet not found.'}
        </Alert>
      </WorkspacePage>
    );
  }

  return (
    <WorkspacePage
      testId="location-safety-sign-page"
      hero={{
        eyebrow: 'Facilities · Safety',
        title: `${sheet.location.name} · Safety sign`,
        description: 'Printable summary of which breakers de-energize this room.',
        action: (
          <Group gap="sm">
            <Button
              variant="default"
              component={Link}
              to={`/inventory/locations/${sheet.location.id}`}
            >
              Back to room
            </Button>
            <Button
              leftSection={<IconPrinter size={16} />}
              component={Link}
              to={`/facilities/locations/${sheet.location.id}/safety-sign/print`}
              target="_blank"
              data-testid="print-safety-sign-button"
            >
              Print
            </Button>
          </Group>
        ),
      }}
    >
      <Paper withBorder p="lg">
        <LocationSafetySignBody sheet={sheet} />
      </Paper>
      <Stack gap="xs" mt="md">
        <Text size="xs" c="dimmed">
          Need to add a missing breaker, switch, or thermostat? Edit the room or the relevant
          electrical record and reload this page.
        </Text>
      </Stack>
    </WorkspacePage>
  );
};

export default LocationSafetySignPage;
