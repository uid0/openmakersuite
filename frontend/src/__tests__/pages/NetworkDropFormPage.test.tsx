/**
 * Resilience tests for NetworkDropFormPage (#457 R6, oms-f7cky).
 *
 * Previously untested. Covers AC-18 (forbidden save) and AC-19 (loading,
 * save-failure, and duplicate-submit states). Edit mode pre-fills the
 * location + identifier so the submit reaches the API without driving the
 * Mantine `Select` (covered in the e2e suite).
 */
import { MantineProvider } from '@mantine/core';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

import NetworkDropFormPage from '../../pages/NetworkDropFormPage';
import * as api from '../../services/api';
import { networkError } from '../helpers/offline';

vi.mock('../../services/api');

const networkDrop = {
  id: 3,
  location: 1,
  identifier: 'drop-1',
  drop_type: 'data',
  patch_panel: 'PP-1',
  patch_port: '7',
  mac_address: '',
  ip_address: '',
  description: 'Office uplink',
  notes: '',
  photo: null,
  is_active: true,
};

const renderEdit = () =>
  render(
    <MantineProvider>
      <MemoryRouter initialEntries={['/facilities/electrical/drops/3/edit']}>
        <Routes>
          <Route
            path="/facilities/electrical/drops/:id/edit"
            element={<NetworkDropFormPage />}
          />
        </Routes>
      </MemoryRouter>
    </MantineProvider>,
  );

describe('NetworkDropFormPage resilience states', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    (api.inventoryAPI.listLocations as jest.Mock).mockResolvedValue({
      data: { results: [{ id: 1, name: 'Electrical Room' }] },
    });
  });

  it('shows a loading state while the network drop loads (AC-19)', async () => {
    (api.electricalCircuitsAPI.getNetworkDrop as jest.Mock).mockReturnValue(
      new Promise(() => {}),
    );

    renderEdit();

    expect(await screen.findByText(/loading network drop/i)).toBeInTheDocument();
  });

  it('renders a forbidden message when the API denies the save (AC-18)', async () => {
    (api.electricalCircuitsAPI.getNetworkDrop as jest.Mock).mockResolvedValue({
      data: networkDrop,
    });
    (api.electricalCircuitsAPI.updateNetworkDrop as jest.Mock).mockRejectedValue({
      response: {
        status: 403,
        data: { detail: 'You do not have permission to perform this action.' },
      },
    });

    renderEdit();

    await screen.findByDisplayValue('drop-1');
    fireEvent.click(screen.getByRole('button', { name: /save changes/i }));

    expect(
      await screen.findByText('You do not have permission to perform this action.'),
    ).toBeInTheDocument();
  });

  it('surfaces a save-failure message when the request errors (AC-19)', async () => {
    (api.electricalCircuitsAPI.getNetworkDrop as jest.Mock).mockResolvedValue({
      data: networkDrop,
    });
    (api.electricalCircuitsAPI.updateNetworkDrop as jest.Mock).mockRejectedValue(
      networkError(),
    );

    renderEdit();

    await screen.findByDisplayValue('drop-1');
    fireEvent.click(screen.getByRole('button', { name: /save changes/i }));

    expect(
      await screen.findByText('Failed to save network drop'),
    ).toBeInTheDocument();
  });

  it('disables the submit button in flight so the save cannot double-fire (AC-19)', async () => {
    let resolveSave: () => void = () => {};
    (api.electricalCircuitsAPI.getNetworkDrop as jest.Mock).mockResolvedValue({
      data: networkDrop,
    });
    (api.electricalCircuitsAPI.updateNetworkDrop as jest.Mock).mockReturnValue(
      new Promise<{ data: unknown }>((resolve) => {
        resolveSave = () => resolve({ data: {} });
      }),
    );

    renderEdit();

    await screen.findByDisplayValue('drop-1');
    const submit = screen.getByRole('button', { name: /save changes/i });

    fireEvent.click(submit);

    await waitFor(() =>
      expect(api.electricalCircuitsAPI.updateNetworkDrop).toHaveBeenCalledTimes(1),
    );
    expect(submit).toBeDisabled();

    fireEvent.click(submit);
    expect(api.electricalCircuitsAPI.updateNetworkDrop).toHaveBeenCalledTimes(1);

    await act(async () => {
      resolveSave();
    });
  });
});
