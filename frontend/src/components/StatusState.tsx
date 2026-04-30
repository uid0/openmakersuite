/**
 * StatusState
 *
 * Canonical, accessible loading / empty / forbidden / missing / error / offline
 * surface for critical journeys. Shared so every page handles the same edge
 * cases the same way (AC-19) and so accessibility expectations (role, label,
 * focus, contrast) only need to be verified in one place (AC-20).
 *
 * Each variant maps to a documented state in
 * docs/FRONTEND_JOURNEY_INVENTORY.md. Pages should compose this around their
 * primary content rather than rolling bespoke "Loading..." / "No data" markup.
 */
import { Alert, Button, Group, Loader, Stack, Text, Title } from '@mantine/core';
import {
  IconAlertTriangle,
  IconInbox,
  IconLock,
  IconQuestionMark,
  IconWifiOff,
} from '@tabler/icons-react';
import React from 'react';

export type StatusStateVariant =
  | 'loading'
  | 'empty'
  | 'forbidden'
  | 'missing'
  | 'error'
  | 'offline';

export interface StatusStateAction {
  label: string;
  onClick: () => void;
  variant?: 'filled' | 'outline' | 'subtle';
}

export interface StatusStateProps {
  variant: StatusStateVariant;
  title?: string;
  description?: React.ReactNode;
  actions?: StatusStateAction[];
  /**
   * Optional override for testing. When omitted, every variant gets a
   * deterministic data-testid of `status-state-<variant>` so e2e and unit
   * tests can assert without rebuilding the title string.
   */
  testId?: string;
}

const DEFAULTS: Record<
  StatusStateVariant,
  { title: string; description: string; role: 'status' | 'alert' }
> = {
  loading: {
    title: 'Loading…',
    description: 'Please wait while we load this page.',
    role: 'status',
  },
  empty: {
    title: 'Nothing here yet',
    description: 'There is no data to show for this view.',
    role: 'status',
  },
  forbidden: {
    title: 'You do not have access',
    description:
      'You are signed in but this action requires a different role. Ask a staff member if you need access.',
    role: 'alert',
  },
  missing: {
    title: 'Not found',
    description: 'The item you were looking for was moved, deleted, or never existed.',
    role: 'alert',
  },
  error: {
    title: 'Something went wrong',
    description: 'We could not complete this action. Try again, or contact staff if this keeps happening.',
    role: 'alert',
  },
  offline: {
    title: 'You appear to be offline',
    description:
      'Check your network connection. We will retry automatically when you are back online, or you can retry now.',
    role: 'alert',
  },
};

const variantIcon = (variant: StatusStateVariant): React.ReactNode => {
  switch (variant) {
    case 'loading':
      return <Loader size="sm" aria-hidden="true" />;
    case 'empty':
      return <IconInbox size={20} aria-hidden="true" />;
    case 'forbidden':
      return <IconLock size={20} aria-hidden="true" />;
    case 'missing':
      return <IconQuestionMark size={20} aria-hidden="true" />;
    case 'offline':
      return <IconWifiOff size={20} aria-hidden="true" />;
    case 'error':
    default:
      return <IconAlertTriangle size={20} aria-hidden="true" />;
  }
};

const variantColor = (variant: StatusStateVariant): string => {
  switch (variant) {
    case 'loading':
      return 'blue';
    case 'empty':
      return 'gray';
    case 'forbidden':
      return 'yellow';
    case 'missing':
      return 'gray';
    case 'offline':
      return 'orange';
    case 'error':
    default:
      return 'red';
  }
};

const StatusState: React.FC<StatusStateProps> = ({
  variant,
  title,
  description,
  actions,
  testId,
}) => {
  const defaults = DEFAULTS[variant];
  const resolvedTitle = title ?? defaults.title;
  const resolvedDescription = description ?? defaults.description;
  const dataTestId = testId ?? `status-state-${variant}`;

  return (
    <div
      role={defaults.role}
      aria-live={defaults.role === 'status' ? 'polite' : 'assertive'}
      data-testid={dataTestId}
      data-variant={variant}
    >
      <Alert
        color={variantColor(variant)}
        variant="light"
        icon={variantIcon(variant)}
      >
        <Stack gap="xs">
          <Title order={3} size="h5" m={0}>
            {resolvedTitle}
          </Title>
          {resolvedDescription && (
            <Text size="sm" c="dimmed">
              {resolvedDescription}
            </Text>
          )}
          {actions && actions.length > 0 && (
            <Group gap="xs" mt="xs">
              {actions.map((action) => (
                <Button
                  key={action.label}
                  size="sm"
                  variant={action.variant ?? 'filled'}
                  onClick={action.onClick}
                >
                  {action.label}
                </Button>
              ))}
            </Group>
          )}
        </Stack>
      </Alert>
    </div>
  );
};

export default StatusState;
