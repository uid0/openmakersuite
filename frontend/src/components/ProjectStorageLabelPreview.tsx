/**
 * Claim-label preview for a project-storage stint.
 *
 * Renders the server-rendered label PNG
 * (GET /api/project-storage/stints/{id}/label/?printer=…) with a
 * Brother QL / Epson TM toggle so a warden can eyeball either printer's
 * output. Styled to mirror the kiosk's post-create preview
 * (ProjectStorageKioskPage): a bordered gray panel with an uppercase
 * caption and a contained image.
 *
 * The label PNG is only fetched while this component is mounted, so
 * callers get lazy loading for free by rendering it conditionally (e.g.
 * only once a stint has loaded).
 */
import { Image, Paper, SegmentedControl, Stack, Text } from '@mantine/core';
import React, { useState } from 'react';

import { projectStorageAPI } from '../services/api';

export type LabelPrinter = 'brother_ql' | 'epson_tm';

const PRINTER_OPTIONS: { label: string; value: LabelPrinter }[] = [
  { label: 'Brother QL', value: 'brother_ql' },
  { label: 'Epson TM', value: 'epson_tm' },
];

interface ProjectStorageLabelPreviewProps {
  stintId: string;
  /** testid for the <img>; the toggle gets `${testId}-printer`. */
  testId?: string;
}

const ProjectStorageLabelPreview: React.FC<ProjectStorageLabelPreviewProps> = ({
  stintId,
  testId = 'label-preview',
}) => {
  const [printer, setPrinter] = useState<LabelPrinter>('brother_ql');

  return (
    <Paper withBorder radius="md" p="md" bg="gray.0">
      <Stack gap="xs" align="center">
        <Text size="xs" c="dimmed" tt="uppercase" fw={700}>
          Label preview
        </Text>
        <SegmentedControl
          size="xs"
          value={printer}
          onChange={(v) => setPrinter(v as LabelPrinter)}
          data={PRINTER_OPTIONS}
          data-testid={`${testId}-printer`}
        />
        <Image
          src={projectStorageAPI.labelUrl(stintId, printer)}
          alt={`Label for stint ${stintId} (${printer})`}
          fit="contain"
          h={200}
          fallbackSrc=""
          data-testid={testId}
        />
      </Stack>
    </Paper>
  );
};

export default ProjectStorageLabelPreview;
