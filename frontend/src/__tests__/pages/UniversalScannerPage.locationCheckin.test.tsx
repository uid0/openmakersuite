/**
 * Universal Scanner (`/scan`) — a location QR scan records a check-in OMS serves.
 *
 * The page used to POST `/location-checkins/check-ins/`, a path OMS never
 * routed: every location scan 404'd and the history line said the scan failed.
 * The route below is read from the backend's own URLconf rather than typed a
 * second time here, so renaming it on either side fails this test.
 */
import { readFileSync } from 'node:fs';
import path from 'node:path';

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

const BACKEND_DIR = path.resolve(__dirname, '../../../../backend');

const match = (file: string, pattern: RegExp): string => {
  const found = readFileSync(path.join(BACKEND_DIR, file), 'utf8').match(pattern);
  if (!found) throw new Error(`${pattern} not found in backend/${file}`);
  return found[1];
};

/**
 * `POST <api prefix>/<router prefix>/<action url_path>/` for the public
 * `LocationCheckInViewSet.checkin` action, relative to the axios baseURL
 * (`/api`), so it is exactly what the web client must call.
 */
const checkinRoute = () => {
  const appPrefix = match(
    'config/urls.py',
    /path\("api\/([^"]+)",\s*include\("location_checkins\.urls"\)\)/,
  );
  const routerPrefix = match(
    'location_checkins/urls.py',
    /router\.register\(r"([^"]+)",\s*LocationCheckInViewSet\b/,
  );
  const urlPath = match('location_checkins/views.py', /url_path="([^"]+)",?\s*\)\s*def checkin\(/);
  return `/${appPrefix}${routerPrefix}/${urlPath}/`;
};

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

  test('derives the check-in route from the backend URLconf', () => {
    expect(checkinRoute()).toBe('/location-checkins/checkins/checkin/');
  });

  test('posts the scanned location to the routed check-in action', async () => {
    const route = checkinRoute();
    const posted: unknown[] = [];
    mock.onPost('/scanner/dispatch/').reply(200, {
      action: 'location_checkin',
      target_type: 'location',
      target_id: '11',
      target_name: 'Wood Shop',
      raw_payload: 'LOC-11',
    });
    mock.onPost(route).reply((config) => {
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
