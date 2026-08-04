/**
 * ServiceStatusContext tests.
 *
 * The contract this file defends is the conservative one: the status poll is
 * only ever spent when it can pay off (signed in, tab visible), and nothing it
 * fails to learn is ever allowed to gate a control.
 */
import { render, renderHook, screen, waitFor } from '@testing-library/react';
import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import {
  SERVICE_STATUS_POLL_MS,
  ServiceStatusProvider,
} from '../../contexts/ServiceStatusContext';
import { useServiceStatus } from '../../hooks/useServiceStatus';
import { ResilienceStatus, ServiceStatus } from '../../types';

vi.mock('../../services/api', async () => {
  const actual = await vi.importActual<typeof import('../../services/api')>(
    '../../services/api',
  );
  return {
    ...actual,
    resilienceAPI: { getStatus: vi.fn() },
  };
});

// eslint-disable-next-line @typescript-eslint/no-var-requires
const { resilienceAPI } = await import('../../services/api');
const getStatus = resilienceAPI.getStatus as unknown as ReturnType<typeof vi.fn>;

const service = (overrides: Partial<ServiceStatus> = {}): ServiceStatus => ({
  key: 'device_control',
  label: 'Device control',
  description: 'Turning equipment on and off remotely',
  state: 'open',
  healthy: false,
  since: '2026-08-04T12:00:00Z',
  last_error: 'broker refused connection at mqtt-internal:8883',
  degraded_count: 1,
  total_count: 1,
  ...overrides,
});

const snapshot = (services: ServiceStatus[]): { data: ResilienceStatus } => ({
  data: {
    degraded: services.some((s) => !s.healthy),
    checked_at: '2026-08-04T12:00:00Z',
    services,
  },
});

const wrapper = ({ children }: { children: React.ReactNode }) => (
  <ServiceStatusProvider>{children}</ServiceStatusProvider>
);

describe('ServiceStatusProvider', () => {
  beforeEach(() => {
    localStorage.clear();
    getStatus.mockReset();
    getStatus.mockResolvedValue(snapshot([]));
  });

  it('does not poll while unauthenticated', async () => {
    renderHook(() => useServiceStatus(), { wrapper });

    // Give any effect-scheduled fetch a chance to land before asserting.
    await waitFor(() => expect(getStatus).not.toHaveBeenCalled());
  });

  it('polls once signed in and reports the degraded service', async () => {
    localStorage.setItem('token', 'jwt');
    getStatus.mockResolvedValue(snapshot([service()]));

    const { result } = renderHook(() => useServiceStatus(), { wrapper });

    await waitFor(() => expect(getStatus).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(result.current.degraded).toBe(true));
    expect(result.current.isDegraded('device_control')).toBe(true);
    expect(result.current.getService('device_control')?.label).toBe('Device control');
  });

  it('treats half_open as degraded — the dependency is still on trial', async () => {
    localStorage.setItem('token', 'jwt');
    getStatus.mockResolvedValue(
      snapshot([service({ state: 'half_open', healthy: false })]),
    );

    const { result } = renderHook(() => useServiceStatus(), { wrapper });

    await waitFor(() => expect(result.current.isDegraded('device_control')).toBe(true));
  });

  it('reports a closed service as healthy', async () => {
    localStorage.setItem('token', 'jwt');
    getStatus.mockResolvedValue(
      snapshot([service({ state: 'closed', healthy: true, last_error: null })]),
    );

    const { result } = renderHook(() => useServiceStatus(), { wrapper });

    await waitFor(() => expect(getStatus).toHaveBeenCalled());
    await waitFor(() => expect(result.current.services).toHaveLength(1));
    expect(result.current.isDegraded('device_control')).toBe(false);
    expect(result.current.degraded).toBe(false);
  });

  it('treats a failed status fetch as healthy and surfaces no error', async () => {
    localStorage.setItem('token', 'jwt');
    getStatus.mockRejectedValue(new Error('500 Server Error'));

    const { result } = renderHook(() => useServiceStatus(), { wrapper });

    await waitFor(() => expect(getStatus).toHaveBeenCalled());
    expect(result.current.degraded).toBe(false);
    expect(result.current.isDegraded('device_control')).toBe(false);
    expect(result.current.isDegraded('email')).toBe(false);
    expect(result.current.status).toBeNull();
  });

  it('stops gating once a previously degraded fetch starts failing', async () => {
    localStorage.setItem('token', 'jwt');
    getStatus.mockResolvedValueOnce(snapshot([service()]));

    const { result } = renderHook(() => useServiceStatus(), { wrapper });
    await waitFor(() => expect(result.current.isDegraded('device_control')).toBe(true));

    // A snapshot we can no longer refresh is not evidence of an outage.
    getStatus.mockRejectedValue(new Error('network down'));
    await result.current.refresh();

    await waitFor(() => expect(result.current.isDegraded('device_control')).toBe(false));
  });

  it('skips the request while the tab is hidden', async () => {
    localStorage.setItem('token', 'jwt');
    const visibility = vi
      .spyOn(document, 'visibilityState', 'get')
      .mockReturnValue('hidden');

    renderHook(() => useServiceStatus(), { wrapper });

    await waitFor(() => expect(getStatus).not.toHaveBeenCalled());

    // …and catches up as soon as the tab comes back.
    visibility.mockReturnValue('visible');
    document.dispatchEvent(new Event('visibilitychange'));
    await waitFor(() => expect(getStatus).toHaveBeenCalledTimes(1));

    visibility.mockRestore();
  });

  it('starts polling when a sign-in happens mid-session', async () => {
    const { result } = renderHook(() => useServiceStatus(), { wrapper });
    await waitFor(() => expect(getStatus).not.toHaveBeenCalled());

    localStorage.setItem('token', 'jwt');
    getStatus.mockResolvedValue(snapshot([service({ key: 'email', label: 'Email delivery' })]));
    window.dispatchEvent(new Event('authChange'));

    await waitFor(() => expect(result.current.isDegraded('email')).toBe(true));
  });

  it('clears the snapshot on sign-out', async () => {
    localStorage.setItem('token', 'jwt');
    getStatus.mockResolvedValue(snapshot([service()]));

    const { result } = renderHook(() => useServiceStatus(), { wrapper });
    await waitFor(() => expect(result.current.degraded).toBe(true));

    localStorage.removeItem('token');
    window.dispatchEvent(new Event('authChange'));

    await waitFor(() => expect(result.current.status).toBeNull());
    expect(result.current.isDegraded('device_control')).toBe(false);
  });

  it('gates nothing when consumed outside the provider', () => {
    const Probe: React.FC = () => {
      const { isDegraded, degraded } = useServiceStatus();
      return (
        <span data-testid="probe">
          {String(degraded)}:{String(isDegraded('device_control'))}
        </span>
      );
    };

    render(<Probe />);

    expect(screen.getByTestId('probe')).toHaveTextContent('false:false');
    expect(getStatus).not.toHaveBeenCalled();
  });

  it('polls on a one-minute cycle', () => {
    expect(SERVICE_STATUS_POLL_MS).toBe(60_000);
  });
});
