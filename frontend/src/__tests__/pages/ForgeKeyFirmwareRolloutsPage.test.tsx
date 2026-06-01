/**
 * Tests for the ForgeKey firmware rollouts management page.
 *
 * Covers: non-staff redirect, the rollout cards (version + status + pace),
 * and starting a draft rollout.
 */
import { MantineProvider } from '@mantine/core';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import ForgeKeyFirmwareRolloutsPage, {
  defaultPioEnv,
} from '../../pages/ForgeKeyFirmwareRolloutsPage';
import { forgekeyAPI } from '../../services/api';

vi.mock('../../services/api', async () => {
  const actual = await vi.importActual('../../services/api');
  return {
    ...actual,
    forgekeyAPI: {
      listFirmwareRollouts: jest.fn(),
      listFirmwareVersions: jest.fn(),
      createFirmwareRollout: jest.fn(),
      startRollout: jest.fn(),
      pauseRollout: jest.fn(),
      cancelRollout: jest.fn(),
      advanceRollout: jest.fn(),
      listFirmwareBuilds: jest.fn(),
      listDeviceTypes: jest.fn(),
      createFirmwareBuild: jest.fn(),
      cancelFirmwareBuild: jest.fn(),
    },
  };
});

const mockApi = forgekeyAPI as jest.Mocked<typeof forgekeyAPI>;

const buildRollout = (overrides: Partial<any> = {}) => ({
  id: 'r1',
  firmware_version: 'fw1',
  firmware_version_string: '2.0.0',
  device_type_name: 'AC Relay',
  name: '',
  status: 'draft',
  batch_size_percent: 25,
  interval_minutes: 60,
  created_by: 1,
  created_by_username: 'admin',
  created_at: '2026-05-01T00:00:00Z',
  updated_at: '2026-05-01T00:00:00Z',
  started_at: null,
  completed_at: null,
  last_advanced_at: null,
  progress: { total: 4, on_target: 0, pending: 0, in_progress: 0, failed: 0, remaining: 4 },
  ...overrides,
});

const renderPage = () =>
  render(
    <MantineProvider>
      <MemoryRouter initialEntries={['/facilities/forgekey-rollouts']}>
        <Routes>
          <Route
            path="/facilities/forgekey-rollouts"
            element={<ForgeKeyFirmwareRolloutsPage />}
          />
          <Route path="/" element={<div>HOME</div>} />
        </Routes>
      </MemoryRouter>
    </MantineProvider>,
  );

describe('ForgeKeyFirmwareRolloutsPage', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    localStorage.clear();
    mockApi.listFirmwareVersions.mockResolvedValue({ data: [] } as any);
    mockApi.listFirmwareBuilds.mockResolvedValue({ data: [] } as any);
    mockApi.listDeviceTypes.mockResolvedValue({ data: [] } as any);
  });

  test('non-staff users are redirected', async () => {
    localStorage.setItem('is_staff', 'false');
    localStorage.setItem('is_superuser', 'false');

    renderPage();

    expect(await screen.findByText('HOME')).toBeInTheDocument();
    expect(mockApi.listFirmwareRollouts).not.toHaveBeenCalled();
  });

  test('staff sees rollout cards with version, status, and pace', async () => {
    localStorage.setItem('is_staff', 'true');
    mockApi.listFirmwareRollouts.mockResolvedValue({ data: [buildRollout()] } as any);

    renderPage();

    expect(await screen.findByText('2.0.0')).toBeInTheDocument();
    expect(screen.getByTestId('rollout-status-r1')).toHaveTextContent('draft');
    expect(screen.getByText(/25% of the fleet every 60 min/)).toBeInTheDocument();
  });

  test('starting a draft rollout flips it to active', async () => {
    localStorage.setItem('is_staff', 'true');
    mockApi.listFirmwareRollouts.mockResolvedValue({ data: [buildRollout()] } as any);
    mockApi.startRollout.mockResolvedValue({
      data: buildRollout({
        status: 'active',
        dispatched: 1,
        progress: { total: 4, on_target: 0, pending: 1, in_progress: 0, failed: 0, remaining: 3 },
      }),
    } as any);

    renderPage();

    const startBtn = await screen.findByText('Start');
    fireEvent.click(startBtn);

    await waitFor(() => expect(mockApi.startRollout).toHaveBeenCalledWith('r1'));
    await waitFor(() =>
      expect(screen.getByTestId('rollout-status-r1')).toHaveTextContent('active'),
    );
  });

  test('staff sees the firmware build form', async () => {
    localStorage.setItem('is_staff', 'true');
    mockApi.listFirmwareRollouts.mockResolvedValue({ data: [] } as any);

    renderPage();

    expect(await screen.findByTestId('build-create')).toBeInTheDocument();
    // Disabled until device type + env + version are filled in.
    expect(screen.getByTestId('build-submit')).toBeDisabled();
  });

  test('shows queued firmware builds', async () => {
    localStorage.setItem('is_staff', 'true');
    mockApi.listFirmwareRollouts.mockResolvedValue({ data: [] } as any);
    mockApi.listFirmwareBuilds.mockResolvedValue({
      data: [
        {
          id: 'b1',
          device_type: 3,
          device_type_name: 'E-paper',
          pio_env: 'seeed_xiao_epaper',
          source_ref: 'main',
          version: '5.0.0',
          mandatory: false,
          release_notes: '',
          status: 'queued',
          ca_fingerprint: '',
          commit_sha: '',
          log: '',
          error_message: '',
          firmware_version: null,
          firmware_version_string: null,
          requested_by_username: 'admin',
          requested_at: '2026-06-01T00:00:00Z',
          started_at: null,
          completed_at: null,
        },
      ],
    } as any);

    renderPage();

    expect(await screen.findByTestId('build-b1')).toBeInTheDocument();
    expect(screen.getByText('5.0.0')).toBeInTheDocument();
    expect(screen.getByText('seeed_xiao_epaper')).toBeInTheDocument();
  });

  test('defaultPioEnv maps device codes to PlatformIO envs', () => {
    expect(defaultPioEnv('epaper_screen')).toBe('seeed_xiao_epaper');
    expect(defaultPioEnv('temperature_sensor')).toBe('seeed_xiao_esp32s3_temperature');
    expect(defaultPioEnv('ac_relay')).toBe('seeed_xiao_esp32s3');
  });

  test('opens a build log modal', async () => {
    localStorage.setItem('is_staff', 'true');
    mockApi.listFirmwareRollouts.mockResolvedValue({ data: [] } as any);
    mockApi.listFirmwareBuilds.mockResolvedValue({
      data: [
        {
          id: 'b1',
          device_type: 3,
          device_type_name: 'E-paper',
          pio_env: 'seeed_xiao_epaper',
          source_ref: 'main',
          version: '5.0.0',
          mandatory: false,
          release_notes: '',
          status: 'failed',
          ca_fingerprint: '',
          commit_sha: '',
          log: 'cloning...\npio run\n[ERROR] boom',
          error_message: 'pio failed',
          firmware_version: null,
          firmware_version_string: null,
          requested_by_username: 'admin',
          requested_at: '2026-06-01T00:00:00Z',
          started_at: '2026-06-01T00:01:00Z',
          completed_at: '2026-06-01T00:05:00Z',
        },
      ],
    } as any);

    renderPage();

    fireEvent.click(await screen.findByTestId('build-log-b1'));
    const log = await screen.findByTestId('build-log');
    expect(log).toHaveTextContent('[ERROR] boom');
  });
});
