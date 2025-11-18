/**
 * Tests for ChecklistCompletionPage component
 */
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import ChecklistCompletionPage from '../../pages/ChecklistCompletionPage';
import * as api from '../../services/api';

// Mock the API
jest.mock('../../services/api');

const mockNavigate = jest.fn();
jest.mock('react-router-dom', () => ({
  ...jest.requireActual('react-router-dom'),
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
      <MemoryRouter initialEntries={[`/checklist/${checklistId}/complete/${completionId}`]}>
        <Routes>
          <Route
            path="/checklist/:checklistId/complete/:completionId"
            element={<ChecklistCompletionPage />}
          />
        </Routes>
      </MemoryRouter>
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

    // Mock window.confirm to return true
    window.confirm = jest.fn(() => true);

    await renderWithRouter();

    await screen.findByText('Monthly Safety Walk');

    const completeButton = screen.getByRole('button', { name: /complete checklist/i });
    fireEvent.click(completeButton);

    await waitFor(() => {
      expect(api.checklistsAPI.completeChecklist).toHaveBeenCalledWith('completion-id');
    });
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

    await screen.findByText(/checklist completed/i);
    expect(screen.getByText(/✓ checklist completed/i)).toBeInTheDocument();
  });
});

