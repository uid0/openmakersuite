/**
 * Device-control gating on the ForgeKey device detail page.
 *
 * Every control here ends in an MQTT publish, so when the device_control
 * service is open the relay channels, the LED blink and the OTA send all go
 * grey with a reason attached — and none of them do while it is closed.
 */
import { MantineProvider } from '@mantine/core';
import { render, screen, waitFor } from '@testing-library/react';
import React from 'react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { ServiceStatusProvider } from '../../contexts/ServiceStatusContext';
import ForgeKeyDeviceDetailPage from '../../pages/ForgeKeyDeviceDetailPage';
import { ServiceStatusState } from '../../types';

vi.mock('../../services/api', async () => {
  const actual = await vi.importActual<typeof import('../../services/api')>(
    '../../services/api',
  );
  return {
    ...actual,
    resilienceAPI: { getStatus: vi.fn() },
    forgekeyAPI: {
      getDevice: vi.fn(),
      getOccupancy: vi.fn(),
      getTemperature: vi.fn(),
      recentCommands: vi.fn(),
      listDeviceTypes: vi.fn(),
      listIndicatorBindings: vi.fn(),
      setRelayChannel: vi.fn(),
      blink: vi.fn(),
      firmwareUpdate: vi.fn(),
    },
  };
});

vi.mock('recharts', async () => ({
  __esModule: true,
  ResponsiveContainer: ({ children }: any) => <div data-testid="chart">{children}</div>,
  LineChart: ({ children }: any) => <div>{children}</div>,
  Line: () => null,
  XAxis: () => null,
  YAxis: () => null,
  CartesianGrid: () => null,
  Tooltip: () => null,
}));

const { forgekeyAPI, resilienceAPI } = await import('../../services/api');
const api = forgekeyAPI as unknown as Record<string, ReturnType<typeof vi.fn>>;
const getStatus = resilienceAPI.getStatus as unknown as ReturnType<typeof vi.fn>;

const seedDevice = () => {
  api.getDevice.mockResolvedValue({
    data: {
      id: 'dev-1',
      mac_address: 'AA:BB:CC:DD:EE:FF',
      name: 'Laser interlock',
      is_online: true,
      last_seen: '2026-08-04T11:00:00Z',
      capabilities: ['power_relay', 'status_led'],
      capabilities_announced_at: '2026-08-04T10:00:00Z',
      relay_channels: [{ channel: 1, on: true }],
      indicator_state: {},
    },
  });
  api.getOccupancy.mockResolvedValue({
    data: { device: 'AA:BB:CC:DD:EE:FF', since: '', current_occupancy: 0, events: [] },
  });
  api.getTemperature.mockResolvedValue({
    data: {
      device: 'AA:BB:CC:DD:EE:FF',
      since: '',
      latest_temperature_c: null,
      latest_humidity_percent: null,
      readings: [],
    },
  });
  api.recentCommands.mockResolvedValue({ data: { results: [] } });
  api.listDeviceTypes.mockResolvedValue({ data: [] });
  api.listIndicatorBindings.mockResolvedValue({ data: [] });
};

const seedStatus = (state: ServiceStatusState) => {
  getStatus.mockResolvedValue({
    data: {
      degraded: state !== 'closed',
      checked_at: '2026-08-04T12:00:00Z',
      services: [
        {
          key: 'device_control',
          label: 'Device control',
          description: 'Turning equipment on and off remotely',
          state,
          healthy: state === 'closed',
          since: '2026-08-04T12:00:00Z',
          last_error: null,
          degraded_count: state === 'closed' ? 0 : 1,
          total_count: 1,
        },
      ],
    },
  });
};

const renderPage = () =>
  render(
    <MantineProvider>
      <ServiceStatusProvider>
        <MemoryRouter initialEntries={['/facilities/forgekey-devices/dev-1']}>
          <Routes>
            <Route
              path="/facilities/forgekey-devices/:id"
              element={<ForgeKeyDeviceDetailPage />}
            />
          </Routes>
        </MemoryRouter>
      </ServiceStatusProvider>
    </MantineProvider>,
  );

describe('ForgeKeyDeviceDetailPage device-control gating', () => {
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem('token', 'jwt');
    localStorage.setItem('is_staff', 'true');
    getStatus.mockReset();
    Object.values(api).forEach((fn) => fn.mockReset());
    seedDevice();
  });

  it('greys out the relay, blink and OTA controls with a reason when MQTT is down', async () => {
    seedStatus('open');

    renderPage();

    expect(await screen.findByTestId('relay-device-control-notice')).toHaveTextContent(
      'Device control unavailable (MQTT broker unreachable)',
    );
    expect(screen.getByTestId('relay-channel-1-enable')).toBeDisabled();
    expect(screen.getByTestId('relay-channel-1-disable')).toBeDisabled();
    expect(screen.getByTestId('relay-channel-2-enable')).toBeDisabled();
    expect(screen.getByRole('button', { name: /blink led/i })).toBeDisabled();
    expect(screen.getByTestId('ota-device-control-notice')).toBeInTheDocument();
  });

  it('leaves the controls alone while device control is healthy', async () => {
    seedStatus('closed');

    renderPage();

    await waitFor(() => expect(getStatus).toHaveBeenCalled());
    expect(await screen.findByTestId('relay-channel-1-enable')).toBeEnabled();
    expect(screen.getByRole('button', { name: /blink led/i })).toBeEnabled();
    expect(screen.queryByTestId('relay-device-control-notice')).not.toBeInTheDocument();
    expect(screen.queryByTestId('ota-device-control-notice')).not.toBeInTheDocument();
  });
});
