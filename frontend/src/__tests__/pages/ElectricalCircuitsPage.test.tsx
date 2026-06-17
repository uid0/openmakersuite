/**
 * Tests for ElectricalCircuitsPage (oms-a5f).
 */
import { MantineProvider } from '@mantine/core';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import ElectricalCircuitsPage from '../../pages/ElectricalCircuitsPage';
import * as api from '../../services/api';
import { confirmDelete, showError } from '../../utils/dialogs';
import { expectNoA11yViolations } from '../helpers/axe';

vi.mock('../../services/api');

vi.mock('../../utils/dialogs', async () => ({
  confirmDelete: jest.fn(),
  showError: jest.fn(),
}));

const mockNavigate = jest.fn();
vi.mock('react-router-dom', async () => ({
  ...(await vi.importActual('react-router-dom')),
  useNavigate: () => mockNavigate,
}));

const renderPage = () =>
  render(
    <MantineProvider>
      <MemoryRouter>
        <ElectricalCircuitsPage />
      </MemoryRouter>
    </MantineProvider>,
  );

describe('ElectricalCircuitsPage', () => {
  const breaker = {
    id: 1,
    location: 1,
    location_name: 'Electrical Room',
    panel: 'A',
    breaker_number: '12',
    amperage: 20,
    voltage: 120,
    poles: 1,
    description: 'Wood shop',
    notes: '',
    is_active: true,
    created_at: '2026-04-01T00:00:00Z',
    updated_at: '2026-04-01T00:00:00Z',
  };

  beforeEach(() => {
    jest.clearAllMocks();
    (api.inventoryAPI.listLocations as jest.Mock).mockResolvedValue({
      data: { results: [{ id: 1, name: 'Electrical Room' }] },
    });
    (api.electricalCircuitsAPI.listBreakers as jest.Mock).mockResolvedValue({
      data: { results: [breaker] },
    });
    (api.electricalCircuitsAPI.listOutlets as jest.Mock).mockResolvedValue({
      data: { results: [] },
    });
    (api.electricalCircuitsAPI.listLightSwitches as jest.Mock).mockResolvedValue({
      data: { results: [] },
    });
    (api.electricalCircuitsAPI.listNetworkDrops as jest.Mock).mockResolvedValue({
      data: { results: [] },
    });
  });

  it('lists breakers in the breakers tab on mount', async () => {
    renderPage();
    expect(await screen.findByText('A / 12')).toBeInTheDocument();
    // 'Electrical Room' may appear in both the location filter dropdown and the row
    expect(screen.getAllByText('Electrical Room').length).toBeGreaterThan(0);
    expect(screen.getByText('20A')).toBeInTheDocument();
    expect(api.electricalCircuitsAPI.listBreakers).toHaveBeenCalled();
  });

  it('opens a confirm modal and deletes a breaker on confirm', async () => {
    (api.electricalCircuitsAPI.deleteBreaker as jest.Mock).mockResolvedValue({});
    renderPage();

    const deleteBtn = await screen.findByTitle('Delete');
    deleteBtn.click();

    expect(confirmDelete).toHaveBeenCalledTimes(1);
    const [message, onConfirm] = (confirmDelete as jest.Mock).mock.calls[0];
    expect(message).toContain('A/12');

    await onConfirm();

    expect(api.electricalCircuitsAPI.deleteBreaker).toHaveBeenCalledWith(1);
    expect(showError).not.toHaveBeenCalled();
  });

  it('navigates to the trace view when the route action is clicked', async () => {
    renderPage();

    const traceBtn = await screen.findByTitle('Trace fed loads');
    traceBtn.click();

    expect(mockNavigate).toHaveBeenCalledWith('/facilities/electrical/breakers/1/trace');
  });

  it('surfaces a Mantine error notification when delete fails', async () => {
    (api.electricalCircuitsAPI.deleteBreaker as jest.Mock).mockRejectedValue({
      response: { data: { detail: 'Outlets still depend on it' } },
    });
    renderPage();

    const deleteBtn = await screen.findByTitle('Delete');
    deleteBtn.click();

    const [, onConfirm] = (confirmDelete as jest.Mock).mock.calls[0];
    await onConfirm();

    await waitFor(() => {
      expect(showError).toHaveBeenCalledWith('Outlets still depend on it');
    });
  });

  // AC-19: while the active tab is fetching, the table renders a consistent
  // loading row rather than a premature "No breakers found" empty state.
  it('shows a loading state in the breakers table before data resolves (AC-19)', async () => {
    (api.electricalCircuitsAPI.listBreakers as jest.Mock).mockReturnValue(
      new Promise(() => {}),
    );

    renderPage();

    expect(await screen.findByText('Loading…')).toBeInTheDocument();
    expect(screen.queryByText('No breakers found')).not.toBeInTheDocument();
  });

  // AC-20: the loaded circuits table (tabs, filter select, action controls)
  // must be free of WCAG A/AA violations.
  it('has no accessibility violations once loaded (AC-20)', async () => {
    const { container } = renderPage();

    expect(await screen.findByText('A / 12')).toBeInTheDocument();
    await expectNoA11yViolations(container);
  });
});
