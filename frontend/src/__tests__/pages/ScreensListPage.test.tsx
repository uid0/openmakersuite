/**
 * Tests for ScreensListPage
 */
import { MantineProvider } from '@mantine/core';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import ScreensListPage from '../../pages/ScreensListPage';
import * as api from '../../services/api';

vi.mock('../../services/api');

const mockNavigate = jest.fn();
vi.mock('react-router-dom', async () => ({
  ...(await vi.importActual('react-router-dom')),
  useNavigate: () => mockNavigate,
}));

const renderPage = () =>
  render(
    <MantineProvider>
      <MemoryRouter>
        <ScreensListPage />
      </MemoryRouter>
    </MantineProvider>,
  );

describe('ScreensListPage', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('shows empty state when no screens exist', async () => {
    (api.screensAPI.getStatus as jest.Mock).mockResolvedValue({
      data: { count: 0, screens: [] },
    });
    renderPage();
    await waitFor(() => {
      expect(screen.getByText(/No screens configured yet/i)).toBeInTheDocument();
    });
  });

  it('renders screen status rows with online/offline badges', async () => {
    (api.screensAPI.getStatus as jest.Mock).mockResolvedValue({
      data: {
        count: 2,
        screens: [
          {
            id: 'a',
            slug: 'lobby',
            name: 'Lobby Display',
            is_active: true,
            is_online: true,
            last_heartbeat_at: '2026-04-21T00:00:00Z',
            sig_name: 'Front Desk',
            location_name: 'Lobby',
            updated_at: '2026-04-21T00:00:00Z',
          },
          {
            id: 'b',
            slug: 'shop',
            name: 'Shop Display',
            is_active: false,
            is_online: false,
            last_heartbeat_at: null,
            sig_name: '',
            location_name: '',
            updated_at: '2026-04-21T00:00:00Z',
          },
        ],
      },
    });
    renderPage();
    await waitFor(() => {
      expect(screen.getByText('Lobby Display')).toBeInTheDocument();
    });
    expect(screen.getByText('Shop Display')).toBeInTheDocument();
    expect(screen.getByText('Online')).toBeInTheDocument();
    expect(screen.getByText('Offline')).toBeInTheDocument();
    expect(screen.getByText('Disabled')).toBeInTheDocument();
  });

  it('surfaces an error message when the API fails', async () => {
    (api.screensAPI.getStatus as jest.Mock).mockRejectedValue(new Error('nope'));
    renderPage();
    await waitFor(() => {
      expect(screen.getByText(/Unable to load screens/i)).toBeInTheDocument();
    });
  });
});
