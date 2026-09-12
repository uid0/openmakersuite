/**
 * Universal Scanner (`/scan`) — what the reorder scan REPORTS.
 *
 * The page is kiosk-mode anonymous, so the server's "one pending anonymous
 * request per item" rule fires here as surely as it does on the QR scan page.
 * A resolved POST is therefore no longer proof a row was created: the endpoint
 * answers 200 with `already_requested: true` when it named a pending request
 * instead of filing a new one.
 *
 * The scan history line is the only record the operator gets, so it must say
 * which of the two happened — "created" for a request that was not created is
 * a false message, and being told nothing was filed is what makes an operator
 * scan again.
 */
import { MantineProvider } from '@mantine/core';
import { ModalsProvider } from '@mantine/modals';
import { Notifications, cleanNotifications } from '@mantine/notifications';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import UniversalScannerPage from '../../pages/UniversalScannerPage';
import * as api from '../../services/api';

vi.mock('../../services/api');

vi.mock('react-router-dom', async () => ({
  ...(await vi.importActual('react-router-dom')),
  useNavigate: () => jest.fn(),
}));

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

const scan = async (payload: string) => {
  const input = await screen.findByTestId('universal-scanner-input');
  fireEvent.change(input, { target: { value: payload } });
  fireEvent.keyDown(input, { key: 'Enter' });
};

describe('UniversalScannerPage — reorder scan outcome', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    // The Mantine notification store is module-global; left dirty it leaks
    // between cases in the same file.
    cleanNotifications();
    (api.scannerAPI.dispatch as jest.Mock).mockResolvedValue({
      data: {
        action: 'inventory_reorder',
        target_id: 'item-1',
        target_name: 'Blue nitrile gloves',
        raw_payload: 'GLOVE-M',
      },
    });
  });

  test('a filed reorder is reported as created', async () => {
    (api.reorderAPI.createRequest as jest.Mock).mockResolvedValue({
      data: { id: 7, already_requested: false },
    });

    renderPage();
    await scan('GLOVE-M');

    await waitFor(() =>
      expect(screen.getByTestId('universal-scanner-history')).toHaveTextContent(
        /reorder request created/i,
      ),
    );
  });

  test('a duplicate is reported as already requested, never as created', async () => {
    (api.reorderAPI.createRequest as jest.Mock).mockResolvedValue({
      data: { id: 7, already_requested: true, detail: 'already recorded' },
    });

    renderPage();
    await scan('GLOVE-M');

    const history = await screen.findByTestId('universal-scanner-history');
    await waitFor(() => expect(history).toHaveTextContent(/already requested/i));
    // The claim that must not survive: nothing was created.
    expect(history).not.toHaveTextContent(/reorder request created/i);
    // And it stays a SUCCESS — the need is on file, so nothing here should
    // read as a scan that failed.
    expect(history).not.toHaveTextContent(/failed|could not/i);
  });
});
