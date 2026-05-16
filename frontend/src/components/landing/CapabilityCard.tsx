/**
 * CapabilityCard — single grid item used on the homepage and every
 * workspace overview page. Composes:
 *   - Optional eyebrow (mono caps): a numbered or category label
 *   - Icon "instrument chip" (Tabler icon on a dark square)
 *   - Title (sans, semibold)
 *   - Description (1-3 lines, muted)
 *   - Optional badge (e.g. "Staff" for gated capabilities)
 *   - "Open →" affordance that animates on hover
 *
 * The whole card is the link (single click target — no accessibility
 * footgun where the title and the arrow are separate links).
 *
 * AC-2: every workspace overview page uses this component, so card
 * styling stays consistent without per-page duplication.
 */
import { Badge, Box, Group, Stack, Text } from '@mantine/core';
import { IconArrowNarrowRight } from '@tabler/icons-react';
import React from 'react';
import { Link } from 'react-router-dom';

import '../../styles/landing.css';

export interface CapabilityCardProps {
  /** Destination path. Use absolute paths (e.g. "/inventory/scan"). */
  to: string;
  /** Required: card title in body sans. */
  title: string;
  /** Required: 1-3 line description shown beneath the title. */
  description: string;
  /** Tabler icon component (rendered at 22px on a dark chip). */
  icon: React.ReactNode;
  /** Optional eyebrow (mono caps). Use for capability numbering or category. */
  eyebrow?: string;
  /** Optional pill badge (e.g. "Staff") shown above the title. */
  badge?: string;
  /** Override the call-to-action label; defaults to "Open". */
  ctaLabel?: string;
  /** Stable test selector. Defaults to `capability-card-${to}`. */
  testId?: string;
}

const CapabilityCard: React.FC<CapabilityCardProps> = ({
  to,
  title,
  description,
  icon,
  eyebrow,
  badge,
  ctaLabel = 'Open',
  testId,
}) => {
  // Backend endpoints (api/*, /media/*, /static/*) and *.pdf / *.csv
  // downloads must use a real <a href>, not a SPA <Link>, otherwise React
  // Router intercepts the click and the browser never makes the HTTP
  // request. Detect those and emit an anchor; SPA routes stay on <Link>.
  const isExternal =
    to.startsWith('/api/') ||
    to.startsWith('/media/') ||
    to.startsWith('/static/') ||
    to.startsWith('http://') ||
    to.startsWith('https://') ||
    /\.(pdf|csv|xlsx?|json)(\?|$)/i.test(to);

  const innerContent = (
    <Stack gap="md" style={{ height: '100%' }}>
      <Group justify="space-between" align="flex-start" wrap="nowrap">
        <Box className="landing-icon-chip" aria-hidden>
          {icon}
        </Box>
        {badge && (
          <Badge color="blue" variant="light" size="sm" radius="sm">
            {badge}
          </Badge>
        )}
      </Group>

      <Stack gap={6} style={{ flex: 1 }}>
        {eyebrow && (
          <Text component="span" className="landing-eyebrow">
            {eyebrow}
          </Text>
        )}
        <Text component="h3" fw={600} size="lg" style={{ margin: 0, lineHeight: 1.25 }}>
          {title}
        </Text>
        <Text size="sm" c="dimmed" style={{ lineHeight: 1.5 }}>
          {description}
        </Text>
      </Stack>

      <Box>
        <span className="landing-arrow">
          {ctaLabel}
          <IconArrowNarrowRight size={16} stroke={2.4} />
        </span>
      </Box>
    </Stack>
  );

  if (isExternal) {
    return (
      <a
        href={to}
        target="_blank"
        rel="noopener noreferrer"
        className="landing-capability-card"
        data-testid={testId ?? `capability-card-${to}`}
        aria-label={title}
        style={{ padding: '1.4rem' }}
      >
        {innerContent}
      </a>
    );
  }

  return (
    <Link
      to={to}
      className="landing-capability-card"
      data-testid={testId ?? `capability-card-${to}`}
      aria-label={title}
      style={{ padding: '1.4rem' }}
    >
      {innerContent}
    </Link>
  );
};

export default CapabilityCard;
