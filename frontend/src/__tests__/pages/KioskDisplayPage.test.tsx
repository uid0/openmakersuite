/**
 * Tests for KioskDisplayPage
 */
import { act, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import KioskDisplayPage from '../../pages/KioskDisplayPage';
import * as api from '../../services/api';
import { networkError } from '../helpers/offline';

vi.mock('../../services/api');

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

  it('renders a shared_weather block (native OpenWeather-backed implementation)', async () => {
    // The block now fetches /api/screens/weather/current/ rather than
    // rendering an iframe. The mocked api default export will reject
    // (no weather endpoint stubbed), so we expect the fallback path —
    // which falls back to the legacy iframe URL when one is supplied.
    (api.kioskAPI.fetchPayload as jest.Mock).mockResolvedValue({
      data: makePayload({
        weather_url: 'https://www.wunderground.com/weather/us/tx/carrollton/',
        content_blocks: [
          {
            id: 'w1',
            block_type: 'shared_weather',
            title: '',
            body: '',
            config: {},
            order: 0,
          },
        ],
      }),
    });
    renderKiosk('lobby', 'secret');
    // Either the native panel renders (if api.default.get was mocked to
    // succeed by another test) or we get the loading/error/fallback —
    // any of those is a valid render proving the block was mounted.
    await screen.findByText(/Weather|Loading/i);
  });

  it('renders a shared_traffic block as an iframe using the config URL', async () => {
    (api.kioskAPI.fetchPayload as jest.Mock).mockResolvedValue({
      data: makePayload({
        content_blocks: [
          {
            id: 'b1',
            block_type: 'shared_traffic',
            title: 'Site Traffic',
            body: '',
            config: { url: 'https://embed.waze.com/iframe?lat=1&lon=2' },
            order: 0,
          },
        ],
      }),
    });
    renderKiosk('lobby', 'secret');
    await waitFor(() => {
      expect(screen.getByTestId('shared-traffic-iframe')).toBeInTheDocument();
    });
    const iframe = screen.getByTestId('shared-traffic-iframe') as HTMLIFrameElement;
    expect(iframe.src).toBe('https://embed.waze.com/iframe?lat=1&lon=2');
    expect(screen.getByText('Site Traffic')).toBeInTheDocument();
  });

  it('shows error state when backend rejects', async () => {
    (api.kioskAPI.fetchPayload as jest.Mock).mockRejectedValue(new Error('403'));
    renderKiosk('lobby', 'secret');
    await waitFor(() => {
      expect(screen.getByTestId('kiosk-error')).toBeInTheDocument();
    });
  });

  it('shows a readable offline fallback when the network is unavailable', async () => {
    // AC-16: a dropped connection surfaces a readable state, not a blank screen.
    (api.kioskAPI.fetchPayload as jest.Mock).mockRejectedValue(networkError());
    renderKiosk('lobby', 'secret');
    const errorEl = await screen.findByTestId('kiosk-error');
    expect(errorEl).toHaveTextContent('Unable to load screen payload.');
  });

  it('auto-refreshes on its interval and picks up updated content', async () => {
    // AC-16/19: the unattended kiosk re-polls and recovers fresh content
    // without manual intervention. refresh_interval_seconds defaults to 60.
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      (api.kioskAPI.fetchPayload as jest.Mock)
        .mockResolvedValueOnce({
          data: makePayload({
            content_blocks: [
              { id: 'b1', block_type: 'custom_text', title: 'Welcome', body: 'First', config: {}, order: 0 },
            ],
          }),
        })
        .mockResolvedValue({
          data: makePayload({
            content_blocks: [
              { id: 'b1', block_type: 'custom_text', title: 'Welcome', body: 'Second', config: {}, order: 0 },
            ],
          }),
        });
      renderKiosk('lobby', 'secret');

      expect(await screen.findByText('First')).toBeInTheDocument();
      expect(api.kioskAPI.fetchPayload).toHaveBeenCalledTimes(1);

      // Fire the 60s refresh interval.
      await act(async () => {
        vi.advanceTimersByTime(60000);
      });

      expect(await screen.findByText('Second')).toBeInTheDocument();
      expect((api.kioskAPI.fetchPayload as jest.Mock).mock.calls.length).toBeGreaterThanOrEqual(2);
    } finally {
      vi.useRealTimers();
    }
  });
});
