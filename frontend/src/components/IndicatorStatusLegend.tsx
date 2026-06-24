/**
 * The indicator color/pattern legend — one row per operational status with a
 * live swatch, label and plain-language description. Mirrors the ga-72l spec
 * mapping so admins can map a light they see in the room to its meaning.
 */
import { Group, Stack, Text } from '@mantine/core';
import IndicatorSwatch from './IndicatorSwatch';
import {
  INDICATOR_STATUS_DESCRIPTIONS,
  INDICATOR_STATUS_LABELS,
  INDICATOR_STATUS_ORDER,
} from '../utils/indicatorPresentation';

interface Props {
  title?: string;
}

export default function IndicatorStatusLegend({ title = 'Light legend' }: Props) {
  return (
    <div data-testid="indicator-legend">
      <Text size="sm" fw={500} mb="xs">
        {title}
      </Text>
      <Stack gap={6}>
        {INDICATOR_STATUS_ORDER.map((status) => (
          <Group key={status} gap="xs" wrap="nowrap" data-testid={`legend-row-${status}`}>
            <IndicatorSwatch status={status} testId={`legend-swatch-${status}`} />
            <Text size="sm" fw={500}>
              {INDICATOR_STATUS_LABELS[status]}
            </Text>
            <Text size="xs" c="dimmed">
              — {INDICATOR_STATUS_DESCRIPTIONS[status]}
            </Text>
          </Group>
        ))}
      </Stack>
    </div>
  );
}
