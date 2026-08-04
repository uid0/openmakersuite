/**
 * ServiceStatusBanner tests.
 *
 * Covers what the banner promises members: it stays out of the way when
 * everything works, names the services that don't, never leaks the internal
 * error detail, and a dismissal doesn't swallow the *next* outage.
 */
import { MantineProvider } from '@mantine/core';
import { fireEvent, render, screen } from '@testing-library/react';
import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import ServiceStatusBanner from '../../components/ServiceStatusBanner';
import { ServiceKey, ServiceStatus } from '../../types';

const mockUseServiceStatus = vi.fn();
vi.mock('../../hooks/useServiceStatus', () => ({
  useServiceStatus: () => mockUseServiceStatus(),
}));

const LAST_ERROR = 'HTTPError 401 from postmark: invalid server token abc123';

const service = (overrides: Partial<ServiceStatus> = {}): ServiceStatus => ({
  key: 'email',
  label: 'Email delivery',
  description: 'Sending notifications, reorder alerts, and receipts',
  state: 'open',
  healthy: false,
  since: '2026-08-04T12:00:00Z',
  last_error: LAST_ERROR,
  degraded_count: 1,
  total_count: 1,
  ...overrides,
});

const setStatus = (services: ServiceStatus[]) => {
  mockUseServiceStatus.mockReturnValue({
    status: { degraded: services.some((s) => !s.healthy), checked_at: '', services },
    degraded: services.some((s) => !s.healthy),
    services,
    getService: (key: ServiceKey) => services.find((s) => s.key === key) ?? null,
    isDegraded: (key: ServiceKey) =>
      services.some((s) => s.key === key && s.state !== 'closed'),
    refresh: vi.fn(),
  });
};

const renderBanner = () =>
  render(
    <MantineProvider>
      <ServiceStatusBanner />
    </MantineProvider>,
  );

describe('ServiceStatusBanner', () => {
  beforeEach(() => {
    sessionStorage.clear();
    mockUseServiceStatus.mockReset();
  });

  it('renders nothing while every service is healthy', () => {
    setStatus([service({ state: 'closed', healthy: true, last_error: null })]);
    renderBanner();

    expect(screen.queryByTestId('service-status-banner')).not.toBeInTheDocument();
  });

  it('renders nothing when the status is unknown', () => {
    setStatus([]);
    renderBanner();

    expect(screen.queryByTestId('service-status-banner')).not.toBeInTheDocument();
  });

  it('names the degraded service in plain language', () => {
    setStatus([service()]);
    renderBanner();

    expect(screen.getByTestId('service-status-banner')).toBeInTheDocument();
    expect(screen.getByText('Email delivery is temporarily unavailable')).toBeInTheDocument();
    expect(
      screen.getByText(/Sending notifications, reorder alerts, and receipts/),
    ).toBeInTheDocument();
    expect(screen.getByText(/We're retrying automatically/)).toBeInTheDocument();
  });

  it('never renders last_error — it can carry internal detail', () => {
    setStatus([service()]);
    const { container } = renderBanner();

    expect(container.textContent).not.toContain(LAST_ERROR);
    expect(container.textContent).not.toContain('postmark');
  });

  it('lists every affected label when more than one service is down', () => {
    setStatus([
      service(),
      service({
        key: 'device_control',
        label: 'Device control',
        description: 'Turning equipment on and off remotely',
      }),
      service({
        key: 'whmcs',
        label: 'Maker Box billing',
        description: 'Maker Box subscription and billing lookups',
        state: 'closed',
        healthy: true,
        last_error: null,
      }),
    ]);
    renderBanner();

    expect(screen.getByText('Some services are temporarily unavailable')).toBeInTheDocument();
    expect(screen.getByTestId('service-status-line-email')).toBeInTheDocument();
    expect(screen.getByTestId('service-status-line-device_control')).toBeInTheDocument();
    // Healthy services stay off the banner entirely.
    expect(screen.queryByTestId('service-status-line-whmcs')).not.toBeInTheDocument();
  });

  it('reports counts for an aggregated family rather than declaring it all down', () => {
    setStatus([
      service({
        key: 'webhooks',
        label: 'Webhook delivery',
        description: 'Notifying connected external systems',
        degraded_count: 3,
        total_count: 12,
      }),
    ]);
    renderBanner();

    expect(
      screen.getByText('Webhook delivery degraded (3 of 12 endpoints)'),
    ).toBeInTheDocument();
  });

  it('hides on dismiss and stays hidden for the same outage', () => {
    setStatus([service()]);
    const { unmount } = renderBanner();

    fireEvent.click(screen.getByRole('button', { name: /dismiss service status/i }));
    expect(screen.queryByTestId('service-status-banner')).not.toBeInTheDocument();

    // Dismissal is per-session, so a remount (route change) keeps it hidden.
    unmount();
    renderBanner();
    expect(screen.queryByTestId('service-status-banner')).not.toBeInTheDocument();
  });

  it('comes back when a different service goes degraded after a dismissal', () => {
    setStatus([service()]);
    const { unmount } = renderBanner();
    fireEvent.click(screen.getByRole('button', { name: /dismiss service status/i }));
    unmount();

    setStatus([
      service(),
      service({
        key: 'device_control',
        label: 'Device control',
        description: 'Turning equipment on and off remotely',
      }),
    ]);
    renderBanner();

    expect(screen.getByTestId('service-status-banner')).toBeInTheDocument();
    expect(screen.getByTestId('service-status-line-device_control')).toBeInTheDocument();
  });

  it('comes back when a dismissed service recovers and breaks again', () => {
    setStatus([service()]);
    const { unmount: unmountFirst } = renderBanner();
    fireEvent.click(screen.getByRole('button', { name: /dismiss service status/i }));
    unmountFirst();

    // Recovered: the dismissal for this key is dropped…
    setStatus([service({ state: 'closed', healthy: true, last_error: null })]);
    const { unmount: unmountSecond } = renderBanner();
    expect(screen.queryByTestId('service-status-banner')).not.toBeInTheDocument();
    unmountSecond();

    // …so the next outage of the same service is announced again.
    setStatus([service()]);
    renderBanner();
    expect(screen.getByTestId('service-status-banner')).toBeInTheDocument();
  });
});
