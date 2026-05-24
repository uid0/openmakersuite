/**
 * Tests for ThermostatFormPage (oms-gzycmj).
 */
import { MantineProvider } from '@mantine/core';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import ThermostatFormPage from '../../pages/ThermostatFormPage';
import {
  assetsAPI,
  climateAPI,
  electricalSafetyAPI,
  inventoryAPI,
  Thermostat,
} from '../../services/api';

vi.mock('../../services/api');

const mockNavigate = jest.fn();
vi.mock('react-router-dom', async () => ({
  ...(await vi.importActual('react-router-dom')),
  useNavigate: () => mockNavigate,
}));

const mockInventoryAPI = inventoryAPI as jest.Mocked<typeof inventoryAPI>;
const mockAssetsAPI = assetsAPI as jest.Mocked<typeof assetsAPI>;
const mockClimateAPI = climateAPI as jest.Mocked<typeof climateAPI>;
const mockElectricalSafetyAPI = electricalSafetyAPI as jest.Mocked<typeof electricalSafetyAPI>;

const locations = [
  { id: 12, name: 'Wood Shop', description: '', created_at: '', updated_at: '' },
  { id: 13, name: 'Front Hall', description: '', created_at: '', updated_at: '' },
];

const assets = [
  {
    id: 'asset-88',
    name: 'RTU-3',
    description: '',
    serial_number: 'SN1',
    asset_tag: 'TAG-88',
    inventory_item: null,
    inventory_item_name: '',
    manufacturer: null,
    manufacturer_name: '',
    display_manufacturer: '',
    date_received: '',
    amount_paid: '',
    is_donation: false,
    donor_name: '',
    acquisition_display: '',
    category: 1,
    category_name: '',
    location: 12,
    location_name: 'Wood Shop',
    product_url: '',
    wiki_page_url: '',
    maintenance_plan: '',
    image: null,
    image_url: '',
    thumbnail_url: null,
    manual_pdf: null,
    manual_pdf_url: '',
    needs_review: false,
    sigs: [],
    sig_names: [],
    is_active: true,
    is_loanable: false,
    deactivated_at: null,
    deactivated_by: null,
    deactivated_by_username: null,
    deactivation_reason: '',
    created_at: '',
    updated_at: '',
  } as any,
];

const renderAt = (path: string) =>
  render(
    <MantineProvider>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route
            path="/facilities/climate/thermostats/new"
            element={<ThermostatFormPage />}
          />
          <Route
            path="/facilities/climate/thermostats/:id/edit"
            element={<ThermostatFormPage />}
          />
        </Routes>
      </MemoryRouter>
    </MantineProvider>,
  );

const apiResponse = <T,>(data: T) => ({
  data,
  status: 200,
  statusText: 'OK',
  headers: {},
  config: {} as any,
});

describe('ThermostatFormPage', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockInventoryAPI.listLocations.mockResolvedValue(
      apiResponse({ results: locations }) as any,
    );
    mockAssetsAPI.listAssets.mockResolvedValue(
      apiResponse({ results: assets }) as any,
    );
    mockElectricalSafetyAPI.getAssetPowerChain.mockResolvedValue(
      apiResponse({
        asset: { id: 'asset-88', name: 'RTU-3', type: 'asset', label: 'RTU-3' },
        chain: [],
      }) as any,
    );
  });

  it('creates a thermostat and navigates to the conditioned room', async () => {
    mockClimateAPI.createThermostat.mockResolvedValue(
      apiResponse({
        id: 7,
        label: 'Wood Shop North',
        location: 12,
        location_name: 'Wood Shop',
        controls_location: 12,
        controls_location_name: 'Wood Shop',
        controlled_asset: null,
        controlled_asset_name: null,
        manufacturer: '',
        model: '',
        notes: '',
        needs_review: false,
        created_at: '',
        updated_at: '',
      } as Thermostat),
    );

    renderAt('/facilities/climate/thermostats/new?location=12');

    // Locations should load and the seeded location should pre-fill.
    await waitFor(() => expect(mockInventoryAPI.listLocations).toHaveBeenCalled());

    const labelInput = await screen.findByPlaceholderText('Wood shop north');
    fireEvent.change(labelInput, { target: { value: 'Wood Shop North' } });

    const submitButton = screen.getByRole('button', { name: /create thermostat/i });
    fireEvent.click(submitButton);

    await waitFor(() => expect(mockClimateAPI.createThermostat).toHaveBeenCalled());
    const payload = mockClimateAPI.createThermostat.mock.calls[0][0];
    expect(payload.label).toBe('Wood Shop North');
    expect(payload.location).toBe(12);
    expect(payload.controls_location).toBe(12);

    await waitFor(() => expect(mockNavigate).toHaveBeenCalledWith('/inventory/locations/12'));
  });

  it('loads an existing thermostat on edit and submits a PATCH', async () => {
    mockClimateAPI.getThermostat.mockResolvedValue(
      apiResponse({
        id: 7,
        label: 'Old label',
        location: 12,
        location_name: 'Wood Shop',
        controls_location: 12,
        controls_location_name: 'Wood Shop',
        controlled_asset: 'asset-88',
        controlled_asset_name: 'RTU-3',
        manufacturer: 'Honeywell',
        model: 'T6 Pro',
        notes: 'Calibrated 2026-04-01',
        needs_review: false,
        created_at: '',
        updated_at: '',
      } as Thermostat),
    );
    mockClimateAPI.updateThermostat.mockResolvedValue(
      apiResponse({
        id: 7,
        label: 'Wood Shop North',
        location: 12,
        location_name: 'Wood Shop',
        controls_location: 12,
        controls_location_name: 'Wood Shop',
        controlled_asset: 'asset-88',
        controlled_asset_name: 'RTU-3',
        manufacturer: 'Honeywell',
        model: 'T6 Pro',
        notes: 'Calibrated 2026-04-01',
        needs_review: false,
        created_at: '',
        updated_at: '',
      } as Thermostat),
    );

    renderAt('/facilities/climate/thermostats/7/edit');

    const labelInput = await screen.findByDisplayValue('Old label');
    fireEvent.change(labelInput, { target: { value: 'Wood Shop North' } });

    fireEvent.click(screen.getByRole('button', { name: /save changes/i }));

    await waitFor(() => expect(mockClimateAPI.updateThermostat).toHaveBeenCalled());
    const [thermostatId, payload] = mockClimateAPI.updateThermostat.mock.calls[0];
    expect(thermostatId).toBe('7');
    expect(payload.label).toBe('Wood Shop North');
    expect(payload.controlled_asset).toBe('asset-88');
  });

  it('shows a kill-breaker preview when the power chain resolves', async () => {
    mockClimateAPI.getThermostat.mockResolvedValue(
      apiResponse({
        id: 7,
        label: 'North stat',
        location: 12,
        location_name: 'Wood Shop',
        controls_location: 12,
        controls_location_name: 'Wood Shop',
        controlled_asset: 'asset-88',
        controlled_asset_name: 'RTU-3',
        manufacturer: '',
        model: '',
        notes: '',
        needs_review: false,
        created_at: '',
        updated_at: '',
      } as Thermostat),
    );
    mockElectricalSafetyAPI.getAssetPowerChain.mockResolvedValue(
      apiResponse({
        asset: { id: 'asset-88', name: 'RTU-3', type: 'asset', label: 'RTU-3' },
        chain: [
          { kind: 'panel', id: 1, type: 'panel', label: 'Panel B' },
          { kind: 'breaker', id: 2, type: 'breaker', label: '22' },
        ],
      }) as any,
    );

    renderAt('/facilities/climate/thermostats/7/edit');

    const preview = await screen.findByTestId('kill-breaker-preview');
    await waitFor(() => expect(preview).toHaveTextContent('Panel B · 22'));
  });
});
