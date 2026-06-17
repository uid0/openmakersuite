/**
 * Tests for WebhookFormPage.
 *
 * Covers the edit-mode loading state, a failed save, and forbidden
 * (admin-only) API responses on both the edit-mode load and the create-mode
 * save. Maps to AC-18 (a denied action is surfaced with a recovery path and
 * the user is not navigated away) and AC-19 (loading / error states stay
 * consistent). Part of #457 R7 (settings/webhooks coverage).
 */
import { MantineProvider } from '@mantine/core';
import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import WebhookFormPage from '../../pages/WebhookFormPage';
import { webhooksAPI } from '../../services/api';

vi.mock('../../services/api', () => ({
  webhooksAPI: {
    getWebhook: jest.fn(),
    createWebhook: jest.fn(),
    updateWebhook: jest.fn(),
  },
}));

const mockUseParams = vi.fn((): { id?: string } => ({}));
const mockNavigate = jest.fn();
vi.mock('react-router-dom', async () => ({
  ...(await vi.importActual('react-router-dom')),
  useParams: () => mockUseParams(),
  useNavigate: () => mockNavigate,
}));

const mockWebhook = {
  id: 5,
  name: 'Existing Hook',
  description: 'Sends reorder events',
  url: 'https://example.com/hook',
  event_type: 'reorder_request_created',
  event_type_display: 'Reorder Request Created',
  is_active: true,
  headers: null,
  last_triggered_at: null,
  success_count: 0,
  failure_count: 0,
  success_rate: null,
  total_triggers: 0,
  last_error: '',
  created_at: '2024-01-01T00:00:00Z',
  updated_at: '2024-01-01T00:00:00Z',
};

const renderPage = () =>
  render(
    <MantineProvider>
      <MemoryRouter>
        <WebhookFormPage />
      </MemoryRouter>
    </MantineProvider>,
  );

describe('WebhookFormPage', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockUseParams.mockReturnValue({});
    (webhooksAPI.getWebhook as jest.Mock).mockResolvedValue({ data: mockWebhook });
    (webhooksAPI.createWebhook as jest.Mock).mockResolvedValue({ data: mockWebhook });
    (webhooksAPI.updateWebhook as jest.Mock).mockResolvedValue({ data: mockWebhook });
  });

  // AC-19: in edit mode the page shows a loading state while the webhook
  // loads rather than flashing an empty form.
  it('shows a loading state while the webhook loads in edit mode', () => {
    mockUseParams.mockReturnValue({ id: '5' });
    (webhooksAPI.getWebhook as jest.Mock).mockImplementation(
      () => new Promise(() => {}), // never resolves: hold the loading state
    );

    renderPage();

    expect(screen.getByText(/loading webhook/i)).toBeInTheDocument();
  });

  // AC-19: a failed save surfaces an error alert and keeps the user on the
  // form instead of navigating away or throwing.
  it('surfaces an error and stays on the form when saving fails', async () => {
    mockUseParams.mockReturnValue({ id: '5' });
    (webhooksAPI.updateWebhook as jest.Mock).mockRejectedValue({
      response: { data: { detail: 'Upstream rejected the webhook' } },
    });

    renderPage();

    // The form is pre-filled from the loaded webhook, so it is already valid.
    const saveButton = await screen.findByRole('button', { name: /save changes/i });
    fireEvent.click(saveButton);

    expect(await screen.findByText('Upstream rejected the webhook')).toBeInTheDocument();
    expect(mockNavigate).not.toHaveBeenCalledWith('/settings/webhooks');
  });

  // AC-18/AC-19: a forbidden load (non-admin) surfaces a recovery message and
  // does not leave the page stuck on the loading state.
  it('surfaces a forbidden error when loading is denied', async () => {
    mockUseParams.mockReturnValue({ id: '5' });
    (webhooksAPI.getWebhook as jest.Mock).mockRejectedValue({
      response: {
        status: 403,
        data: { detail: 'You do not have permission to perform this action.' },
      },
    });

    renderPage();

    expect(
      await screen.findByText('You do not have permission to perform this action.'),
    ).toBeInTheDocument();
    expect(screen.queryByText(/loading webhook/i)).not.toBeInTheDocument();
  });

  // AC-18: a forbidden create (non-admin) is surfaced rather than silently
  // failing, and the user is not navigated away on the denied action.
  it('surfaces a forbidden error when creating is denied', async () => {
    mockUseParams.mockReturnValue({}); // create mode
    (webhooksAPI.createWebhook as jest.Mock).mockRejectedValue({
      response: { status: 403, data: { detail: 'Admin access required' } },
    });

    renderPage();

    fireEvent.change(await screen.findByLabelText(/^name/i), {
      target: { value: 'New Hook' },
    });
    fireEvent.change(screen.getByLabelText(/webhook url/i), {
      target: { value: 'https://example.com/new-hook' },
    });
    fireEvent.click(screen.getByRole('button', { name: /create webhook/i }));

    expect(await screen.findByText('Admin access required')).toBeInTheDocument();
    expect(mockNavigate).not.toHaveBeenCalledWith('/settings/webhooks');
  });
});
