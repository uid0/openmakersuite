/**
 * Tests for WebhookListPage — focuses on the Mantine modal delete flow
 * introduced as part of Phase 2d popup conversion (oms-1dv).
 */
import { MantineProvider } from '@mantine/core';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import WebhookListPage from '../../pages/WebhookListPage';
import * as api from '../../services/api';
import { confirmDelete, showError } from '../../utils/dialogs';

jest.mock('../../services/api');

jest.mock('../../utils/dialogs', () => ({
  confirmDelete: jest.fn(),
  showError: jest.fn(),
}));

const mockNavigate = jest.fn();
jest.mock('react-router-dom', () => ({
  ...jest.requireActual('react-router-dom'),
  useNavigate: () => mockNavigate,
}));

describe('WebhookListPage delete flow', () => {
  const baseWebhook = {
    id: 42,
    name: 'Reorder Webhook',
    url: 'https://example.com/hook',
    description: 'Test hook',
    event_type: 'reorder_request_created',
    event_type_display: 'Reorder Request Created',
    is_active: true,
    success_rate: 100,
    total_triggers: 3,
    last_triggered_at: null,
  };

  beforeEach(() => {
    jest.clearAllMocks();
    (api.webhooksAPI.listWebhooks as jest.Mock).mockResolvedValue({
      data: { results: [baseWebhook] },
    });
  });

  const renderPage = () =>
    render(
      <MantineProvider>
        <MemoryRouter>
          <WebhookListPage />
        </MemoryRouter>
      </MantineProvider>,
    );

  it('opens a confirm modal when delete is clicked and deletes on confirm', async () => {
    (api.webhooksAPI.deleteWebhook as jest.Mock).mockResolvedValue({});

    renderPage();
    await screen.findByText('Reorder Webhook');

    const deleteButton = await screen.findByTitle('Delete');
    deleteButton.click();

    expect(confirmDelete).toHaveBeenCalledTimes(1);
    const [message, onConfirm] = (confirmDelete as jest.Mock).mock.calls[0];
    expect(message).toContain('Reorder Webhook');

    await onConfirm();

    expect(api.webhooksAPI.deleteWebhook).toHaveBeenCalledWith(42);
    expect(showError).not.toHaveBeenCalled();
  });

  it('surfaces a Mantine error notification when delete fails', async () => {
    (api.webhooksAPI.deleteWebhook as jest.Mock).mockRejectedValue({
      response: { data: { detail: 'Not allowed' } },
    });

    renderPage();
    await screen.findByText('Reorder Webhook');

    const deleteButton = await screen.findByTitle('Delete');
    deleteButton.click();

    const [, onConfirm] = (confirmDelete as jest.Mock).mock.calls[0];
    await onConfirm();

    await waitFor(() => {
      expect(showError).toHaveBeenCalledWith('Not allowed');
    });
  });
});
