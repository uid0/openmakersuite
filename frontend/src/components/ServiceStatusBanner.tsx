/**
 * ServiceStatusBanner
 *
 * Global banner for external dependencies that are currently down, so a member
 * finds out from the app rather than from a control that silently does nothing.
 * Mounted once in WorkspaceLayout alongside the other banners; fed by the one
 * shared poll in ServiceStatusContext.
 *
 * Copy rules:
 *  - Plain language and reassuring. The reader cannot fix a dead MQTT broker;
 *    they need to know what won't work and that we're retrying.
 *  - `last_error` is NEVER rendered here. It can carry internal detail
 *    (hostnames, provider responses) and this banner is shown to every member.
 *  - Dismissible for the session, but the dismissal is scoped to the services
 *    that were degraded when it was dismissed — if something *else* breaks
 *    afterwards, the banner comes back.
 */
import { Alert, Text } from '@mantine/core';
import { IconAlertTriangle } from '@tabler/icons-react';
import React, { useCallback, useEffect, useMemo, useState } from 'react';

import { useServiceStatus } from '../hooks/useServiceStatus';
import { ServiceStatus } from '../types';

const DISMISSED_KEY = 'oms_service_status_dismissed';

const readDismissed = (): string[] => {
  try {
    const raw = sessionStorage.getItem(DISMISSED_KEY);
    const parsed = raw ? JSON.parse(raw) : null;
    return Array.isArray(parsed) ? parsed.filter((k): k is string => typeof k === 'string') : [];
  } catch {
    // Unreadable/garbage store is best-effort: show the banner.
    return [];
  }
};

const writeDismissed = (keys: string[]): void => {
  try {
    sessionStorage.setItem(DISMISSED_KEY, JSON.stringify(keys));
  } catch {
    // Private mode / quota. The dismissal just won't survive a remount.
  }
};

/**
 * One-line summary of what is wrong with a service.
 *
 * A family of breakers (webhooks) reports counts, because "webhook delivery is
 * down" overstates 3 bad endpoints out of 12. `total_count` is only ever > 1
 * for such a family, which is why "endpoints" is safe wording here.
 */
export const serviceHeadline = (service: ServiceStatus): string =>
  service.total_count > 1
    ? `${service.label} degraded (${service.degraded_count} of ${service.total_count} endpoints)`
    : `${service.label} is temporarily unavailable`;

const ServiceStatusBanner: React.FC = () => {
  const { services } = useServiceStatus();
  const [dismissed, setDismissed] = useState<string[]>(readDismissed);

  const affected = useMemo(() => services.filter((service) => !service.healthy), [services]);
  const affectedKeys = useMemo<string[]>(
    () => affected.map((service) => service.key),
    [affected],
  );
  const affectedSignature = affectedKeys.join('|');

  // Drop recovered services from the dismissal. Without this, dismissing an
  // email outage would also swallow the *next* email outage this session.
  useEffect(() => {
    setDismissed((prev) => {
      const next = prev.filter((key) => affectedKeys.includes(key));
      if (next.length === prev.length) return prev;
      writeDismissed(next);
      return next;
    });
    // affectedKeys is rebuilt on every snapshot; key off its contents instead.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [affectedSignature]);

  const handleDismiss = useCallback(() => {
    setDismissed(affectedKeys);
    writeDismissed(affectedKeys);
  }, [affectedKeys]);

  const visible = affected.some((service) => !dismissed.includes(service.key));
  if (!visible) return null;

  const single = affected.length === 1 ? affected[0] : null;

  return (
    <Alert
      icon={<IconAlertTriangle size={16} aria-hidden="true" />}
      title={single ? serviceHeadline(single) : 'Some services are temporarily unavailable'}
      color="yellow"
      variant="light"
      radius="md"
      // Self-spacing: the layout mounts this bare, so it must not leave a gap
      // behind when it renders nothing.
      m="md"
      withCloseButton
      closeButtonLabel="Dismiss service status"
      onClose={handleDismiss}
      role="status"
      data-testid="service-status-banner"
    >
      {single ? (
        <Text size="sm" data-testid={`service-status-line-${single.key}`}>
          {single.description}.
        </Text>
      ) : (
        <ul style={{ margin: 0, paddingLeft: '1.1rem' }}>
          {affected.map((service) => (
            <li key={service.key} data-testid={`service-status-line-${service.key}`}>
              <Text size="sm" span fw={600}>
                {serviceHeadline(service)}
              </Text>
              <Text size="sm" span>
                {' '}
                — {service.description.charAt(0).toLowerCase()}
                {service.description.slice(1)}.
              </Text>
            </li>
          ))}
        </ul>
      )}
      <Text size="sm" mt={6} c="dimmed">
        We&apos;re retrying automatically. Everything else keeps working.
      </Text>
    </Alert>
  );
};

export default ServiceStatusBanner;
