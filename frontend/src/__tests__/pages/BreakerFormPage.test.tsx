/**
 * Resilience tests for BreakerFormPage (#457 R6, oms-f7cky).
 *
 * The page previously had no test. These cover the journey-resilience states
 * required by AC-18 (protected actions / forbidden) and AC-19 (loading,
 * save-failure, and duplicate-submit states render a consistent, accessible
 * surface rather than a blank screen or raw exception).
 *
 * Edit mode is used to exercise the save path: loading an existing breaker
 * pre-fills the location + required fields, so the submit reaches the API
 * without driving the Mantine `Select` (interaction covered in the e2e suite).
 */
import { MantineProvider } from '@mantine/core';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

import BreakerFormPage from '../../pages/BreakerFormPage';
import * as api from '../../services/api';
import { networkError } from '../helpers/offline';

vi.mock('../../services/api');

const breaker = {
  id: 5,
  location: 1,
  panel: 'A',
  breaker_number: '12',
  amperage: 20,
  voltage: 120,
  poles: 1,
  description: 'Wood shop dust collector',
  notes: '',
  is_active: true,
};

const renderEdit = () =>
  render(
    <MantineProvider>
      <MemoryRouter initialEntries={['/facilities/electrical/breakers/5/edit']}>
        <Routes>
          <Route
            path="/facilities/electrical/breakers/:id/edit"
            element={<BreakerFormPage />}
          />
        </Routes>
      </MemoryRouter>
    </MantineProvider>,
  );

describe('BreakerFormPage resilience states', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    (api.inventoryAPI.listLocations as jest.Mock).mockResolvedValue({
      data: { results: [{ id: 1, name: 'Electrical Room' }] },
    });
  });

  it('shows a loading state while the breaker loads (AC-19)', async () => {
    // Never-resolving fetch keeps the page in its loading branch.
    (api.electricalCircuitsAPI.getBreaker as jest.Mock).mockReturnValue(
      new Promise(() => {}),
    );

    renderEdit();

    expect(await screen.findByText(/loading breaker/i)).toBeInTheDocument();
  });

  it('renders a forbidden message when the API denies the save (AC-18)', async () => {
    (api.electricalCircuitsAPI.getBreaker as jest.Mock).mockResolvedValue({
      data: breaker,
    });
    (api.electricalCircuitsAPI.updateBreaker as jest.Mock).mockRejectedValue({
      response: {
        status: 403,
        data: { detail: 'You do not have permission to perform this action.' },
      },
    });

    renderEdit();

    await screen.findByDisplayValue('A');
    fireEvent.click(screen.getByRole('button', { name: /save changes/i }));

    expect(
      await screen.findByText('You do not have permission to perform this action.'),
    ).toBeInTheDocument();
  });

  it('surfaces a save-failure message when the request errors (AC-19)', async () => {
    (api.electricalCircuitsAPI.getBreaker as jest.Mock).mockResolvedValue({
      data: breaker,
    });
    // Offline / no-response error falls through to the generic save fallback.
    (api.electricalCircuitsAPI.updateBreaker as jest.Mock).mockRejectedValue(
      networkError(),
    );

    renderEdit();

    await screen.findByDisplayValue('A');
    fireEvent.click(screen.getByRole('button', { name: /save changes/i }));

    expect(await screen.findByText('Failed to save breaker')).toBeInTheDocument();
  });

  it('disables the submit button in flight so the save cannot double-fire (AC-19)', async () => {
    let resolveSave: () => void = () => {};
    (api.electricalCircuitsAPI.getBreaker as jest.Mock).mockResolvedValue({
      data: breaker,
    });
    (api.electricalCircuitsAPI.updateBreaker as jest.Mock).mockReturnValue(
      new Promise<{ data: unknown }>((resolve) => {
        resolveSave = () => resolve({ data: {} });
      }),
    );

    renderEdit();

    await screen.findByDisplayValue('A');
    const submit = screen.getByRole('button', { name: /save changes/i });

    fireEvent.click(submit);

    await waitFor(() =>
      expect(api.electricalCircuitsAPI.updateBreaker).toHaveBeenCalledTimes(1),
    );
    expect(submit).toBeDisabled();

    // A second click while the first request is in flight is a no-op.
    fireEvent.click(submit);
    expect(api.electricalCircuitsAPI.updateBreaker).toHaveBeenCalledTimes(1);

    // Let the in-flight request settle so pending state updates flush.
    await act(async () => {
      resolveSave();
    });
  });
});
