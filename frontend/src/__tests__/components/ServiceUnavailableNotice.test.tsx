/**
 * Inline service-gating tests.
 *
 * Two halves: the shared notice itself (does it appear only on real evidence
 * of an outage?) and a real gated surface end-to-end through the provider —
 * DeviceControlsCard, whose five buttons all publish over MQTT.
 */
import { MantineProvider } from '@mantine/core';
import { render, screen, waitFor } from '@testing-library/react';
import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import DeviceControlsCard from '../../components/DeviceControlsCard';
import ServiceUnavailableNotice, {
  DEVICE_CONTROL_UNAVAILABLE,
} from '../../components/ServiceUnavailableNotice';
import { ServiceStatusProvider } from '../../contexts/ServiceStatusContext';
import { ServiceStatus, ServiceStatusState } from '../../types';

vi.mock('../../services/api', async () => {
  const actual = await vi.importActual<typeof import('../../services/api')>(
    '../../services/api',
  );
  return {
    ...actual,
    resilienceAPI: { getStatus: vi.fn() },
    forgekeyAPI: {
      recentCommands: vi.fn(),
      blink: vi.fn(),
      restart: vi.fn(),
      capturePhoto: vi.fn(),
      ping: vi.fn(),
      identify: vi.fn(),
    },
  };
});

const { forgekeyAPI, resilienceAPI } = await import('../../services/api');
const getStatus = resilienceAPI.getStatus as unknown as ReturnType<typeof vi.fn>;
const recentCommands = forgekeyAPI.recentCommands as unknown as ReturnType<typeof vi.fn>;

const deviceControl = (state: ServiceStatusState): ServiceStatus => ({
  key: 'device_control',
  label: 'Device control',
  description: 'Turning equipment on and off remotely',
  state,
  healthy: state === 'closed',
  since: '2026-08-04T12:00:00Z',
  last_error: state === 'closed' ? null : 'broker unreachable at mqtt-internal:8883',
  degraded_count: state === 'closed' ? 0 : 1,
  total_count: 1,
});

const seedStatus = (services: ServiceStatus[]) => {
  getStatus.mockResolvedValue({
    data: {
      degraded: services.some((s) => !s.healthy),
      checked_at: '2026-08-04T12:00:00Z',
      services,
    },
  });
};

const device = {
  id: 'dev-1',
  mac_address: 'AA:BB:CC:DD:EE:FF',
  name: 'Sewing counter',
  is_online: true,
  last_seen: '2026-08-04T11:00:00Z',
} as never;

const renderInProvider = (node: React.ReactElement) =>
  render(
    <MantineProvider>
      <ServiceStatusProvider>{node}</ServiceStatusProvider>
    </MantineProvider>,
  );

const CONTROL_KEYS = ['blink', 'restart', 'capture', 'ping', 'identify'];

describe('ServiceUnavailableNotice', () => {
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem('token', 'jwt');
    getStatus.mockReset();
    recentCommands.mockReset();
    recentCommands.mockResolvedValue({ data: { results: [] } });
  });

  it('says nothing while the service is healthy', async () => {
    seedStatus([deviceControl('closed')]);

    renderInProvider(
      <ServiceUnavailableNotice service="device_control" message={DEVICE_CONTROL_UNAVAILABLE} />,
    );

    await waitFor(() => expect(getStatus).toHaveBeenCalled());
    expect(screen.queryByTestId('service-unavailable-device_control')).not.toBeInTheDocument();
  });

  it('says nothing when the status endpoint itself fails', async () => {
    getStatus.mockRejectedValue(new Error('503'));

    renderInProvider(
      <ServiceUnavailableNotice service="device_control" message={DEVICE_CONTROL_UNAVAILABLE} />,
    );

    await waitFor(() => expect(getStatus).toHaveBeenCalled());
    expect(screen.queryByTestId('service-unavailable-device_control')).not.toBeInTheDocument();
  });

  it('explains the outage where the control is', async () => {
    seedStatus([deviceControl('open')]);

    renderInProvider(
      <ServiceUnavailableNotice service="device_control" message={DEVICE_CONTROL_UNAVAILABLE} />,
    );

    expect(
      await screen.findByText('Device control unavailable (MQTT broker unreachable)'),
    ).toBeInTheDocument();
  });

  it('falls back to the service label when the caller supplies no message', async () => {
    seedStatus([deviceControl('half_open')]);

    renderInProvider(<ServiceUnavailableNotice service="device_control" />);

    expect(await screen.findByText(/Device control is temporarily unavailable/)).toBeInTheDocument();
  });

  it('never shows the raw last_error', async () => {
    seedStatus([deviceControl('open')]);

    const { container } = renderInProvider(
      <ServiceUnavailableNotice service="device_control" message={DEVICE_CONTROL_UNAVAILABLE} />,
    );

    await screen.findByTestId('service-unavailable-device_control');
    expect(container.textContent).not.toContain('mqtt-internal');
  });
});

describe('DeviceControlsCard service gating', () => {
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem('token', 'jwt');
    getStatus.mockReset();
    recentCommands.mockReset();
    recentCommands.mockResolvedValue({ data: { results: [] } });
  });

  it('disables every command while the MQTT breaker is open', async () => {
    seedStatus([deviceControl('open')]);

    renderInProvider(<DeviceControlsCard device={device} />);

    await screen.findByTestId('device-controls-service-notice');
    CONTROL_KEYS.forEach((key) => {
      expect(screen.getByTestId(`control-btn-${key}`)).toBeDisabled();
    });
  });

  it('leaves every command usable while device control is healthy', async () => {
    seedStatus([deviceControl('closed')]);

    renderInProvider(<DeviceControlsCard device={device} />);

    await waitFor(() => expect(getStatus).toHaveBeenCalled());
    CONTROL_KEYS.forEach((key) => {
      expect(screen.getByTestId(`control-btn-${key}`)).toBeEnabled();
    });
    expect(screen.queryByTestId('device-controls-service-notice')).not.toBeInTheDocument();
  });

  it('leaves every command usable when the status is unknown', async () => {
    getStatus.mockRejectedValue(new Error('network'));

    renderInProvider(<DeviceControlsCard device={device} />);

    await waitFor(() => expect(getStatus).toHaveBeenCalled());
    CONTROL_KEYS.forEach((key) => {
      expect(screen.getByTestId(`control-btn-${key}`)).toBeEnabled();
    });
  });

  it('does not gate a signed-out session on a status it never fetched', async () => {
    localStorage.removeItem('token');
    seedStatus([deviceControl('open')]);

    renderInProvider(<DeviceControlsCard device={device} />);

    await waitFor(() => expect(recentCommands).toHaveBeenCalled());
    expect(getStatus).not.toHaveBeenCalled();
    CONTROL_KEYS.forEach((key) => {
      expect(screen.getByTestId(`control-btn-${key}`)).toBeEnabled();
    });
  });
});
