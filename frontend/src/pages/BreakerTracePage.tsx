/**
 * Breaker Trace Page (oms-a5f) — show every outlet and light switch fed
 * from a given breaker so a technician can trace power downstream.
 */
import {
  Alert,
  Anchor,
  Badge,
  Group,
  Paper,
  Stack,
  Table,
  Text,
  Title,
} from '@mantine/core';
import { IconAlertCircle, IconArrowLeft } from '@tabler/icons-react';
import React, { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { electricalCircuitsAPI, OUTLET_TYPE_OPTIONS } from '../services/api';
import { Breaker, LightSwitch, Outlet } from '../types';

const outletTypeLabel = (value: string): string =>
  OUTLET_TYPE_OPTIONS.find((option) => option.value === value)?.label || value;

const BreakerTracePage: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const [breaker, setBreaker] = useState<Breaker | null>(null);
  const [outlets, setOutlets] = useState<Outlet[]>([]);
  const [switches, setSwitches] = useState<LightSwitch[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    Promise.all([
      electricalCircuitsAPI.getBreaker(id),
      electricalCircuitsAPI.listOutlets({ breaker: id }),
      electricalCircuitsAPI.listLightSwitches({}),
    ])
      .then(([breakerResp, outletsResp, switchesResp]) => {
        if (cancelled) return;
        setBreaker(breakerResp.data);
        setOutlets(outletsResp.data.results);
        // The API filters outlets by breaker server-side, but light switches
        // do not have a ?breaker filter — narrow client-side.
        setSwitches(
          switchesResp.data.results.filter((sw) => String(sw.breaker) === String(id)),
        );
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err.response?.data?.detail || 'Failed to load breaker trace');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [id]);

  if (loading) {
    return <Text>Loading breaker trace…</Text>;
  }

  if (error || !breaker) {
    return (
      <Stack gap="md">
        <Alert icon={<IconAlertCircle size={16} />} color="red" title="Error">
          {error || 'Breaker not found'}
        </Alert>
        <Anchor component={Link} to="/facilities/electrical">
          Back to electrical circuits
        </Anchor>
      </Stack>
    );
  }

  return (
    <Stack gap="md">
      <Group justify="space-between">
        <div>
          <Anchor component={Link} to="/facilities/electrical">
            <Group gap={4}>
              <IconArrowLeft size={14} />
              <Text size="sm">Back to electrical circuits</Text>
            </Group>
          </Anchor>
          <Title order={2}>
            Trace: {breaker.panel} / {breaker.breaker_number}
          </Title>
          <Text c="dimmed">
            {breaker.amperage}A · {breaker.voltage}V · {breaker.poles}-pole · panel at{' '}
            {breaker.location_name}
          </Text>
        </div>
      </Group>

      {breaker.description && (
        <Paper p="md" withBorder>
          <Text size="sm" fw={500}>
            Description
          </Text>
          <Text>{breaker.description}</Text>
        </Paper>
      )}

      <Paper p="md" withBorder>
        <Group justify="space-between" mb="sm">
          <Title order={4}>Outlets fed by this breaker</Title>
          <Badge variant="light">{outlets.length}</Badge>
        </Group>
        {outlets.length === 0 ? (
          <Text c="dimmed">No outlets are currently linked to this breaker.</Text>
        ) : (
          <Table>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Identifier</Table.Th>
                <Table.Th>Location</Table.Th>
                <Table.Th>Type</Table.Th>
                <Table.Th>Plugged in</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {outlets.map((outlet) => (
                <Table.Tr key={outlet.id}>
                  <Table.Td>
                    <Anchor
                      component={Link}
                      to={`/facilities/electrical/outlets/${outlet.id}/edit`}
                    >
                      {outlet.identifier}
                    </Anchor>
                  </Table.Td>
                  <Table.Td>{outlet.location_name}</Table.Td>
                  <Table.Td>{outletTypeLabel(outlet.outlet_type)}</Table.Td>
                  <Table.Td>{outlet.plugged_in_notes || '—'}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        )}
      </Paper>

      <Paper p="md" withBorder>
        <Group justify="space-between" mb="sm">
          <Title order={4}>Light switches fed by this breaker</Title>
          <Badge variant="light">{switches.length}</Badge>
        </Group>
        {switches.length === 0 ? (
          <Text c="dimmed">
            No light switches are currently linked to this breaker.
          </Text>
        ) : (
          <Table>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Identifier</Table.Th>
                <Table.Th>Mounted at</Table.Th>
                <Table.Th>Controls</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {switches.map((sw) => (
                <Table.Tr key={sw.id}>
                  <Table.Td>
                    <Anchor
                      component={Link}
                      to={`/facilities/electrical/light-switches/${sw.id}/edit`}
                    >
                      {sw.identifier}
                    </Anchor>
                  </Table.Td>
                  <Table.Td>{sw.location_name}</Table.Td>
                  <Table.Td>
                    {sw.controls_location_name ? (
                      <Badge color="blue" variant="light">
                        {sw.controls_location_name}
                      </Badge>
                    ) : (
                      <Text size="sm" c="dimmed">
                        same location
                      </Text>
                    )}
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        )}
      </Paper>
    </Stack>
  );
};

export default BreakerTracePage;
