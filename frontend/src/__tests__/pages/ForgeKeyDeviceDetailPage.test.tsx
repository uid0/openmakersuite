/**
 * Tests for the ForgeKey device detail page (oms-yyg AC-4).
 *
 * Covers:
 *   - chart + occupancy summary populate from /occupancy
 *   - control buttons hit the right command endpoint and surface ack state
 *   - non-staff users get redirected
 */
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
  created_at: '2026-04-27T00:00:00Z',
  updated_at: '2026-05-01T03:00:00Z',
  ...overrides,
});

const renderAt = (path: string) =>
  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/facilities/forgekey-devices/:id" element={<ForgeKeyDeviceDetailPage />} />
        <Route path="/" element={<div>home</div>} />
      </Routes>
    </MemoryRouter>,
  );

describe('ForgeKeyDeviceDetailPage', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    localStorage.setItem('is_staff', 'true');
  });

  afterEach(() => {
    localStorage.removeItem('is_staff');
    localStorage.removeItem('is_superuser');
  });

  it('renders occupancy summary and controls for staff', async () => {
    mockApi.getDevice.mockResolvedValue({ data: buildDevice() } as any);
    mockApi.getOccupancy.mockResolvedValue({
      data: {
        device: 'AA:BB:CC:DD:EE:FF',
        since: '2026-04-30T03:00:00Z',
        current_occupancy: 4,
        events: [
          {
            id: 'e1',
            device: 'dev-1',
            sensor_kind: 'people_counter',
            count_in: 1,
            count_out: 0,
            occupancy_delta: 1,
            event_timestamp_utc: '2026-05-01T02:30:00Z',
            ingested_at: '2026-05-01T02:30:01Z',
            raw_payload: {},
          },
        ],
      },
    } as any);

    renderAt('/facilities/forgekey-devices/dev-1');

    await waitFor(() => expect(mockApi.getDevice).toHaveBeenCalledWith('dev-1'));
    expect(await screen.findByTestId('current-occupancy')).toHaveTextContent('4');
    expect(screen.getByRole('button', { name: /restart/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /capture photo/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^blink$/i })).toBeInTheDocument();
  });

  it('redirects non-staff away from the page', async () => {
    localStorage.removeItem('is_staff');
    renderAt('/facilities/forgekey-devices/dev-1');
    expect(await screen.findByText('home')).toBeInTheDocument();
    expect(mockApi.getDevice).not.toHaveBeenCalled();
  });

  it('dispatches restart command and shows the dispatched ack', async () => {
    mockApi.getDevice.mockResolvedValue({ data: buildDevice() } as any);
    mockApi.getOccupancy.mockResolvedValue({
      data: {
        device: 'AA:BB:CC:DD:EE:FF',
        since: '2026-04-30T03:00:00Z',
        current_occupancy: 0,
        events: [],
      },
    } as any);
    mockApi.restart.mockResolvedValue({
      data: {
        status: 'restart command sent',
        device: 'AA:BB:CC:DD:EE:FF',
        topic: 'forgekey/AA-BB-CC-DD-EE-FF/command',
        dispatched_at: '2026-05-01T03:05:00Z',
      },
    } as any);

    renderAt('/facilities/forgekey-devices/dev-1');
    const restartButton = await screen.findByRole('button', { name: /restart/i });

    fireEvent.click(restartButton);
    await waitFor(() => expect(mockApi.restart).toHaveBeenCalledWith('dev-1'));
    expect(await screen.findByText(/sent/i)).toBeInTheDocument();
  });

  it('disables OTA send until both fields are populated', async () => {
    mockApi.getDevice.mockResolvedValue({ data: buildDevice() } as any);
    mockApi.getOccupancy.mockResolvedValue({
      data: {
        device: 'AA:BB:CC:DD:EE:FF',
        since: '2026-04-30T03:00:00Z',
        current_occupancy: 0,
        events: [],
      },
    } as any);
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
