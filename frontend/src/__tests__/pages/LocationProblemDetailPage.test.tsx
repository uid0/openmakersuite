/**
 * Tests for LocationProblemDetailPage reactive contract (gh-453).
 *
 * The resolve and promote flows must patch the visible problem from the
 * mutation response — no follow-up GET, no flip back to "Loading problem…",
 * action buttons disabled while in flight, failure leaves panel intact.
 */
import { MantineProvider } from '@mantine/core';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

import LocationProblemDetailPage from '../../pages/LocationProblemDetailPage';
import api, { locationProblemsAPI, maintenanceAPI } from '../../services/api';
import { LocationProblem } from '../../types';

vi.mock('../../services/api', () => {
  const apiMock = { get: jest.fn() };
  return {
    __esModule: true,
    default: apiMock,
    locationProblemsAPI: {
      get: jest.fn(),
      resolve: jest.fn(),
      promoteStandard: jest.fn(),
      promoteThirdParty: jest.fn(),
    },
    maintenanceAPI: {
      listItems: jest.fn(),
    },
  };
});

const mockApi = api as jest.Mocked<typeof api>;
const mockLP = locationProblemsAPI as jest.Mocked<typeof locationProblemsAPI>;
const mockMI = maintenanceAPI as jest.Mocked<typeof maintenanceAPI>;

const renderPage = () =>
  render(
    <MantineProvider>
      <MemoryRouter initialEntries={['/maintenance/location-problems/p-1']}>
        <Routes>
          <Route
            path="/maintenance/location-problems/:id"
            element={<LocationProblemDetailPage />}
          />
        </Routes>
      </MemoryRouter>
    </MantineProvider>,
  );

const buildProblem = (overrides: Partial<LocationProblem> = {}): LocationProblem => ({
  id: 'p-1',
  location: 7,
  location_name: 'Bay 4',
  reported_by: 'alice',
  description: 'Door handle is broken',
  status: 'open',
  status_display: 'Open',
  severity: 'high',
  severity_display: 'High',
  photo: null,
  photo_url: null,
  paper_form_attachment: null,
  paper_form_url: null,
  work_order: null,
  work_order_short_id: null,
  third_party_work_order: null,
  third_party_work_order_short_id: null,
  resolution_notes: '',
  reported_at: '2026-05-01T00:00:00Z',
  updated_at: '2026-05-01T00:00:00Z',
  resolved_at: null,
  resolved_by: '',
  ...overrides,
});

beforeEach(() => {
  jest.clearAllMocks();
  localStorage.clear();
  localStorage.setItem('is_staff', 'true');
  mockMI.listItems.mockResolvedValue({ data: { results: [] } } as any);
  mockApi.get.mockResolvedValue({ data: { results: [] } } as any);
});

describe('LocationProblemDetailPage reactive contract (gh-453)', () => {
  test('resolve patches the problem from the API response — no follow-up GET, no full reload', async () => {
    const open = buildProblem({ status: 'open', status_display: 'Open' });
    const resolved = buildProblem({
      status: 'resolved',
      status_display: 'Resolved',
      resolved_at: '2026-05-10T00:00:00Z',
      resolved_by: 'alice',
      resolution_notes: 'Replaced handle',
    });
    mockLP.get.mockResolvedValue({ data: open } as any);
    mockLP.resolve.mockResolvedValue({ data: resolved } as any);

    renderPage();

    await screen.findByText('Door handle is broken');

    const notes = screen.getByLabelText(/resolution notes/i) as HTMLTextAreaElement;
    fireEvent.change(notes, { target: { value: 'Replaced handle' } });

    fireEvent.click(screen.getByRole('button', { name: /mark resolved/i }));

    await waitFor(() => {
      expect(mockLP.resolve).toHaveBeenCalledWith('p-1', {
        status: 'resolved',
        resolution_notes: 'Replaced handle',
      });
    });

    // The status badge updates from the response without a follow-up GET.
    await waitFor(() => {
      expect(screen.getByText('Resolved')).toBeInTheDocument();
    });
    expect(mockLP.get).toHaveBeenCalledTimes(1);
    expect(screen.queryByText(/loading problem/i)).not.toBeInTheDocument();
  });

  test('disables resolve buttons while pending and re-enables on failure', async () => {
    mockLP.get.mockResolvedValue({ data: buildProblem() } as any);

    let rejectResolve: (err: any) => void = () => undefined;
    mockLP.resolve.mockImplementation(
      () =>
        new Promise((_resolve, reject) => {
          rejectResolve = reject;
        }) as any,
    );

    renderPage();

    await screen.findByText('Door handle is broken');

    const markResolvedBtn = screen.getByRole('button', { name: /mark resolved/i });
    const markClosedBtn = screen.getByRole('button', { name: /mark closed/i });

    fireEvent.click(markResolvedBtn);

    await waitFor(() => {
      expect(markResolvedBtn).toBeDisabled();
    });
    // The sibling action is also disabled to prevent racing transitions.
    expect(markClosedBtn).toBeDisabled();
    // Page is not reset back to its initial loading placeholder.
    expect(screen.queryByText(/loading problem/i)).not.toBeInTheDocument();

    // Duplicate clicks while pending are ignored.
    fireEvent.click(markResolvedBtn);
    expect(mockLP.resolve).toHaveBeenCalledTimes(1);

    // Reject — both actions re-enable so the user can retry; the
    // resolution notes the user typed remain in the textarea.
    rejectResolve(new Error('boom'));

    await waitFor(() => {
      expect(
        screen.getByRole('button', { name: /mark resolved/i }),
      ).not.toBeDisabled();
    });
    expect(
      screen.getByRole('button', { name: /mark closed/i }),
    ).not.toBeDisabled();
    // Problem still rendered, no loader.
    expect(screen.getByText('Door handle is broken')).toBeInTheDocument();
  });
});
