/**
 * Tests for WebhookDetailPage.
 *
 * Covers the loading state, the test-send transient-error surface (a failed
 * "Test" delivery is reported in the result modal rather than swallowed), and
 * a forbidden (admin-only) load degrading gracefully to the not-found state.
 * Maps to AC-18 (a denied action stays consistent, no raw exception) and
 * AC-19 (loading / error / missing states are consistent). Part of #457 R7.
 */
import { MantineProvider } from '@mantine/core';
import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { networkError } from '../helpers/offline';
import WebhookDetailPage from '../../pages/WebhookDetailPage';
import { webhooksAPI } from '../../services/api';

vi.mock('../../services/api', () => ({
  webhooksAPI: {
    getWebhook: jest.fn(),
    testWebhook: jest.fn(),
    getTestStatus: jest.fn(),
    deleteWebhook: jest.fn(),
  },
}));

const mockNavigate = jest.fn();
vi.mock('react-router-dom', async () => ({
  ...(await vi.importActual('react-router-dom')),
  useNavigate: () => mockNavigate,
  useParams: () => ({ id: '5' }),
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
        <WebhookDetailPage />
      </MemoryRouter>
    </MantineProvider>,
  );

describe('WebhookDetailPage', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    (webhooksAPI.getWebhook as jest.Mock).mockResolvedValue({ data: mockWebhook });
    (webhooksAPI.testWebhook as jest.Mock).mockResolvedValue({
      data: { webhook_id: 5, success: true, tested_at: '2024-01-01T00:00:00Z' },
    });
  });

  // AC-19: a consistent loading state renders while the webhook loads.
  it('shows a loading state while the webhook loads', () => {
    (webhooksAPI.getWebhook as jest.Mock).mockImplementation(
      () => new Promise(() => {}), // never resolves: hold the loading state
    );

    renderPage();

    expect(screen.getByText(/loading webhook/i)).toBeInTheDocument();
  });

  // AC-19: a transient failure of the "Test" send is surfaced in the result
  // modal instead of being swallowed or crashing the page.
  it('surfaces a transient error when the test send fails', async () => {
    (webhooksAPI.testWebhook as jest.Mock).mockRejectedValue(networkError());

    renderPage();

    const testButton = await screen.findByRole('button', { name: /^test$/i });
    fireEvent.click(testButton);

    expect(await screen.findByText('Test Failed')).toBeInTheDocument();
    // The fallback message is rendered (network error carries no response body).
    expect(screen.getAllByText('Failed to test webhook').length).toBeGreaterThan(0);
  });

  // AC-18/AC-19: when the backend denies the load (e.g. a non-admin viewer),
  // the page degrades to a consistent not-found state rather than a blank
  // screen or a thrown exception.
  it('renders a consistent not-found state when the load is denied', async () => {
    (webhooksAPI.getWebhook as jest.Mock).mockRejectedValue({
      response: {
        status: 403,
        data: { detail: 'You do not have permission to perform this action.' },
      },
    });

    renderPage();

    expect(await screen.findByText('Webhook not found.')).toBeInTheDocument();
    expect(screen.queryByText(/loading webhook/i)).not.toBeInTheDocument();
  });
});
