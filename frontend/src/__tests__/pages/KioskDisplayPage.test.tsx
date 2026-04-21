/**
 * Tests for KioskDisplayPage
 */
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import KioskDisplayPage from '../../pages/KioskDisplayPage';
import * as api from '../../services/api';

jest.mock('../../services/api');

const makePayload = (overrides: Partial<import('../../types').KioskPayload> = {}) => ({
  screen: {
    id: '1',
    slug: 'lobby',
    name: 'Lobby',
    description: '',
    rotation_interval_seconds: 15,
    refresh_interval_seconds: 60,
  },
  system_messages: [],
  content_blocks: [],
  generated_at: '2026-04-21T00:00:00Z',
  ...overrides,
});

const renderKiosk = (slug: string, token: string) =>
  render(
    <MemoryRouter initialEntries={[`/kiosk/${slug}?token=${token}`]}>
      <Routes>
        <Route path="/kiosk/:slug" element={<KioskDisplayPage />} />
      </Routes>
    </MemoryRouter>,
  );

describe('KioskDisplayPage', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    (api.kioskAPI.heartbeat as jest.Mock).mockResolvedValue({ data: {} });
  });

  it('shows an error when no token is supplied', async () => {
    (api.kioskAPI.fetchPayload as jest.Mock).mockResolvedValue({ data: makePayload() });
    render(
      <MemoryRouter initialEntries={['/kiosk/lobby']}>
        <Routes>
          <Route path="/kiosk/:slug" element={<KioskDisplayPage />} />
        </Routes>
      </MemoryRouter>,
    );
    await waitFor(() => {
      expect(screen.getByTestId('kiosk-error')).toBeInTheDocument();
    });
  });

  it('renders a content block when provided', async () => {
    (api.kioskAPI.fetchPayload as jest.Mock).mockResolvedValue({
      data: makePayload({
        content_blocks: [
          {
            id: 'b1',
            block_type: 'custom_text',
            title: 'Welcome',
            body: 'Be safe',
            config: {},
            order: 0,
          },
        ],
      }),
    });
    renderKiosk('lobby', 'secret');
    await waitFor(() => {
      expect(screen.getByText('Welcome')).toBeInTheDocument();
    });
    expect(screen.getByText('Be safe')).toBeInTheDocument();
  });

  it('renders system messages in the banner area', async () => {
    (api.kioskAPI.fetchPayload as jest.Mock).mockResolvedValue({
      data: makePayload({
        system_messages: [
          { id: 'm1', title: 'All Hands', body: 'Meeting at 3', level: 'warning' },
        ],
      }),
    });
    renderKiosk('lobby', 'secret');
    await waitFor(() => {
      expect(screen.getByText('All Hands')).toBeInTheDocument();
    });
    expect(screen.getByText('Meeting at 3')).toBeInTheDocument();
  });

  it('shows error state when backend rejects', async () => {
    (api.kioskAPI.fetchPayload as jest.Mock).mockRejectedValue(new Error('403'));
    renderKiosk('lobby', 'secret');
    await waitFor(() => {
      expect(screen.getByTestId('kiosk-error')).toBeInTheDocument();
    });
  });
});
