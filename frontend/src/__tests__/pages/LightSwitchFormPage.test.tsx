/**
 * Resilience tests for LightSwitchFormPage (#457 R6, oms-f7cky).
 *
 * Previously untested. Covers AC-18 (forbidden save) and AC-19 (loading,
 * save-failure, and duplicate-submit states). Edit mode pre-fills the
 * location + identifier so the submit reaches the API without driving the
 * Mantine `Select` (covered in the e2e suite).
 */
import { MantineProvider } from '@mantine/core';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

import LightSwitchFormPage from '../../pages/LightSwitchFormPage';
import * as api from '../../services/api';
import { networkError } from '../helpers/offline';

vi.mock('../../services/api');

const lightSwitch = {
  id: 9,
  location: 1,
  identifier: 'door-east',
  controls_location: null,
  breaker: null,
  description: 'Bay overhead lights',
  notes: '',
  is_active: true,
};

const renderEdit = () =>
  render(
    <MantineProvider>
      <MemoryRouter initialEntries={['/facilities/electrical/switches/9/edit']}>
        <Routes>
          <Route
            path="/facilities/electrical/switches/:id/edit"
            element={<LightSwitchFormPage />}
          />
        </Routes>
      </MemoryRouter>
    </MantineProvider>,
  );

describe('LightSwitchFormPage resilience states', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    (api.inventoryAPI.listLocations as jest.Mock).mockResolvedValue({
      data: { results: [{ id: 1, name: 'Electrical Room' }] },
    });
    (api.electricalCircuitsAPI.listBreakers as jest.Mock).mockResolvedValue({
      data: { results: [] },
    });
  });

  it('shows a loading state while the light switch loads (AC-19)', async () => {
    (api.electricalCircuitsAPI.getLightSwitch as jest.Mock).mockReturnValue(
      new Promise(() => {}),
    );

    renderEdit();

    expect(await screen.findByText(/loading light switch/i)).toBeInTheDocument();
  });

  it('renders a forbidden message when the API denies the save (AC-18)', async () => {
    (api.electricalCircuitsAPI.getLightSwitch as jest.Mock).mockResolvedValue({
      data: lightSwitch,
    });
    (api.electricalCircuitsAPI.updateLightSwitch as jest.Mock).mockRejectedValue({
      response: {
        status: 403,
        data: { detail: 'You do not have permission to perform this action.' },
      },
    });

    renderEdit();

    await screen.findByDisplayValue('door-east');
    fireEvent.click(screen.getByRole('button', { name: /save changes/i }));

    expect(
      await screen.findByText('You do not have permission to perform this action.'),
    ).toBeInTheDocument();
  });

  it('surfaces a save-failure message when the request errors (AC-19)', async () => {
    (api.electricalCircuitsAPI.getLightSwitch as jest.Mock).mockResolvedValue({
      data: lightSwitch,
    });
    (api.electricalCircuitsAPI.updateLightSwitch as jest.Mock).mockRejectedValue(
      networkError(),
    );

    renderEdit();

    await screen.findByDisplayValue('door-east');
    fireEvent.click(screen.getByRole('button', { name: /save changes/i }));

    expect(
      await screen.findByText('Failed to save light switch'),
    ).toBeInTheDocument();
  });

  it('disables the submit button in flight so the save cannot double-fire (AC-19)', async () => {
    let resolveSave: () => void = () => {};
    (api.electricalCircuitsAPI.getLightSwitch as jest.Mock).mockResolvedValue({
      data: lightSwitch,
    });
    (api.electricalCircuitsAPI.updateLightSwitch as jest.Mock).mockReturnValue(
      new Promise<{ data: unknown }>((resolve) => {
        resolveSave = () => resolve({ data: {} });
      }),
    );

    renderEdit();

    await screen.findByDisplayValue('door-east');
    const submit = screen.getByRole('button', { name: /save changes/i });

    fireEvent.click(submit);

    await waitFor(() =>
      expect(api.electricalCircuitsAPI.updateLightSwitch).toHaveBeenCalledTimes(1),
    );
    expect(submit).toBeDisabled();

    fireEvent.click(submit);
    expect(api.electricalCircuitsAPI.updateLightSwitch).toHaveBeenCalledTimes(1);

    await act(async () => {
      resolveSave();
    });
  });
});
