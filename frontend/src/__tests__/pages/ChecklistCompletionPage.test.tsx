/**
 * Tests for ChecklistCompletionPage component
 */
import { MantineProvider } from '@mantine/core';
import { ModalsProvider } from '@mantine/modals';
import { Notifications } from '@mantine/notifications';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { NotificationProvider } from '../../contexts/NotificationContext';
import ChecklistCompletionPage from '../../pages/ChecklistCompletionPage';
import * as api from '../../services/api';
import { networkError } from '../helpers/offline';

// Mock the API
vi.mock('../../services/api');

// Stub the QR scanner: html5-qrcode needs a real camera/WASM (covered by
// QRScanner.test.tsx). Here we only need to drive the page's onScanError /
// onScanSuccess handlers to assert how the page reacts (AC-14).
vi.mock('../../components/QRScanner', () => ({
  default: (props: {
    onScanSuccess: (text: string) => void;
    onScanError: (error: { kind: string; message: string }) => void;
    onClose: () => void;
  }) => (
    <div data-testid="qr-scanner-stub">
      <button
        type="button"
        onClick={() =>
          props.onScanError({
            kind: 'permission-denied',
            message: 'Enable camera access or enter the code manually.',
          })
        }
      >
        simulate camera denied
      </button>
      <button type="button" onClick={() => props.onScanSuccess('SCAN-CODE')}>
        simulate scan
      </button>
      <button type="button" onClick={props.onClose}>
        close scanner
      </button>
    </div>
  ),
}));

const mockNavigate = jest.fn();
vi.mock('react-router-dom', async () => ({
  ...(await vi.importActual('react-router-dom')),
  useNavigate: () => mockNavigate,
}));

describe('ChecklistCompletionPage', () => {
  const mockChecklist = {
    id: 'test-checklist-id',
    name: 'Monthly Safety Walk',
    description: 'Complete monthly safety inspection',
    sig: 1,
    sig_name: 'Logistics',
    is_active: true,
    is_public: true,
    created_by: null,
    created_by_username: null,
    step_count: 2,
    created_at: '2024-01-01T00:00:00Z',
    updated_at: '2024-01-01T00:00:00Z',
    steps: [
      {
        id: 'step-1',
        step_number: 1,
        name: 'Check Exit Signs',
        asset: 'asset-1',
        location: null,
        inventory_item: null,
        required: true,
        notes: '',
      },
      {
        id: 'step-2',
        step_number: 2,
        name: 'Check Fire Alarm',
        asset: 'asset-2',
        location: null,
        inventory_item: null,
        required: true,
        notes: '',
      },
    ],
  };

  const mockCompletion = {
    id: 'completion-id',
    checklist: 'test-checklist-id',
    checklist_name: 'Monthly Safety Walk',
    user: null,
    user_username: null,
    user_name: '',
    started_at: '2024-01-01T00:00:00Z',
    completed_at: null,
    status: 'in_progress' as const,
    step_completions: [],
    completed_steps_count: 0,
    total_steps_count: 2,
    required_steps_completed: 0,
    required_steps_total: 2,
  };

  beforeEach(() => {
    jest.clearAllMocks();
    localStorage.clear();
  });

  const renderWithRouter = async (
    checklistId = 'test-checklist-id',
    completionId = 'completion-id'
  ) => {
    const view = render(
      <MantineProvider>
        <ModalsProvider>
          <NotificationProvider>
            <Notifications />
            <MemoryRouter initialEntries={[`/checklist/${checklistId}/complete/${completionId}`]}>
              <Routes>
                <Route
                  path="/checklist/:checklistId/complete/:completionId"
                  element={<ChecklistCompletionPage />}
                />
              </Routes>
            </MemoryRouter>
          </NotificationProvider>
        </ModalsProvider>
      </MantineProvider>
    );
    return view;
  };

  test('displays loading state initially', async () => {
    (api.checklistsAPI.getChecklist as jest.Mock).mockReturnValue(new Promise(() => {}));
    (api.checklistsAPI.getCompletion as jest.Mock).mockReturnValue(new Promise(() => {}));

    await renderWithRouter();

    expect(screen.getByText(/loading checklist/i)).toBeInTheDocument();
  });

  test('displays checklist details after loading', async () => {
    (api.checklistsAPI.getChecklist as jest.Mock).mockResolvedValue({
      data: mockChecklist,
    });
    (api.checklistsAPI.getCompletion as jest.Mock).mockResolvedValue({
      data: mockCompletion,
    });

    await renderWithRouter();

    await screen.findByText('Monthly Safety Walk');
    expect(screen.getByText(/complete monthly safety inspection/i)).toBeInTheDocument();
    expect(screen.getByText(/check exit signs/i)).toBeInTheDocument();
    expect(screen.getByText(/check fire alarm/i)).toBeInTheDocument();
  });

  test('displays error when checklist not found', async () => {
    (api.checklistsAPI.getChecklist as jest.Mock).mockRejectedValue({
      response: { data: { detail: 'Checklist not found' } },
    });
    (api.checklistsAPI.getCompletion as jest.Mock).mockResolvedValue({
      data: mockCompletion,
    });

    await renderWithRouter();

    await screen.findByText(/checklist not found/i);
  });

  test('shows step completion status', async () => {
    const completionWithSteps = {
      ...mockCompletion,
      step_completions: [
        {
          id: 'step-completion-1',
          step: 'step-1',
          scanned_at: '2024-01-01T00:00:00Z',
          scanned_asset: 'asset-1',
        },
      ],
      completed_steps_count: 1,
    };

    (api.checklistsAPI.getChecklist as jest.Mock).mockResolvedValue({
      data: mockChecklist,
    });
    (api.checklistsAPI.getCompletion as jest.Mock).mockResolvedValue({
      data: completionWithSteps,
    });

    await renderWithRouter();

    await screen.findByText('Monthly Safety Walk');
    // Step 1 should show as completed
    expect(screen.getByText(/check exit signs/i)).toBeInTheDocument();
  });

  test('handles completing checklist when all required steps done', async () => {
    const completionWithAllSteps = {
      ...mockCompletion,
      step_completions: [
        {
          id: 'step-completion-1',
          step: 'step-1',
          scanned_at: '2024-01-01T00:00:00Z',
        },
        {
          id: 'step-completion-2',
          step: 'step-2',
          scanned_at: '2024-01-01T00:00:00Z',
        },
      ],
      completed_steps_count: 2,
      required_steps_completed: 2,
    };

    (api.checklistsAPI.getChecklist as jest.Mock).mockResolvedValue({
      data: mockChecklist,
    });
    (api.checklistsAPI.getCompletion as jest.Mock).mockResolvedValue({
      data: completionWithAllSteps,
    });
    (api.checklistsAPI.completeChecklist as jest.Mock).mockResolvedValue({
      data: { ...completionWithAllSteps, status: 'completed' },
    });

    await renderWithRouter();

    await screen.findByText('Monthly Safety Walk');

    const completeButton = screen.getByRole('button', { name: /complete checklist/i });
    fireEvent.click(completeButton);

    // Confirm the Mantine modal that replaced window.confirm
    const confirmInModal = await screen.findByRole('button', { name: /^confirm$/i });
    fireEvent.click(confirmInModal);

    await waitFor(() => {
      expect(api.checklistsAPI.completeChecklist).toHaveBeenCalledWith('completion-id');
    });
  });

  test('cancelling the complete checklist modal does not call the API', async () => {
    const completionWithAllSteps = {
      ...mockCompletion,
      step_completions: [
        {
          id: 'step-completion-1',
          step: 'step-1',
          scanned_at: '2024-01-01T00:00:00Z',
        },
        {
          id: 'step-completion-2',
          step: 'step-2',
          scanned_at: '2024-01-01T00:00:00Z',
        },
      ],
      completed_steps_count: 2,
      required_steps_completed: 2,
    };

    (api.checklistsAPI.getChecklist as jest.Mock).mockResolvedValue({
      data: mockChecklist,
    });
    (api.checklistsAPI.getCompletion as jest.Mock).mockResolvedValue({
      data: completionWithAllSteps,
    });
    (api.checklistsAPI.completeChecklist as jest.Mock).mockResolvedValue({
      data: { ...completionWithAllSteps, status: 'completed' },
    });

    await renderWithRouter();

    await screen.findByText('Monthly Safety Walk');

    const completeButton = screen.getByRole('button', { name: /complete checklist/i });
    fireEvent.click(completeButton);

    const cancelInModal = await screen.findByRole('button', { name: /^cancel$/i });
    fireEvent.click(cancelInModal);

    await waitFor(() => {
      expect(screen.queryByRole('button', { name: /^cancel$/i })).not.toBeInTheDocument();
    });

    expect(api.checklistsAPI.completeChecklist).not.toHaveBeenCalled();
  });

  test('shows completed status when checklist is completed', async () => {
    const completedChecklist = {
      ...mockCompletion,
      status: 'completed',
      completed_at: '2024-01-01T00:00:00Z',
    };

    (api.checklistsAPI.getChecklist as jest.Mock).mockResolvedValue({
      data: mockChecklist,
    });
    (api.checklistsAPI.getCompletion as jest.Mock).mockResolvedValue({
      data: completedChecklist,
    });

    await renderWithRouter();

    // Wait for the page to load and find the completed status
    // The page shows "✓ Checklist Completed" as an h3 heading
    // Note: There may also be a notification toast, so we use getAllByRole and check for the h3
    await waitFor(() => {
      const headings = screen.getAllByRole('heading', { name: /checklist completed/i });
      // Should have at least the h3 heading (may also have notification toast)
      expect(headings.length).toBeGreaterThan(0);
    });
    
    // Verify the main h3 heading is present (the page heading, not the notification)
    const headings = screen.getAllByRole('heading', { name: /checklist completed/i });
    const pageHeading = headings.find(h => h.tagName === 'H3');
    expect(pageHeading).toBeInTheDocument();
    expect(pageHeading).toHaveTextContent(/✓ checklist completed/i);
  });

  // --- R2 resilience (oms-sr1l4): long-form checklist journey -------------
  // This is the one public scan page with a live camera (QRScanner), so it
  // anchors AC-14 camera-denied handling; plus AC-16 offline survival and the
  // AC-15 duplicate-submit guard on completion.

  test('AC-14: a camera-permission denial surfaces an actionable, code-entry hint', async () => {
    (api.checklistsAPI.getChecklist as jest.Mock).mockResolvedValue({ data: mockChecklist });
    (api.checklistsAPI.getCompletion as jest.Mock).mockResolvedValue({ data: mockCompletion });

    await renderWithRouter();
    await screen.findByText('Monthly Safety Walk');

    fireEvent.click(screen.getByRole('button', { name: /scan qr code/i }));
    fireEvent.click(await screen.findByRole('button', { name: /simulate camera denied/i }));

    expect(await screen.findByText(/camera access denied/i)).toBeInTheDocument();
    expect(screen.getByText(/enter the code manually/i)).toBeInTheDocument();
  });

  test('AC-16: an offline checklist load shows an actionable error with a way home', async () => {
    (api.checklistsAPI.getChecklist as jest.Mock).mockRejectedValue(networkError());
    (api.checklistsAPI.getCompletion as jest.Mock).mockResolvedValue({ data: mockCompletion });

    await renderWithRouter();

    expect(await screen.findByText(/failed to load checklist/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /go home/i })).toBeInTheDocument();
  });

  test('AC-15: a completed checklist disables Complete so it cannot be submitted twice', async () => {
    const completed = {
      ...mockCompletion,
      status: 'completed',
      completed_at: '2024-01-02T00:00:00Z',
      completed_steps_count: 2,
      required_steps_completed: 2,
      required_steps_total: 2,
      step_completions: [
        { id: 'sc-1', step: 'step-1', scanned_at: '2024-01-01T00:00:00Z' },
        { id: 'sc-2', step: 'step-2', scanned_at: '2024-01-01T00:00:00Z' },
      ],
    };
    (api.checklistsAPI.getChecklist as jest.Mock).mockResolvedValue({ data: mockChecklist });
    (api.checklistsAPI.getCompletion as jest.Mock).mockResolvedValue({ data: completed });

    await renderWithRouter();
    await screen.findByText('Monthly Safety Walk');

    const completeButton = await screen.findByRole('button', { name: /^completed$/i });
    expect(completeButton).toBeDisabled();
    expect(api.checklistsAPI.completeChecklist).not.toHaveBeenCalled();
  });
});

