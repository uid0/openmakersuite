/**
 * Resilience tests for MaintenanceItemFormPage (#457 R4 — AC-19).
 *
 * The PM-task form had no test. AC-19 requires a critical form journey to
 * show a loading placeholder, surface a failed load instead of a blank
 * screen, and keep the operator's input (with the control re-enabled) when a
 * save fails offline so they can retry.
 */
import { MantineProvider } from '@mantine/core';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import MaintenanceItemFormPage from '../../pages/MaintenanceItemFormPage';
import { maintenanceAPI } from '../../services/api';
import { networkError } from '../helpers/offline';

vi.mock('../../services/api');

const mockUseParams = vi.fn<() => { assetId?: string; id?: string }>(() => ({
  assetId: 'a-1',
}));
const mockNavigate = vi.fn();
vi.mock('react-router-dom', async () => ({
  ...(await vi.importActual('react-router-dom')),
  useParams: () => mockUseParams(),
  useNavigate: () => mockNavigate,
}));

const mockMaintenanceAPI = maintenanceAPI as jest.Mocked<typeof maintenanceAPI>;

const renderPage = () =>
  render(
    <MantineProvider>
      <MemoryRouter>
        <MaintenanceItemFormPage />
      </MemoryRouter>
    </MantineProvider>,
  );

const okResponse = <T,>(data: T) =>
  ({ data, status: 200, statusText: 'OK', headers: {}, config: {} as never }) as never;

beforeEach(() => {
  jest.clearAllMocks();
  mockUseParams.mockReturnValue({ assetId: 'a-1' });
});

describe('MaintenanceItemFormPage resilience (#457 R4)', () => {
  it('shows a loading placeholder while an existing task loads (AC-19)', () => {
    mockUseParams.mockReturnValue({ assetId: 'a-1', id: 'mi-1' });
    mockMaintenanceAPI.getItem.mockImplementation(
      () => new Promise(() => undefined) as never,
    );

    renderPage();

    expect(screen.getByText(/loading task/i)).toBeInTheDocument();
  });

  it('surfaces a failed load instead of a blank screen (AC-19)', async () => {
    mockUseParams.mockReturnValue({ assetId: 'a-1', id: 'mi-1' });
    mockMaintenanceAPI.getItem.mockRejectedValue(networkError());

    renderPage();

    expect(
      await screen.findByText(/failed to load maintenance item/i),
    ).toBeInTheDocument();
  });

  it('keeps the form and re-enables Save when the save fails offline (AC-19)', async () => {
    mockMaintenanceAPI.createItem.mockRejectedValue(networkError());

    renderPage();

    const title = (await screen.findByLabelText(/title/i)) as HTMLInputElement;
    fireEvent.change(title, { target: { value: 'Replace air filter' } });

    fireEvent.click(screen.getByRole('button', { name: /create task/i }));

    await waitFor(() => {
      expect(mockMaintenanceAPI.createItem).toHaveBeenCalledTimes(1);
    });

    // The save failed offline: the page shows an actionable error, keeps the
    // title the operator typed, and re-enables the button to retry.
    expect(
      await screen.findByText(/failed to save maintenance item/i),
    ).toBeInTheDocument();
    expect((screen.getByLabelText(/title/i) as HTMLInputElement).value).toBe(
      'Replace air filter',
    );
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /create task/i })).not.toBeDisabled();
    });
  });
});
