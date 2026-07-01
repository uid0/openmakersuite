/**
 * RoomOperationalModeControl
 *
 * Lets an admin set a room's manual operational mode (epic ga-72l). The chosen
 * mode drives every indicator light bound to the room, so the control shows the
 * current state with a swatch and the full color/pattern legend.
 *
 * Rooms have no derived status — the mode is set directly here. Read access is
 * open; the editable control + save are staff-only (the API enforces the same).
 */
import { Badge, Button, Card, Group, Select, Stack, Text } from '@mantine/core';
import { useCallback, useEffect, useState } from 'react';
import IndicatorStatusLegend from './IndicatorStatusLegend';
import IndicatorSwatch from './IndicatorSwatch';
import {
  ForgeKeyRoomOperationalMode,
  IndicatorStatusValue,
  forgekeyAPI,
} from '../services/api';
import { showError, showSuccess } from '../utils/dialogs';
import { extractErrorMessage } from '../utils/extractErrorMessage';
import {
  INDICATOR_STATUS_LABELS,
  INDICATOR_STATUS_ORDER,
  statusLabel,
} from '../utils/indicatorPresentation';

interface Props {
  locationId: number;
  locationName?: string;
}

const STATUS_BADGE_COLORS: Record<string, string> = {
  available: 'green',
  in_use: 'teal',
  unavailable: 'red',
  locked_out: 'dark',
  classroom: 'grape',
};

function unwrap<T>(data: { results?: T[] } | T[]): T[] {
  if (Array.isArray(data)) return data;
  return data.results ?? [];
}

export default function RoomOperationalModeControl({ locationId, locationName }: Props) {
  const isStaff =
    typeof window !== 'undefined' &&
    (localStorage.getItem('is_staff') === 'true' ||
      localStorage.getItem('is_superuser') === 'true');

  const [mode, setMode] = useState<ForgeKeyRoomOperationalMode | null>(null);
  const [selected, setSelected] = useState<IndicatorStatusValue>('available');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await forgekeyAPI.listRoomOperationalModes({ location: locationId });
      const existing = unwrap<ForgeKeyRoomOperationalMode>(res.data)[0] ?? null;
      setMode(existing);
      setSelected(existing?.mode ?? 'available');
    } catch (err) {
      showError(extractErrorMessage(err, 'Failed to load room mode.'));
    } finally {
      setLoading(false);
    }
  }, [locationId]);

  useEffect(() => {
    load();
  }, [load]);

  const onSave = async () => {
    setSaving(true);
    try {
      if (mode) {
        await forgekeyAPI.setRoomOperationalMode(mode.id, selected);
      } else {
        await forgekeyAPI.createRoomOperationalMode({ location: locationId, mode: selected });
      }
      showSuccess('Room mode updated.');
      await load();
    } catch (err) {
      showError(extractErrorMessage(err, 'Failed to update room mode.'));
    } finally {
      setSaving(false);
    }
  };

  if (loading) return null;

  const currentMode: IndicatorStatusValue = mode?.mode ?? 'available';

  return (
    <Card withBorder radius="md" p="md" data-testid="room-operational-mode">
      <Text fw={600}>Room operational mode</Text>
      <Text size="xs" c="dimmed" mb="md">
        Drives the indicator lights bound to {locationName ? `“${locationName}”` : 'this room'}.
      </Text>

      <Stack gap="md">
        <Group gap="xs">
          <Text size="sm" fw={500}>
            Current
          </Text>
          <IndicatorSwatch status={currentMode} testId="room-mode-swatch" />
          <Badge
            color={STATUS_BADGE_COLORS[currentMode] ?? 'gray'}
            data-testid="room-mode-current"
          >
            {statusLabel(currentMode)}
          </Badge>
          {!mode && (
            <Text size="xs" c="dimmed">
              (default — not set)
            </Text>
          )}
          {mode?.updated_by_username && (
            <Text size="xs" c="dimmed">
              set by {mode.updated_by_username}
            </Text>
          )}
        </Group>

        <Group align="flex-end" gap="sm">
          <Select
            label="Set mode"
            data={INDICATOR_STATUS_ORDER.map((s) => ({
              value: s,
              label: INDICATOR_STATUS_LABELS[s],
            }))}
            value={selected}
            onChange={(v) => setSelected((v as IndicatorStatusValue) ?? 'available')}
            disabled={!isStaff}
            w={220}
            aria-label="Set mode"
            data-testid="room-mode-select"
          />
          <Button
            size="sm"
            loading={saving}
            disabled={!isStaff || selected === currentMode}
            onClick={onSave}
            data-testid="room-mode-save"
          >
            Save
          </Button>
        </Group>

        {!isStaff && (
          <Text size="xs" c="dimmed">
            Staff access is required to change the room mode.
          </Text>
        )}

        <IndicatorStatusLegend />
      </Stack>
    </Card>
  );
}
