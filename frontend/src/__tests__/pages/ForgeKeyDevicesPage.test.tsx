/**
 * Tests for the ForgeKey Devices admin page (oms-cuy).
 *
 * Covers ACs:
 *   1. Page lists ESP32 devices with editable location dropdown.
 *   2. Saving a location persists via PATCH on the existing
 *      forgekey/devices/<id>/ endpoint.
 *   3. Non-staff users cannot reach the page.
 *   4. Frontend test covers the edit flow.
 */
import { MantineProvider } from '@mantine/core';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import ForgeKeyDevicesPage from '../../pages/ForgeKeyDevicesPage';
import { forgekeyAPI, inventoryAPI } from '../../services/api';

vi.mock('../../services/api', async () => {
  const actual = await vi.importActual('../../services/api');
  return {
    ...actual,
    forgekeyAPI: {
      listDevices: jest.fn(),
      updateDevice: jest.fn(),
    },
    inventoryAPI: {
      ...actual.inventoryAPI,
      listLocations: jest.fn(),
    },
  };
});

const mockForgekey = forgekeyAPI as jest.Mocked<typeof forgekeyAPI>;
const mockInventory = inventoryAPI as jest.Mocked<typeof inventoryAPI>;

const buildDevice = (overrides: Partial<any> = {}) => ({
  id: 'd1',
  mac_address: 'AA:BB:CC:DD:EE:FF',
  device_type: 1,
  device_type_name: 'traffic_counter',
  name: 'Front door counter',
  description: '',
  firmware_version: '0.1.0',
  last_seen: null,
  is_online: false,
  is_active: true,
  location: null,
  enrollment_photo: null,
  last_photo: null,
  boot_count: null,
  free_heap: null,
  ip: null,
  created_at: '2026-04-27T00:00:00Z',
  updated_at: '2026-04-27T00:00:00Z',
  ...overrides,
});

const buildLocation = (overrides: Partial<any> = {}) => ({
  id: 10,
  name: 'Maker Bay',
  description: '',
  is_active: true,
  parent: null,
  parent_name: null,
  ...overrides,
});

const renderPage = (initialEntry = '/facilities/forgekey-devices') =>
  render(
    <MantineProvider>
      <MemoryRouter initialEntries={[initialEntry]}>
        <Routes>
          <Route path="/facilities/forgekey-devices" element={<ForgeKeyDevicesPage />} />
          <Route path="/" element={<div>HOME</div>} />
        </Routes>
      </MemoryRouter>
    </MantineProvider>,
  );

describe('ForgeKeyDevicesPage', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    localStorage.clear();
  });

  test('non-staff users are redirected away from the page', async () => {
    localStorage.setItem('is_staff', 'false');
    localStorage.setItem('is_superuser', 'false');

    renderPage();

    expect(await screen.findByText('HOME')).toBeInTheDocument();
    expect(mockForgekey.listDevices).not.toHaveBeenCalled();
  });

  test('staff sees device list and can save a location change', async () => {
    localStorage.setItem('is_staff', 'true');

    const device = buildDevice();
    const location = buildLocation({ id: 10, name: 'Maker Bay' });

    mockForgekey.listDevices.mockResolvedValue({ data: { results: [device] } } as any);
    mockInventory.listLocations.mockResolvedValue({
      data: { results: [location] },
    } as any);
    mockForgekey.updateDevice.mockResolvedValue({
      data: { ...device, location: 10 },
    } as any);

    renderPage();

    expect(await screen.findByText('Front door counter')).toBeInTheDocument();
    expect(screen.getByText('AA:BB:CC:DD:EE:FF')).toBeInTheDocument();

    const select = screen.getByLabelText(/Location for Front door counter/i) as HTMLSelectElement;
    expect(select.value).toBe('');
    expect(screen.getByRole('option', { name: 'Maker Bay' })).toBeInTheDocument();

    fireEvent.change(select, { target: { value: '10' } });

    await waitFor(() => {
      expect(mockForgekey.updateDevice).toHaveBeenCalledWith('d1', { location: 10 });
    });

    await waitFor(() => {
      expect(screen.getByText('saved')).toBeInTheDocument();
    });

    expect((screen.getByLabelText(/Location for Front door counter/i) as HTMLSelectElement).value).toBe('10');
  });
});
