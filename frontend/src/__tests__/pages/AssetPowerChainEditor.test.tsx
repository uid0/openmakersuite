/**
 * Tests for AssetPowerChainEditor — Cordset / Hardwired tab split (PR 3/5).
 *
 * Covers the spec's six bullet points:
 *   1. Tab switcher renders both tabs; default is Cordset.
 *   2. Hardwired tab lists existing HardwiredConnection rows.
 *   3. Adding a hardwired connection with an existing disconnect succeeds.
 *   4. Adding a hardwired connection with an inline new disconnect POSTs
 *      both bodies in order and threads the disconnect id into the
 *      hardwired-connection body.
 *   5. Type-specific field visibility (fused / integral / none).
 *   6. Deleting a hardwired connection removes the row from the list.
 */
import { MantineProvider } from '@mantine/core';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import React from 'react';

import AssetPowerChainEditor from '../../pages/AssetPowerChainEditor';
import * as api from '../../services/api';

jest.mock('../../services/api', () => {
  const actual = jest.requireActual('../../services/api');
  // Preserve constants (DISCONNECT_TYPE_OPTIONS, NEMA tables, etc.) so
  // the dropdowns render real options; only the API entry points get
  // stubbed in beforeEach below.
  return {
    ...actual,
    electricalCircuitsAPI: { ...actual.electricalCircuitsAPI },
    electricalTopologyAPI: { ...actual.electricalTopologyAPI },
    inventoryAPI: { ...actual.inventoryAPI },
    lotoAPI: { ...actual.lotoAPI },
  };
});
jest.mock('../../utils/dialogs', () => ({
  showError: jest.fn(),
  showSuccess: jest.fn(),
}));

const ASSET_ID = '11111111-1111-1111-1111-111111111111';

const existingConnection: api.HardwiredConnection = {
  id: 7,
  asset: ASSET_ID,
  asset_name: 'RTU-1',
  disconnect: 42,
  disconnect_label: 'RTU-1 disconnect',
  circuit: 100,
  circuit_label: 'RTU feeder',
  conductor_size: '10 AWG',
  conductor_length_ft: 22,
  notes: '',
  needs_review: false,
  created_at: '2026-05-01T00:00:00Z',
  updated_at: '2026-05-01T00:00:00Z',
};

const circuit: api.PowerCircuitDetail = {
  id: 100,
  breaker: 9,
  breaker_label: 'Panel A / pos 14',
  panel_id: 1,
  panel_name: 'Panel A',
  label: 'Wood shop dust',
  conductor_size: '12 AWG',
  conductor_length_ft: null,
  max_load_amps: 16,
  notes: '',
  needs_review: false,
  outlet_count: 0,
  created_at: '2026-04-01T00:00:00Z',
  updated_at: '2026-04-01T00:00:00Z',
};

const existingDisconnect: api.Disconnect = {
  id: 42,
  circuit: 100,
  circuit_label: 'Wood shop dust',
  panel_name: 'Panel A',
  breaker_position: '14',
  location: 5,
  location_name: 'Shop',
  label: 'Wood shop dust disconnect',
  disconnect_type: 'unfused',
  amperage: 30,
  fuse_size: '',
  is_lockable: true,
  photo: null,
  notes: '',
  required_loto_devices: [],
  needs_review: false,
  created_at: '2026-04-01T00:00:00Z',
  updated_at: '2026-04-01T00:00:00Z',
};

const renderEditor = () =>
  render(
    <MantineProvider>
      <AssetPowerChainEditor assetId={ASSET_ID} isStaff onChange={jest.fn()} />
    </MantineProvider>,
  );

beforeEach(() => {
  jest.clearAllMocks();

  (api.electricalTopologyAPI as any).listPorts = jest.fn().mockResolvedValue({
    data: { results: [] },
  });
  (api.electricalTopologyAPI as any).listPowerCables = jest.fn().mockResolvedValue({
    data: { results: [] },
  });
  (api.electricalTopologyAPI as any).listOutlets = jest.fn().mockResolvedValue({
    data: { results: [] },
  });
  (api.electricalTopologyAPI as any).listCircuits = jest.fn().mockResolvedValue({
    data: { results: [circuit] },
  });

  (api.electricalCircuitsAPI as any).listHardwiredConnections = jest
    .fn()
    .mockResolvedValue({ data: { results: [existingConnection] } });
  (api.electricalCircuitsAPI as any).listDisconnects = jest
    .fn()
    .mockResolvedValue({ data: { results: [existingDisconnect] } });
  (api.electricalCircuitsAPI as any).createDisconnect = jest
    .fn()
    .mockResolvedValue({ data: { ...existingDisconnect, id: 99 } });
  (api.electricalCircuitsAPI as any).createHardwiredConnection = jest
    .fn()
    .mockResolvedValue({ data: { ...existingConnection, id: 8 } });
  (api.electricalCircuitsAPI as any).deleteHardwiredConnection = jest
    .fn()
    .mockResolvedValue({});

  (api.inventoryAPI as any).listLocations = jest.fn().mockResolvedValue({
    data: { results: [{ id: 5, name: 'Shop' }] },
  });
  (api.lotoAPI as any).listDevices = jest.fn().mockResolvedValue({
    data: { results: [{ id: 3, label: 'Padlock-3', device_type_display: 'Padlock' }] },
  });
});

describe('AssetPowerChainEditor — tabs', () => {
  it('renders both tabs and defaults to Cordset', async () => {
    renderEditor();
    expect(await screen.findByRole('tab', { name: /cordset/i })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /hardwired/i })).toBeInTheDocument();
    const cordsetTab = screen.getByRole('tab', { name: /cordset/i });
    expect(cordsetTab).toHaveAttribute('aria-selected', 'true');
  });
});

describe('AssetPowerChainEditor — hardwired list', () => {
  it('lists existing HardwiredConnection rows in the Hardwired tab', async () => {
    renderEditor();
    await userEvent.click(await screen.findByRole('tab', { name: /hardwired/i }));

    await waitFor(() =>
      expect(api.electricalCircuitsAPI.listHardwiredConnections).toHaveBeenCalledWith({
        asset: ASSET_ID,
      }),
    );

    const row = await screen.findByTestId(`hardwired-row-${existingConnection.id}`);
    expect(within(row).getByText(/RTU-1 disconnect/i)).toBeInTheDocument();
    expect(within(row).getByText(/10 AWG/i)).toBeInTheDocument();
    expect(within(row).getByText(/22 ft/i)).toBeInTheDocument();
  });

  it('removes a hardwired row when the delete button is clicked', async () => {
    renderEditor();
    await userEvent.click(await screen.findByRole('tab', { name: /hardwired/i }));

    const row = await screen.findByTestId(`hardwired-row-${existingConnection.id}`);
    const deleteBtn = within(row).getByRole('button', {
      name: /remove hardwired connection/i,
    });

    // After delete, the refresh() call should return an empty list.
    (api.electricalCircuitsAPI.listHardwiredConnections as jest.Mock).mockResolvedValueOnce({
      data: { results: [] },
    });

    await userEvent.click(deleteBtn);

    await waitFor(() =>
      expect(api.electricalCircuitsAPI.deleteHardwiredConnection).toHaveBeenCalledWith(
        existingConnection.id,
      ),
    );
    await waitFor(() =>
      expect(screen.queryByTestId(`hardwired-row-${existingConnection.id}`)).toBeNull(),
    );
  });
});

describe('AssetPowerChainEditor — adding a hardwired connection', () => {
  async function gotoHardwired() {
    renderEditor();
    await userEvent.click(await screen.findByRole('tab', { name: /hardwired/i }));
  }

  async function pickCircuit() {
    const circuitInput = await screen.findByPlaceholderText('select circuit');
    await userEvent.click(circuitInput);
    await userEvent.click(
      await screen.findByRole('option', { name: /Panel A · Panel A \/ pos 14 · Wood shop dust/ }),
    );
  }

  async function pickDisconnect(matcher: RegExp) {
    const disconnectInput = await screen.findByPlaceholderText('select disconnect');
    await userEvent.click(disconnectInput);
    await userEvent.click(await screen.findByRole('option', { name: matcher }));
  }

  it('posts a HardwiredConnection against an existing disconnect', async () => {
    await gotoHardwired();
    await pickCircuit();

    await waitFor(() =>
      expect(api.electricalCircuitsAPI.listDisconnects).toHaveBeenCalledWith({
        circuit: String(circuit.id),
      }),
    );

    await pickDisconnect(/Wood shop dust disconnect/);

    await userEvent.click(
      screen.getByRole('button', { name: /add hardwired connection/i }),
    );

    await waitFor(() =>
      expect(api.electricalCircuitsAPI.createHardwiredConnection).toHaveBeenCalled(),
    );
    const body = (api.electricalCircuitsAPI.createHardwiredConnection as jest.Mock).mock
      .calls[0][0];
    expect(body.asset).toBe(ASSET_ID);
    expect(body.disconnect).toBe(existingDisconnect.id);
    // Existing-disconnect path must not POST a new disconnect.
    expect(api.electricalCircuitsAPI.createDisconnect).not.toHaveBeenCalled();
  });

  it('inline-creates a new disconnect and posts a HardwiredConnection referencing it', async () => {
    await gotoHardwired();
    await pickCircuit();
    await pickDisconnect(/Create new disconnect/);

    // New disconnect form should now be visible.
    const newForm = await screen.findByTestId('new-disconnect-form');
    const labelInput = within(newForm).getByLabelText(/label/i);
    await userEvent.clear(labelInput);
    await userEvent.type(labelInput, 'RTU-2 disconnect');

    // Switch to 'integral' so the form doesn't require a location pick
    // (location is auto-null for that type per the spec).
    const typeSelect = within(newForm).getByLabelText(/^type$/i) as HTMLSelectElement;
    fireEvent.change(typeSelect, { target: { value: 'integral' } });

    await userEvent.click(
      screen.getByRole('button', { name: /add hardwired connection/i }),
    );

    await waitFor(() =>
      expect(api.electricalCircuitsAPI.createDisconnect).toHaveBeenCalled(),
    );
    const disconnectBody = (api.electricalCircuitsAPI.createDisconnect as jest.Mock).mock
      .calls[0][0];
    expect(disconnectBody.circuit).toBe(circuit.id);
    expect(disconnectBody.label).toBe('RTU-2 disconnect');
    expect(disconnectBody.disconnect_type).toBe('integral');
    expect(disconnectBody.location).toBeNull();
    expect(api.electricalCircuitsAPI.createDisconnect).toHaveBeenCalledTimes(1);

    await waitFor(() =>
      expect(api.electricalCircuitsAPI.createHardwiredConnection).toHaveBeenCalled(),
    );
    const hwBody = (api.electricalCircuitsAPI.createHardwiredConnection as jest.Mock).mock
      .calls[0][0];
    // Disconnect-create resolved with id=99 — that id must be threaded
    // into the hardwired-connection POST body.
    expect(hwBody.disconnect).toBe(99);
    expect(hwBody.asset).toBe(ASSET_ID);

    // Order matters: disconnect must be created before the connection.
    const disconnectCallOrder = (api.electricalCircuitsAPI.createDisconnect as jest.Mock)
      .mock.invocationCallOrder[0];
    const hwCallOrder = (
      api.electricalCircuitsAPI.createHardwiredConnection as jest.Mock
    ).mock.invocationCallOrder[0];
    expect(disconnectCallOrder).toBeLessThan(hwCallOrder);
  }, 15000);
});

describe('AssetPowerChainEditor — disconnect type field visibility', () => {
  async function openInlineForm() {
    renderEditor();
    await userEvent.click(await screen.findByRole('tab', { name: /hardwired/i }));

    const circuitInput = await screen.findByPlaceholderText('select circuit');
    await userEvent.click(circuitInput);
    await userEvent.click(
      await screen.findByRole('option', { name: /Panel A · Panel A \/ pos 14 · Wood shop dust/ }),
    );
    const disconnectInput = await screen.findByPlaceholderText('select disconnect');
    await userEvent.click(disconnectInput);
    await userEvent.click(
      await screen.findByRole('option', { name: /Create new disconnect/ }),
    );
    return screen.findByTestId('new-disconnect-form');
  }

  async function setType(form: HTMLElement, value: string) {
    const typeSelect = within(form).getByLabelText(/^type$/i) as HTMLSelectElement;
    await userEvent.selectOptions(typeSelect, value);
  }

  it('shows fuse_size when type is fused', async () => {
    const form = await openInlineForm();
    // The form opens with no type selected, so fuse_size is hidden.
    expect(within(form).queryByPlaceholderText(/30A class J/i)).toBeNull();
    await setType(form, 'fused');
    expect(within(form).getByPlaceholderText(/30A class J/i)).toBeInTheDocument();
  });

  it('hides location when type is integral or none', async () => {
    const form = await openInlineForm();
    // Pick a type that requires a separate location, then verify the
    // picker appears.
    await setType(form, 'unfused');
    expect(
      within(form).getByPlaceholderText('where is the switch mounted?'),
    ).toBeInTheDocument();
    await setType(form, 'integral');
    expect(
      within(form).queryByPlaceholderText('where is the switch mounted?'),
    ).toBeNull();
    await setType(form, 'none');
    expect(
      within(form).queryByPlaceholderText('where is the switch mounted?'),
    ).toBeNull();
  });
});
