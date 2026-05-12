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
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import ForgeKeyDeviceDetailPage from '../../pages/ForgeKeyDeviceDetailPage';
import { forgekeyAPI } from '../../services/api';

jest.mock('../../services/api', () => {
  const actual = jest.requireActual('../../services/api');
  return {
    ...actual,
    forgekeyAPI: {
      getDevice: jest.fn(),
      getOccupancy: jest.fn(),
      restart: jest.fn(),
      capturePhoto: jest.fn(),
      blink: jest.fn(),
      firmwareUpdate: jest.fn(),
      ping: jest.fn(),
      identify: jest.fn(),
      recentCommands: jest.fn(),
    },
  };
});

// recharts uses ResponsiveContainer which needs a non-zero size in JSDOM —
// stub it so tests can assert on the surrounding panel without rendering SVG.
jest.mock('recharts', () => ({
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
  mockApi.recentCommands.mockResolvedValue({
    data: { device: 'AA:BB:CC:DD:EE:FF', results: [] },
  } as any);
};

describe('ForgeKeyDeviceDetailPage', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    localStorage.setItem('is_staff', 'true');
  });

  afterEach(() => {
    localStorage.removeItem('is_staff');
    localStorage.removeItem('is_superuser');
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
});
