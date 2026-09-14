/**
 * Universal Scanner (`/scan`) — a location QR scan records a check-in OMS serves.
 *
 * The page used to POST `/location-checkins/check-ins/`, a path OMS never
 * routed: every location scan 404'd and the history line said the scan failed.
 * CHECKIN_ROUTE is the wire contract OMS pins from its side in
 * `backend/location_checkins/tests/test_checkin_route.py`; every other path
 * answers 404 here, as it does on the server.
 */
import { MantineProvider } from '@mantine/core';
import { ModalsProvider } from '@mantine/modals';
import { Notifications, cleanNotifications } from '@mantine/notifications';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import MockAdapter from 'axios-mock-adapter';
import { MemoryRouter } from 'react-router-dom';

import UniversalScannerPage from '../../pages/UniversalScannerPage';
import api from '../../services/api';

vi.mock('react-router-dom', async () => ({
  ...(await vi.importActual('react-router-dom')),
  useNavigate: () => jest.fn(),
}));

/** Relative to the axios baseURL (`/api`). */
const CHECKIN_ROUTE = '/location-checkins/checkins/checkin/';

const renderPage = () =>
  render(
    <MantineProvider>
      <ModalsProvider>
        <Notifications />
        <MemoryRouter>
          <UniversalScannerPage />
        </MemoryRouter>
      </ModalsProvider>
    </MantineProvider>,
  );

describe('UniversalScannerPage — location check-in scan', () => {
  let mock: MockAdapter;

  beforeEach(() => {
    cleanNotifications();
    mock = new MockAdapter(api);
  });

  afterEach(() => {
    mock.restore();
  });

  test('posts the scanned location to the routed check-in action', async () => {
    const posted: unknown[] = [];
    mock.onPost('/scanner/dispatch/').reply(200, {
      action: 'location_checkin',
      target_type: 'location',
      target_id: '11',
      target_name: 'Wood Shop',
      raw_payload: 'LOC-11',
    });
    mock.onPost(CHECKIN_ROUTE).reply((config) => {
      posted.push(JSON.parse(config.data));
      return [201, { id: 'c-1', location: 11, checkin_type: 'anonymous' }];
    });
    // Anything else is a path OMS does not route.
    mock.onAny().reply(404, { detail: 'Not found.' });

    renderPage();
    const input = await screen.findByTestId('universal-scanner-input');
    fireEvent.change(input, { target: { value: 'LOC-11' } });
    fireEvent.keyDown(input, { key: 'Enter' });

    const history = await screen.findByTestId('universal-scanner-history');
    await waitFor(() => expect(history).toHaveTextContent('Location check-in recorded.'));
    expect(posted).toEqual([{ location_id: '11', checkin_type: 'anonymous' }]);
  });
});
