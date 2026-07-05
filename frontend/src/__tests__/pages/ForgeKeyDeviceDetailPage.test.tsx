/**
 * Tests for the ForgeKey device detail page (oms-yyg AC-4 + oms-zta).
 *
 * Covers:
 *   - chart + occupancy summary populate from /occupancy
 *   - Device Controls card renders all five buttons via the new component
 *   - non-staff users get redirected
 *   - OTA disabled state
 */
import { MantineProvider } from '@mantine/core';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import ForgeKeyDeviceDetailPage from '../../pages/ForgeKeyDeviceDetailPage';
import { forgekeyAPI } from '../../services/api';

vi.mock('../../services/api', async () => {
  const actual = await vi.importActual('../../services/api');
  return {
    ...actual,
    forgekeyAPI: {
      getDevice: jest.fn(),
      getOccupancy: jest.fn(),
      getTemperature: jest.fn(),
      restart: jest.fn(),
      capturePhoto: jest.fn(),
      blink: jest.fn(),
      firmwareUpdate: jest.fn(),
      ping: jest.fn(),
      identify: jest.fn(),
      recentCommands: jest.fn(),
      setRelayChannel: jest.fn(),
      // IndicatorManagementCard (mounted for indicator devices) probes these on
      // load; the test device is a people_counter, so it renders nothing.
      listDeviceTypes: jest.fn(),
      listIndicatorBindings: jest.fn(),
    },
  };
});

// recharts uses ResponsiveContainer which needs a non-zero size in JSDOM —
// stub it so tests can assert on the surrounding panel without rendering SVG.
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

const mockApi = forgekeyAPI as jest.Mocked<typeof forgekeyAPI>;

const buildDevice = (overrides: Partial<any> = {}) => ({
  id: 'dev-1',
  mac_address: 'AA:BB:CC:DD:EE:FF',
  device_type: 1,
  device_type_name: 'people_counter',
  name: 'Sewing counter',
  description: '',
  firmware_version: '1.0.0',
  last_seen: '2026-05-01T03:00:00Z',
  is_online: true,
  is_active: true,
  location: null,
  enrollment_photo: null,
  last_photo: null,
  boot_count: 5,
  free_heap: null,
  ip: null,
  capabilities: [],
  capabilities_announced_at: null,
  relay_channels: [],
  indicator_state: {},
  created_at: '2026-04-27T00:00:00Z',
  updated_at: '2026-05-01T03:00:00Z',
  ...overrides,
});

const renderAt = (path: string) =>
  render(
    <MantineProvider>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/facilities/forgekey-devices/:id" element={<ForgeKeyDeviceDetailPage />} />
          <Route path="/" element={<div>home</div>} />
        </Routes>
      </MemoryRouter>
    </MantineProvider>,
  );

const seedHappyPath = () => {
  mockApi.getDevice.mockResolvedValue({ data: buildDevice() } as any);
  mockApi.getOccupancy.mockResolvedValue({
    data: {
      device: 'AA:BB:CC:DD:EE:FF',
      since: '2026-04-30T03:00:00Z',
      current_occupancy: 4,
      events: [],
    },
  } as any);
  mockApi.getTemperature.mockResolvedValue({
    data: {
      device: 'AA:BB:CC:DD:EE:FF',
      since: '2026-04-30T03:00:00Z',
      latest_temperature_c: null,
      latest_humidity_percent: null,
      readings: [],
    },
  } as any);
  mockApi.recentCommands.mockResolvedValue({
    data: { device: 'AA:BB:CC:DD:EE:FF', results: [] },
  } as any);
};

describe('ForgeKeyDeviceDetailPage', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    localStorage.setItem('is_staff', 'true');
    // Default: no indicator device type, so IndicatorManagementCard is inert.
    mockApi.listDeviceTypes.mockResolvedValue({ data: [] } as any);
    mockApi.listIndicatorBindings.mockResolvedValue({ data: [] } as any);
  });

  afterEach(() => {
    localStorage.removeItem('is_staff');
    localStorage.removeItem('is_superuser');
  });

  it('enables a power-relay channel from the capability card (ga-40w)', async () => {
    seedHappyPath();
    mockApi.getDevice.mockResolvedValue({
      data: buildDevice({
        capabilities: ['power_relay'],
        capabilities_announced_at: '2026-05-01T03:00:00Z',
      }),
    } as any);
    mockApi.setRelayChannel.mockResolvedValue({ data: { command_id: 'c1' } } as any);

    renderAt('/facilities/forgekey-devices/dev-1');

    fireEvent.click(await screen.findByTestId('relay-channel-1-enable'));
    await waitFor(() => expect(mockApi.setRelayChannel).toHaveBeenCalledWith('dev-1', 1, true));
  });

  it('disables a power-relay channel from the capability card (ga-40w)', async () => {
    seedHappyPath();
    mockApi.getDevice.mockResolvedValue({
      data: buildDevice({ capabilities: ['power_relay'] }),
    } as any);
    mockApi.setRelayChannel.mockResolvedValue({ data: {} } as any);

    renderAt('/facilities/forgekey-devices/dev-1');

    fireEvent.click(await screen.findByTestId('relay-channel-2-disable'));
    await waitFor(() => expect(mockApi.setRelayChannel).toHaveBeenCalledWith('dev-1', 2, false));
  });

  it('surfaces an error when a relay-channel command fails (ga-40w)', async () => {
    seedHappyPath();
    mockApi.getDevice.mockResolvedValue({
      data: buildDevice({ capabilities: ['power_relay'] }),
    } as any);
    mockApi.setRelayChannel.mockRejectedValue(new Error('broker down'));

    renderAt('/facilities/forgekey-devices/dev-1');

    fireEvent.click(await screen.findByTestId('relay-channel-1-enable'));
    expect(await screen.findByTestId('relay-channel-error')).toBeInTheDocument();
  });

  it('surfaces live per-channel relay on/off from the cached sub-state (op-2cr)', async () => {
    seedHappyPath();
    mockApi.getDevice.mockResolvedValue({
      data: buildDevice({
        capabilities: ['power_relay'],
        relay_channels: [
          { channel: 1, on: true },
          { channel: 2, on: false },
        ],
      }),
    } as any);

    renderAt('/facilities/forgekey-devices/dev-1');

    const ch1 = await screen.findByTestId('relay-channel-1');
    const ch1State = within(ch1).getByTestId('relay-channel-state');
    expect(ch1State).toHaveTextContent(/on/i);
    expect(ch1State).toHaveAttribute('data-on', 'true');

    const ch2 = screen.getByTestId('relay-channel-2');
    const ch2State = within(ch2).getByTestId('relay-channel-state');
    expect(ch2State).toHaveTextContent(/off/i);
    expect(ch2State).toHaveAttribute('data-on', 'false');
  });

  it('notes when live relay state has not been reported yet (op-2cr)', async () => {
    seedHappyPath();
    mockApi.getDevice.mockResolvedValue({
      data: buildDevice({ capabilities: ['power_relay'], relay_channels: [] }),
    } as any);

    renderAt('/facilities/forgekey-devices/dev-1');

    expect(await screen.findByText(/live on\/off state not reported yet/i)).toBeInTheDocument();
  });

  it('surfaces the live indicator colour from the cached sub-state (op-2cr)', async () => {
    seedHappyPath();
    mockApi.getDevice.mockResolvedValue({
      data: buildDevice({
        capabilities: ['status_led'],
        indicator_state: { color: 'green', pattern: 'solid' },
      }),
    } as any);

    renderAt('/facilities/forgekey-devices/dev-1');

    const state = await screen.findByTestId('indicator-state');
    expect(within(state).getByTestId('indicator-state-color')).toHaveTextContent('green');
    expect(within(state).getByTestId('indicator-state-swatch')).toBeInTheDocument();
  });

  it('shows a placeholder when no indicator state has been reported (op-2cr)', async () => {
    seedHappyPath();
    mockApi.getDevice.mockResolvedValue({
      data: buildDevice({ capabilities: ['status_led'], indicator_state: {} }),
    } as any);

    renderAt('/facilities/forgekey-devices/dev-1');

    expect(await screen.findByTestId('indicator-state')).toHaveTextContent('State: —');
  });

  it('renders the temperature chart when the device reports readings', async () => {
    seedHappyPath();
    mockApi.getTemperature.mockResolvedValue({
      data: {
        device: 'AA:BB:CC:DD:EE:FF',
        since: '2026-04-30T03:00:00Z',
        latest_temperature_c: 21.4,
        latest_humidity_percent: 47.1,
        readings: [
          {
            id: 'r1',
            device: 'dev-1',
            sensor_kind: 'temperature_sensor',
            temperature_c: 21.4,
            humidity_percent: 47.1,
            recorded_at: '2026-05-01T03:00:00Z',
            raw_payload: {},
          },
        ],
      },
    } as any);

    renderAt('/facilities/forgekey-devices/dev-1');

    expect(await screen.findByTestId('latest-temperature')).toHaveTextContent('21.4°C');
  });

  it('renders all five command buttons for staff', async () => {
    seedHappyPath();
    renderAt('/facilities/forgekey-devices/dev-1');

    await waitFor(() => expect(mockApi.getDevice).toHaveBeenCalledWith('dev-1'));
    expect(await screen.findByTestId('control-btn-restart')).toBeInTheDocument();
    expect(screen.getByTestId('control-btn-blink')).toBeInTheDocument();
    expect(screen.getByTestId('control-btn-capture')).toBeInTheDocument();
    expect(screen.getByTestId('control-btn-ping')).toBeInTheDocument();
    expect(screen.getByTestId('control-btn-identify')).toBeInTheDocument();
  });

  it('redirects non-staff away from the page', async () => {
    localStorage.removeItem('is_staff');
    renderAt('/facilities/forgekey-devices/dev-1');
    expect(await screen.findByText('home')).toBeInTheDocument();
    expect(mockApi.getDevice).not.toHaveBeenCalled();
  });

  it('dispatches the restart command when the restart button is clicked', async () => {
    seedHappyPath();
    mockApi.restart.mockResolvedValue({
      data: {
        status: 'restart command sent',
        device: 'AA:BB:CC:DD:EE:FF',
        topic: 'forgekey/AA-BB-CC-DD-EE-FF/command',
        command_id: 'cmd-1',
        dispatched_at: '2026-05-01T03:05:00Z',
      },
    } as any);

    renderAt('/facilities/forgekey-devices/dev-1');
    const restartButton = await screen.findByTestId('control-btn-restart');

    fireEvent.click(restartButton);
    await waitFor(() => expect(mockApi.restart).toHaveBeenCalledWith('dev-1'));
  });

  it('dispatches the identify command with a default duration', async () => {
    seedHappyPath();
    mockApi.identify.mockResolvedValue({
      data: {
        status: 'identify command sent',
        device: 'AA:BB:CC:DD:EE:FF',
        topic: 'forgekey/AA-BB-CC-DD-EE-FF/command',
        command_id: 'cmd-2',
        dispatched_at: '2026-05-01T03:05:00Z',
      },
    } as any);

    renderAt('/facilities/forgekey-devices/dev-1');
    const identifyButton = await screen.findByTestId('control-btn-identify');

    fireEvent.click(identifyButton);
    await waitFor(() =>
      expect(mockApi.identify).toHaveBeenCalledWith('dev-1', { duration_s: 30 }),
    );
  });

  it('shows acknowledged feedback once the recent-commands endpoint reports an ack', async () => {
    seedHappyPath();
    mockApi.restart.mockResolvedValue({
      data: {
        status: 'restart command sent',
        device: 'AA:BB:CC:DD:EE:FF',
        topic: 'forgekey/AA-BB-CC-DD-EE-FF/command',
        command_id: 'cmd-9',
        dispatched_at: '2026-05-01T03:05:00Z',
      },
    } as any);
    // After dispatch, the endpoint reports the row as acked.
    mockApi.recentCommands.mockResolvedValue({
      data: {
        device: 'AA:BB:CC:DD:EE:FF',
        results: [
          {
            id: 'cmd-9',
            command: 'restart',
            payload: {},
            sent_by: 1,
            sent_by_username: 'alice',
            sent_at: new Date().toISOString(),
            ack_status: 'acked',
            effective_ack_status: 'acked',
            ack_at: new Date().toISOString(),
            ack_payload: { status: 'ok' },
          },
        ],
      },
    } as any);

    renderAt('/facilities/forgekey-devices/dev-1');
    const restartButton = await screen.findByTestId('control-btn-restart');
    fireEvent.click(restartButton);

    await waitFor(() => expect(mockApi.restart).toHaveBeenCalled());

    // The component polls the recent-commands endpoint every 2s while a
    // command is pending; wait long enough for one cycle to land.
    await waitFor(
      () => {
        const feedback = screen.queryByTestId('control-feedback-restart');
        expect(feedback?.getAttribute('data-state')).toBe('acked');
      },
      { timeout: 5_000 },
    );
  });

  it('disables OTA send until both fields are populated', async () => {
    seedHappyPath();
    mockApi.firmwareUpdate.mockResolvedValue({
      data: {
        status: 'firmware_update command sent',
        device: 'AA:BB:CC:DD:EE:FF',
        topic: 'forgekey/x/command',
        dispatched_at: '2026-05-01T03:06:00Z',
      },
    } as any);

    renderAt('/facilities/forgekey-devices/dev-1');
    const otaButton = await screen.findByRole('button', { name: /send ota/i });
    expect(otaButton).toBeDisabled();

    fireEvent.change(screen.getByPlaceholderText(/version/i), { target: { value: '2.3.4' } });
    fireEvent.change(screen.getByPlaceholderText(/firmware url/i), {
      target: { value: 'https://example.test/fw.bin' },
    });

    await waitFor(() => expect(otaButton).not.toBeDisabled());
    fireEvent.click(otaButton);
    await waitFor(() =>
      expect(mockApi.firmwareUpdate).toHaveBeenCalledWith('dev-1', {
        version: '2.3.4',
        url: 'https://example.test/fw.bin',
      }),
    );
  });

  describe('device-type-aware sections (op-3u4)', () => {
    it('greys out the occupancy section for an indicator device', async () => {
      seedHappyPath();
      mockApi.getDevice.mockResolvedValue({
        data: buildDevice({
          device_type: 9,
          device_type_name: 'Indicator/Status Light',
          capabilities: ['status_led', 'status_matrix'],
        }),
      } as any);
      mockApi.listDeviceTypes.mockResolvedValue({
        data: [{ id: 9, name: 'Indicator/Status Light', code: 'indicator' }],
      } as any);
      // An existing binding keeps IndicatorManagementCard from fetching assets/locations.
      mockApi.listIndicatorBindings.mockResolvedValue({
        data: [
          {
            id: 'b1',
            device: 'dev-1',
            asset: null,
            asset_name: null,
            location: 5,
            location_name: 'Lab',
            last_status: null,
            last_synced_at: null,
          },
        ],
      } as any);

      renderAt('/facilities/forgekey-devices/dev-1');

      const gate = await screen.findByTestId('section-gate-occupancy');
      expect(gate).toHaveAttribute('aria-disabled', 'true');
      expect(
        within(gate).getByText(/not applicable for this device type/i),
      ).toBeInTheDocument();
      // Greyed, not hidden: the section content is still in the DOM.
      expect(within(gate).getByText('Occupancy (last 24h)')).toBeInTheDocument();
    });

    it('renders the occupancy section normally for a people-counter device', async () => {
      seedHappyPath();
      mockApi.listDeviceTypes.mockResolvedValue({
        data: [{ id: 1, name: 'People Counter', code: 'people_counter' }],
      } as any);

      renderAt('/facilities/forgekey-devices/dev-1');

      const gate = await screen.findByTestId('section-gate-occupancy');
      expect(gate).not.toHaveAttribute('aria-disabled');
      expect(
        within(gate).queryByText(/not applicable for this device type/i),
      ).not.toBeInTheDocument();
      expect(within(gate).getByText('Occupancy (last 24h)')).toBeInTheDocument();
    });

    it('renders the occupancy section normally when type and capabilities are unknown', async () => {
      // Fresh device: no announced capabilities, device-types list empty (beforeEach
      // default) so the type cannot be resolved -> relevance unknown -> render.
      seedHappyPath();

      renderAt('/facilities/forgekey-devices/dev-1');

      const gate = await screen.findByTestId('section-gate-occupancy');
      expect(gate).not.toHaveAttribute('aria-disabled');
      expect(within(gate).getByText('Occupancy (last 24h)')).toBeInTheDocument();
    });
  });
});
